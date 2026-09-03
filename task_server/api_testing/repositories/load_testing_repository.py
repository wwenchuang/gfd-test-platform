"""Transaction-scoped persistence for API performance testing."""

import copy
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.load_testing import (
    ApiLoadAgent,
    ApiLoadEvent,
    ApiLoadMetricBucket,
    ApiLoadRun,
    ApiLoadRunShard,
    ApiLoadSample,
    ApiLoadScenario,
    ApiLoadScenarioVersion,
)


RUN_TRANSITIONS = {
    "draft": {"preflighting", "cancelled", "failed"},
    "preflighting": {"queued", "cancelled", "failed"},
    "queued": {"starting", "cancelled", "failed"},
    "starting": {"running", "stopping", "cancelled", "failed"},
    "running": {"stopping", "finished", "cancelled", "failed"},
    "stopping": {"finished", "cancelled", "failed"},
    "finished": set(),
    "cancelled": set(),
    "failed": set(),
}


class LoadTestingRecordNotFound(LookupError):
    """Raised when a load-testing parent record does not exist."""


class InvalidLoadRunTransition(ValueError):
    """Raised when a run attempts an invalid or stale state transition."""


def _audit(actor_id):
    return {"owner_id": actor_id, "created_by": actor_id, "updated_by": actor_id}


