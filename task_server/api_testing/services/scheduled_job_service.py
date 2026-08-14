"""Scheduled API regression job management and manual dispatch."""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from ..models.case import ApiBaseline, ApiCaseVersion
from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.project import ApiProject
from ..models.scheduled_job import ApiScheduledJob, ApiScheduledJobRun, ApiScheduledJobTarget
from ..models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from ..models.test_task import ApiTestTask
from ..repositories.source_repository import audit_fields
from .execution_service import ExecutionService


class ScheduledJobInputError(ValueError):
    pass


class ScheduledJobNotFoundError(LookupError):
    pass


TARGET_TYPES = frozenset({"cases", "task", "baselines", "baseline_group"})
SCHEDULE_TYPES = frozenset({"daily", "weekly", "cron"})
ENVIRONMENT_STRATEGIES = frozenset({"fixed_revision", "latest_environment"})


@dataclass(frozen=True)
class ScheduledJobView:
    id: str
    project_id: str
    source_revision_id: Optional[str]
    environment_revision_id: Optional[str]
    environment_id: Optional[str]
    name: str
    target_type: str
    target_ids: tuple
    schedule_type: str
    cron_expression: str
    environment_strategy: str
    enabled: bool
    notify_feishu: bool
    retry_count: int
    timeout_seconds: int
    latest_execution_id: Optional[str]
    created_at: object
    updated_at: object


def _text(value, field, maximum=200, *, allow_empty=False):
    if not isinstance(value, str):
        raise ScheduledJobInputError(f"{field} is invalid")
    text = value.strip()
    if (not text and not allow_empty) or len(text) > maximum:
        raise ScheduledJobInputError(f"{field} is invalid")
    return text


def _bool(value, field):
    if not isinstance(value, bool):
        raise ScheduledJobInputError(f"{field} must be a boolean")
    return value


def _int(value, field, minimum, maximum):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ScheduledJobInputError(f"{field} is invalid")
    return value


def _ids(value, field):
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 500
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ScheduledJobInputError(f"{field} must be a non-empty string array")
    return tuple(dict.fromkeys(item.strip() for item in value))