def _content_hash(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LoadTestingRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @classmethod
    def from_factory(cls, session_factory):
        return cls(session_factory)

    def create_scenario(self, project_id, name, scenario_type, actor_id):
        with self.session_factory.begin() as session:
            record = ApiLoadScenario(
                project_id=project_id,
                name=name,
                scenario_type=scenario_type,
                **_audit(actor_id),
            )
            session.add(record)
            session.flush()
            return record

    def create_scenario_version(self, scenario_id, definition, compiler_version, actor_id):
        with self.session_factory.begin() as session:
            scenario = session.scalar(
                select(ApiLoadScenario)
                .where(ApiLoadScenario.id == scenario_id)
                .with_for_update()
            )
            if scenario is None:
                raise LoadTestingRecordNotFound("load scenario does not exist")
            next_number = (
                session.scalar(
                    select(func.max(ApiLoadScenarioVersion.version_number)).where(
                        ApiLoadScenarioVersion.scenario_id == scenario_id
                    )
                )
                or 0
            ) + 1
            snapshot = copy.deepcopy(definition)
            record = ApiLoadScenarioVersion(
                scenario_id=scenario_id,
                version_number=next_number,
                definition=snapshot,
                compiler_version=compiler_version,
                content_hash=_content_hash(snapshot),
                **_audit(actor_id),
            )
            session.add(record)
            session.flush()
            scenario.active_version_id = record.id
            scenario.updated_by = actor_id
            session.flush()
            return record

    def create_run(self, scenario_version_id, environment_revision_id, configuration, actor_id):
        with self.session_factory.begin() as session:
            version = session.get(ApiLoadScenarioVersion, scenario_version_id)
            environment_revision = session.get(ApiEnvironmentRevision, environment_revision_id)
            if version is None or environment_revision is None:
                raise LoadTestingRecordNotFound("scenario version or environment revision does not exist")
            scenario = session.get(ApiLoadScenario, version.scenario_id)
            environment = session.get(ApiEnvironment, environment_revision.environment_id)
            if scenario is None or environment is None or scenario.project_id != environment.project_id:
                raise ValueError("scenario and environment must belong to the same project")
            snapshot = copy.deepcopy(configuration)
            record = ApiLoadRun(
                project_id=scenario.project_id,
                scenario_version_id=version.id,
                environment_revision_id=environment_revision.id,
                load_model=str(snapshot.get("executor") or ""),
                queue_priority=str(snapshot.get("priority") or "normal"),
                configuration=snapshot,
                **_audit(actor_id),
            )
            session.add(record)
            session.flush()
            return record

    def create_shard(self, run_id, agent_id, sequence, allocation, actor_id):
        with self.session_factory.begin() as session:
            run = session.get(ApiLoadRun, run_id)
            agent = session.get(ApiLoadAgent, agent_id)
            if run is None or agent is None:
                raise LoadTestingRecordNotFound("load run or Agent does not exist")
            record = ApiLoadRunShard(
                run_id=run.id,
                agent_id=agent.id,
                sequence=sequence,
                global_sequence=sequence,
                allocation=copy.deepcopy(allocation),
                **_audit(actor_id),
            )
            session.add(record)
            session.flush()
            return record

    def transition_run(self, run_id, expected, target, *, summary=None):
        with self.session_factory.begin() as session:
            run = session.scalar(
                select(ApiLoadRun).where(ApiLoadRun.id == run_id).with_for_update()
            )
            if run is None:
                raise LoadTestingRecordNotFound("load run does not exist")
            if run.state not in expected:
                raise InvalidLoadRunTransition(
                    f"expected run state {expected!r}, got {run.state!r}"
                )
            allowed = RUN_TRANSITIONS.get(run.state, set())
            if target not in allowed:
                raise InvalidLoadRunTransition(
                    f"run cannot transition from {run.state!r} to {target!r}"
                )
            run.state = target
            if summary is not None:
                run.summary = copy.deepcopy(summary)
            now = datetime.now(timezone.utc)
            if target == "running" and run.started_at is None:
                run.started_at = now
            if target in {"finished", "cancelled", "failed"}:
                run.finished_at = now
            session.flush()
            return run

    def upsert_metric_bucket(self, run_id, shard_id, step_id, started_at, metrics):
        with self.session_factory.begin() as session:
            run = session.get(ApiLoadRun, run_id)
            shard = session.get(ApiLoadRunShard, shard_id)
            if run is None or shard is None or shard.run_id != run.id:
                raise LoadTestingRecordNotFound("load run shard does not exist")
            values = {
                "run_id": run.id,
                "shard_id": shard.id,
                "scenario_step_id": step_id,
                "bucket_started_at": started_at,
                "metrics": copy.deepcopy(metrics),
                **_audit(run.owner_id),
            }
            statement = insert(ApiLoadMetricBucket).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=(
                    ApiLoadMetricBucket.run_id,
                    ApiLoadMetricBucket.shard_id,
                    ApiLoadMetricBucket.scenario_step_id,
                    ApiLoadMetricBucket.bucket_started_at,
                ),
                set_={
                    "metrics": copy.deepcopy(metrics),
                    "updated_by": run.owner_id,
                    "updated_at": func.now(),
                },
            ).returning(ApiLoadMetricBucket)
            return session.scalars(statement).one()

    def append_event(self, run_id, event_type, payload):
        with self.session_factory.begin() as session:
            run = session.scalar(
                select(ApiLoadRun).where(ApiLoadRun.id == run_id).with_for_update()
            )
            if run is None:
                raise LoadTestingRecordNotFound("load run does not exist")
            sequence = (
                session.scalar(select(func.max(ApiLoadEvent.sequence)).where(ApiLoadEvent.run_id == run.id))
                or 0
            ) + 1
            event = ApiLoadEvent(
                run_id=run.id,
                sequence=sequence,
                event_type=event_type,
                payload=copy.deepcopy(payload),
                **_audit(run.owner_id),
            )
            session.add(event)
            session.flush()
            return event

    def append_bounded_sample(self, run_id, shard_id, step_id, kind, payload, limit=20):
        if limit < 1:
            raise ValueError("sample limit must be positive")
        with self.session_factory.begin() as session:
            run = session.scalar(
                select(ApiLoadRun).where(ApiLoadRun.id == run_id).with_for_update()
            )
            shard = session.get(ApiLoadRunShard, shard_id)
            if run is None or shard is None or shard.run_id != run.id:
                raise LoadTestingRecordNotFound("load run shard does not exist")
            query = (
                select(ApiLoadSample)
                .where(
                    ApiLoadSample.run_id == run.id,
                    ApiLoadSample.shard_id == shard.id,
                    ApiLoadSample.scenario_step_id == step_id,
                    ApiLoadSample.kind == kind,
                )
                .order_by(ApiLoadSample.created_at, ApiLoadSample.id)
            )
            samples = tuple(session.scalars(query))
            if len(samples) >= limit:
                sample = samples[-1]
                sample.occurrence_count += 1
                session.flush()
                return sample
            sample_payload = copy.deepcopy(payload)
            sample = ApiLoadSample(
                run_id=run.id,
                shard_id=shard.id,
                scenario_step_id=step_id,
                kind=kind,
                elapsed_ms=sample_payload.get("elapsed_ms"),
                status_code=sample_payload.get("status_code"),
                business_code=(
                    ""
                    if sample_payload.get("business_code") is None
                    else str(sample_payload["business_code"])
                ),
                payload=sample_payload,
                **_audit(run.owner_id),
            )
            session.add(sample)
            session.flush()
            return sample