class ScheduledJobService:
    def __init__(self, session_factory, *, enqueue=None, event_stream=None):
        self.session_factory = session_factory
        self.enqueue = enqueue
        self.event_stream = event_stream

    def create(self, payload, actor_id):
        parsed = self._parse(payload)
        with self.session_factory.begin() as session:
            self._validate_project(session, parsed["project_id"], actor_id)
            environment_revision_id, environment_id = self._resolve_environment(session, parsed)
            source_revision_id = self._resolve_source_revision(session, parsed, actor_id)
            record = ApiScheduledJob(
                project_id=parsed["project_id"],
                source_revision_id=source_revision_id,
                environment_revision_id=environment_revision_id,
                environment_id=environment_id,
                name=parsed["name"],
                target_type=parsed["target_type"],
                schedule_type=parsed["schedule_type"],
                cron_expression=parsed["cron_expression"],
                environment_strategy=parsed["environment_strategy"],
                enabled=parsed["enabled"],
                notify_feishu=parsed["notify_feishu"],
                retry_count=parsed["retry_count"],
                timeout_seconds=parsed["timeout_seconds"],
                summary="",
                **audit_fields(actor_id),
            )
            session.add(record)
            session.flush()
            for index, target_id in enumerate(parsed["target_ids"]):
                session.add(ApiScheduledJobTarget(
                    job_id=record.id,
                    sequence=index,
                    target_type=parsed["target_type"],
                    target_id=target_id,
                    group_name=target_id if parsed["target_type"] == "baseline_group" else "",
                    **audit_fields(actor_id),
                ))
            session.flush()
            return self._view(session, record)

    def list(self, project_id, actor_id):
        with self.session_factory() as session:
            self._validate_project(session, project_id, actor_id)
            return tuple(
                self._view(session, record)
                for record in session.scalars(
                    select(ApiScheduledJob)
                    .where(ApiScheduledJob.project_id == project_id, ApiScheduledJob.owner_id == actor_id)
                    .order_by(ApiScheduledJob.updated_at.desc(), ApiScheduledJob.id.desc())
                )
            )

    def get(self, job_id, actor_id):
        with self.session_factory() as session:
            record = self._owned_job(session, job_id, actor_id)
            return self._view(session, record)

    def run_once(self, job_id, actor_id, *, idempotency_key):
        with self.session_factory.begin() as session:
            job = self._owned_job(session, job_id, actor_id, for_update=True)
            environment_revision_id = self._runtime_environment_revision_id(session, job)
            source_revision_id, case_version_ids, baseline_ids = self._runtime_targets(session, job, actor_id)
            run = ApiScheduledJobRun(
                job_id=job.id,
                execution_id=None,
                trigger_type="manual",
                state="queued",
                **audit_fields(actor_id),
            )
            session.add(run)
            session.flush()
            task = {
                "id": job.id,
                "name": job.name,
                "type": "scheduled_job",
                "source": "scheduled_job",
            }

        service = ExecutionService(
            self.session_factory,
            event_stream=self.event_stream,
        )
        if job.target_type in {"baselines", "baseline_group", "task"} and baseline_ids is not None:
            execution = service.submit_active_baselines(
                {
                    "project_id": job.project_id,
                    "source_revision_id": source_revision_id,
                    "environment_revision_id": environment_revision_id,
                    "baseline_ids": list(baseline_ids),
                },
                actor_id,
                idempotency_key,
                task=task,
            )
        else:
            execution = service.submit(
                {
                    "project_id": job.project_id,
                    "source_revision_id": source_revision_id,
                    "environment_revision_id": environment_revision_id,
                    "case_version_ids": list(case_version_ids),
                    "execution_type": "scheduled",
                    "overrides": {},
                },
                actor_id,
                idempotency_key,
                task=task,
            )
        with self.session_factory.begin() as session:
            persisted_run = session.get(ApiScheduledJobRun, run.id)
            if persisted_run is not None:
                persisted_run.execution_id = execution.id
                persisted_run.state = "queued"
                persisted_run.updated_by = actor_id
            session.flush()
        if self.enqueue is not None:
            self.enqueue(execution.id)
        return execution

    @staticmethod
    def _parse(payload):
        if not isinstance(payload, dict):
            raise ScheduledJobInputError("scheduled job payload must be an object")
        target_type = _text(payload.get("target_type"), "target_type", 32)
        schedule_type = _text(payload.get("schedule_type"), "schedule_type", 32)
        environment_strategy = _text(payload.get("environment_strategy"), "environment_strategy", 32)
        if target_type not in TARGET_TYPES:
            raise ScheduledJobInputError("target_type is not supported")
        if schedule_type not in SCHEDULE_TYPES:
            raise ScheduledJobInputError("schedule_type is not supported")
        if environment_strategy not in ENVIRONMENT_STRATEGIES:
            raise ScheduledJobInputError("environment_strategy is not supported")
        cron_expression = _text(payload.get("cron_expression", ""), "cron_expression", 120, allow_empty=True)
        if schedule_type == "cron" and not cron_expression:
            raise ScheduledJobInputError("cron_expression is required for cron jobs")
        return {
            "project_id": _text(payload.get("project_id"), "project_id", 36),
            "source_revision_id": _text(payload.get("source_revision_id", ""), "source_revision_id", 36, allow_empty=True) or None,
            "environment_revision_id": _text(payload.get("environment_revision_id", ""), "environment_revision_id", 36, allow_empty=True) or None,
            "environment_id": _text(payload.get("environment_id", ""), "environment_id", 36, allow_empty=True) or None,
            "name": _text(payload.get("name"), "name", 200),
            "target_type": target_type,
            "target_ids": _ids(payload.get("target_ids"), "target_ids"),
            "schedule_type": schedule_type,
            "cron_expression": cron_expression,
            "environment_strategy": environment_strategy,
            "enabled": _bool(payload.get("enabled", True), "enabled"),
            "notify_feishu": _bool(payload.get("notify_feishu", False), "notify_feishu"),
            "retry_count": _int(payload.get("retry_count", 0), "retry_count", 0, 5),
            "timeout_seconds": _int(payload.get("timeout_seconds", 1800), "timeout_seconds", 30, 86_400),
        }

    @staticmethod
    def _validate_project(session, project_id, actor_id):
        project = session.get(ApiProject, project_id)
        if project is None or project.owner_id != actor_id:
            raise ScheduledJobNotFoundError("API testing project was not found")
        return project

    @staticmethod
    def _resolve_environment(session, parsed):
        if parsed["environment_strategy"] == "fixed_revision":
            revision = session.get(ApiEnvironmentRevision, parsed["environment_revision_id"])
            environment = session.get(ApiEnvironment, revision.environment_id) if revision else None
            if environment is None or environment.project_id != parsed["project_id"]:
                raise ScheduledJobInputError("environment revision is outside this project")
            return revision.id, environment.id
        environment = session.get(ApiEnvironment, parsed["environment_id"])
        if environment is None or environment.project_id != parsed["project_id"]:
            raise ScheduledJobInputError("environment is outside this project")
        return None, environment.id

    def _resolve_source_revision(self, session, parsed, actor_id):
        if parsed["source_revision_id"]:
            revision = session.get(ApiSourceRevision, parsed["source_revision_id"])
            source = session.get(ApiSource, revision.source_id) if revision else None
            if source is None or source.project_id != parsed["project_id"]:
                raise ScheduledJobInputError("source revision is outside this project")
            return revision.id
        source_revision_id, _, _ = self._target_context(session, parsed["project_id"], parsed["target_type"], parsed["target_ids"], actor_id)
        return source_revision_id

    def _runtime_environment_revision_id(self, session, job):
        if job.environment_strategy == "fixed_revision":
            if not job.environment_revision_id:
                raise ScheduledJobInputError("scheduled job has no fixed environment revision")
            return job.environment_revision_id
        environment = session.get(ApiEnvironment, job.environment_id)
        if environment is None or not environment.active_revision_id:
            raise ScheduledJobInputError("scheduled job environment has no active revision")
        return environment.active_revision_id

    def _runtime_targets(self, session, job, actor_id):
        target_ids = tuple(target.target_id for target in self._targets(session, job.id))
        if not target_ids:
            raise ScheduledJobInputError("scheduled job has no targets")
        source_revision_id, case_version_ids, baseline_ids = self._target_context(
            session,
            job.project_id,
            job.target_type,
            target_ids,
            actor_id,
        )
        return job.source_revision_id or source_revision_id, case_version_ids, baseline_ids

    def _target_context(self, session, project_id, target_type, target_ids, actor_id):
        if target_type == "cases":
            versions = self._case_versions(session, target_ids, project_id)
            source_revision_ids = self._case_source_revision_ids(session, versions)
            if len(source_revision_ids) != 1:
                raise ScheduledJobInputError("scheduled cases must share one source revision")
            return next(iter(source_revision_ids)), tuple(version.id for version in versions), None
        if target_type == "baselines":
            baselines = self._baselines(session, target_ids, project_id, actor_id)
            return self._baseline_source_revision_id(session, baselines, project_id), (), tuple(item.id for item in baselines)
        if target_type == "baseline_group":
            baselines = tuple(session.scalars(
                select(ApiBaseline)
                .where(
                    ApiBaseline.project_id == project_id,
                    ApiBaseline.owner_id == actor_id,
                    ApiBaseline.status != "archived",
                    ApiBaseline.group_name.in_(target_ids),
                )
                .order_by(ApiBaseline.created_at, ApiBaseline.id)
            ))
            if not baselines:
                raise ScheduledJobInputError("baseline group has no active baselines")
            return self._baseline_source_revision_id(session, baselines, project_id), (), tuple(item.id for item in baselines)
        if target_type == "task":
            if len(target_ids) != 1:
                raise ScheduledJobInputError("scheduled task target must contain exactly one task")
            task = session.get(ApiTestTask, target_ids[0])
            if task is None or task.owner_id != actor_id or task.project_id != project_id:
                raise ScheduledJobInputError("scheduled task is outside this project")
            baselines = tuple(session.scalars(
                select(ApiBaseline)
                .join(ApiCaseVersion, ApiCaseVersion.id == ApiBaseline.case_version_id)
                .where(
                    ApiBaseline.project_id == project_id,
                    ApiBaseline.owner_id == actor_id,
                    ApiBaseline.status != "archived",
                    ApiCaseVersion.endpoint_id.in_(tuple(task.selected_endpoint_ids or ())),
                )
                .order_by(ApiBaseline.created_at, ApiBaseline.id)
            ))
            if not baselines:
                raise ScheduledJobInputError("scheduled task has no active baselines")
            return task.source_revision_id, (), tuple(item.id for item in baselines)
        raise ScheduledJobInputError("target_type is not supported")

    @staticmethod
    def _case_versions(session, target_ids, project_id):
        versions = tuple(session.scalars(
            select(ApiCaseVersion)
            .where(ApiCaseVersion.id.in_(tuple(target_ids)))
            .order_by(ApiCaseVersion.created_at, ApiCaseVersion.id)
        ))
        if len(versions) != len(target_ids):
            raise ScheduledJobInputError("scheduled case version was not found")
        endpoints = {
            item.id: item
            for item in session.scalars(
                select(ApiSourceEndpoint).where(ApiSourceEndpoint.id.in_(tuple(version.endpoint_id for version in versions)))
            )
        }
        for version in versions:
            endpoint = endpoints.get(version.endpoint_id)
            if endpoint is None:
                raise ScheduledJobInputError("scheduled case endpoint was not found")
            revision = session.get(ApiSourceRevision, endpoint.revision_id)
            source = session.get(ApiSource, revision.source_id) if revision else None
            if source is None or source.project_id != project_id:
                raise ScheduledJobInputError("scheduled case is outside this project")
        return versions

    @staticmethod
    def _case_source_revision_ids(session, versions):
        endpoint_ids = tuple(version.endpoint_id for version in versions)
        return {
            item.revision_id
            for item in session.scalars(select(ApiSourceEndpoint).where(ApiSourceEndpoint.id.in_(endpoint_ids)))
        }

    @staticmethod
    def _baselines(session, target_ids, project_id, actor_id):
        baselines = tuple(session.scalars(
            select(ApiBaseline)
            .where(
                ApiBaseline.id.in_(tuple(target_ids)),
                ApiBaseline.project_id == project_id,
                ApiBaseline.owner_id == actor_id,
                ApiBaseline.status != "archived",
            )
            .order_by(ApiBaseline.created_at, ApiBaseline.id)
        ))
        if len(baselines) != len(target_ids):
            raise ScheduledJobInputError("scheduled baseline was not found")
        return baselines

    @staticmethod
    def _baseline_source_revision_id(session, baselines, project_id):
        versions = tuple(session.scalars(
            select(ApiCaseVersion).where(ApiCaseVersion.id.in_(tuple(item.case_version_id for item in baselines)))
        ))
        source_ids = ScheduledJobService._case_source_revision_ids(session, versions)
        if source_ids:
            return sorted(source_ids)[0]
        source_revision = session.scalar(
            select(ApiSourceRevision)
            .join(ApiSource, ApiSourceRevision.source_id == ApiSource.id)
            .where(ApiSource.project_id == project_id, ApiSource.active_revision_id == ApiSourceRevision.id)
            .order_by(ApiSourceRevision.activated_at.desc(), ApiSourceRevision.created_at.desc())
        )
        if source_revision is None:
            raise ScheduledJobInputError("project has no active source revision")
        return source_revision.id

    @staticmethod
    def _owned_job(session, job_id, actor_id, *, for_update=False):
        query = select(ApiScheduledJob).where(ApiScheduledJob.id == job_id)
        if for_update:
            query = query.with_for_update()
        job = session.scalar(query)
        if job is None or job.owner_id != actor_id:
            raise ScheduledJobNotFoundError("API scheduled job was not found")
        return job

    @staticmethod
    def _targets(session, job_id):
        return tuple(session.scalars(
            select(ApiScheduledJobTarget)
            .where(ApiScheduledJobTarget.job_id == job_id)
            .order_by(ApiScheduledJobTarget.sequence)
        ))

    @classmethod
    def _view(cls, session, job):
        targets = cls._targets(session, job.id)
        latest_run = session.scalar(
            select(ApiScheduledJobRun)
            .where(ApiScheduledJobRun.job_id == job.id)
            .order_by(ApiScheduledJobRun.created_at.desc(), ApiScheduledJobRun.id.desc())
            .limit(1)
        )
        return ScheduledJobView(
            id=job.id,
            project_id=job.project_id,
            source_revision_id=job.source_revision_id,
            environment_revision_id=job.environment_revision_id,
            environment_id=job.environment_id,
            name=job.name,
            target_type=job.target_type,
            target_ids=tuple(target.target_id for target in targets),
            schedule_type=job.schedule_type,
            cron_expression=job.cron_expression,
            environment_strategy=job.environment_strategy,
            enabled=job.enabled,
            notify_feishu=job.notify_feishu,
            retry_count=job.retry_count,
            timeout_seconds=job.timeout_seconds,
            latest_execution_id=latest_run.execution_id if latest_run else None,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
