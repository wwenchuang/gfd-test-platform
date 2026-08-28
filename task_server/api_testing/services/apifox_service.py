"""Orchestrate saved Apifox credentials, discovery, preview, and activation."""

import copy
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy import select

from ..models.project import ApiWorkspace
from ..models.source import ApiSource, ApiSourceDiff
from ..repositories.source_repository import audit_fields


class ApifoxInputError(ValueError):
    pass


@dataclass(frozen=True)
class ApifoxRefreshPreviewView:
    source_preview: object
    environment_candidate: object

    def __post_init__(self):
        object.__setattr__(
            self,
            "environment_candidate",
            MappingProxyType(dict(self.environment_candidate)),
        )


@dataclass(frozen=True)
class ApifoxActivationView:
    source_revision: object
    environment: object
    workspace: object
    secret_placeholders: tuple


def _text(value, label, allow_empty=False):
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise ApifoxInputError("%s必须是字符串" % label)
    text = value.strip()
    if not text and not allow_empty:
        raise ApifoxInputError("%s不能为空" % label)
    return text


def build_environment_candidate(
    project_id, source_id, source_revision_id, environment
):
    if environment is None:
        raise ApifoxInputError("请选择 Apifox 环境")
    services = [
        {
            "name": item.name,
            "module": item.module_name,
            "base_url": item.base_url,
            "metadata": {
                "provider": "apifox",
                "apifox_service_id": item.provider_id,
            },
        }
        for item in environment.services
    ]
    if not services:
        services = [
            {
                "name": "default",
                "module": "default",
                "base_url": None,
                "metadata": {"provider": "apifox"},
            }
        ]
    variables = {
        item.name: item.value
        for item in environment.variables
        if not item.sensitive
    }
    secret_names = sorted(
        item.name for item in environment.variables if item.sensitive
    )
    return {
        "project_id": project_id,
        "source_id": source_id,
        "source_revision_id": source_revision_id,
        "name": environment.name,
        "description": "从 Apifox 手动读取，可在平台内继续编辑",
        "services": services,
        "variables": variables,
        "secret_placeholders": secret_names,
        "default_headers": {},
        "provider": {
            "type": "apifox",
            "environment_id": environment.id,
        },
    }


class ApifoxService:
    def __init__(
        self,
        provider_service,
        discovery_adapter,
        openapi_adapter,
        source_service,
        preview_metadata_store=None,
        session_factory=None,
        environment_service=None,
    ):
        self._provider_service = provider_service
        self._discovery_adapter = discovery_adapter
        self._openapi_adapter = openapi_adapter
        self._source_service = source_service
        self._session_factory = session_factory
        self._environment_service = environment_service
        self._preview_metadata_store = preview_metadata_store
        if self._preview_metadata_store is None and session_factory is not None:
            self._preview_metadata_store = self._store_preview_metadata

    def list_projects(self, owner_id):
        token = self._provider_service.require_apifox_token(owner_id)
        return self._discovery_adapter.list_projects(token)

    def get_context(self, owner_id, project_id, preferred_environment_id=""):
        token = self._provider_service.require_apifox_token(owner_id)
        return self._discovery_adapter.get_context(
            token,
            project_id,
            preferred_environment_id=preferred_environment_id,
        )

    def preview_refresh(self, owner_id, request, actor_id):
        if not isinstance(request, dict):
            raise ApifoxInputError("Apifox 刷新请求必须是对象")
        local_project_id = _text(request.get("project_id"), "本地项目")
        source_id = request.get("source_id") or None
        if source_id is not None:
            source_id = _text(source_id, "接口来源")
        provider_project_id = _text(
            request.get("apifox_project_id"), "Apifox 项目"
        )
        branch_id = _text(request.get("branch_id", ""), "Apifox 分支", True)
        environment_id = _text(
            request.get("environment_id"), "Apifox 环境"
        )
        token = self._provider_service.require_apifox_token(owner_id)
        context = self._discovery_adapter.get_context(
            token,
            provider_project_id,
            preferred_environment_id=environment_id,
        )
        environment = next(
            (item for item in context.environments if item.id == environment_id), None
        )
        if environment is None:
            raise ApifoxInputError("选择的 Apifox 环境不存在或已不可访问")
        document = self._openapi_adapter.export(
            token,
            provider_project_id,
            branch_id=branch_id,
            environment_id=environment_id,
        )
        source_preview = self._source_service.preview_refresh(
            local_project_id, source_id, document, actor_id
        )
        candidate = build_environment_candidate(
            local_project_id,
            source_preview.source_id if hasattr(source_preview, "source_id") else source_id,
            source_preview.candidate_revision_id,
            environment,
        )
        candidate["provider"].update(
            {
                "project_id": provider_project_id,
                "project_name": context.project.name,
                "branch_id": branch_id,
            }
        )
        if self._preview_metadata_store is not None:
            self._preview_metadata_store(source_preview.id, candidate, actor_id)
        return ApifoxRefreshPreviewView(source_preview, candidate)

    def activate_preview(self, owner_id, preview_id, actor_id):
        if self._session_factory is None or self._environment_service is None:
            raise RuntimeError("Apifox activation persistence is unavailable")
        owner = _text(owner_id, "用户")
        actor = _text(actor_id, "操作人")
        with self._session_factory.begin() as session:
            diff = session.get(ApiSourceDiff, preview_id)
            if diff is None:
                raise ApifoxInputError("Apifox 更新预览不存在")
            source = session.get(ApiSource, diff.source_id)
            if source is None or source.owner_id != owner:
                raise ApifoxInputError("Apifox 更新预览不存在")
            candidate = copy.deepcopy(
                (diff.summary or {}).get("environment_candidate")
            )
            if not isinstance(candidate, dict):
                raise ApifoxInputError("Apifox 环境候选数据不存在，请重新检查更新")
            source_revision = self._source_service.activate_preview_in_session(
                session, preview_id, actor, include_content=False
            )
            candidate["project_id"] = source_revision.project_id
            candidate["source_id"] = source_revision.source_id
            candidate["source_revision_id"] = source_revision.id
            secret_placeholders = tuple(
                sorted(
                    str(item)
                    for item in candidate.pop("secret_placeholders", ())
                    if str(item).strip()
                )
            )
            environment = self._environment_service.upsert_from_source_in_session(
                session, candidate, actor
            )
            workspace = session.scalar(
                select(ApiWorkspace)
                .where(ApiWorkspace.owner_id == owner)
                .with_for_update()
            )
            workspace_payload = {
                "project_id": source_revision.project_id,
                "source_revision_id": source_revision.id,
                "environment_revision_id": environment.revision_id,
            }
            if workspace is None:
                workspace = ApiWorkspace(**workspace_payload, **audit_fields(actor))
                session.add(workspace)
            else:
                for name, value in workspace_payload.items():
                    setattr(workspace, name, value)
                workspace.updated_by = actor
            session.flush()
            return ApifoxActivationView(
                source_revision=source_revision,
                environment=environment,
                workspace=workspace_payload,
                secret_placeholders=secret_placeholders,
            )

    def _store_preview_metadata(self, preview_id, candidate, actor_id):
        with self._session_factory.begin() as session:
            diff = session.get(ApiSourceDiff, preview_id)
            if diff is None:
                raise ApifoxInputError("Apifox 更新预览不存在")
            source = session.get(ApiSource, diff.source_id)
            if source is None or source.owner_id != actor_id:
                raise ApifoxInputError("Apifox 更新预览不存在")
            diff.summary = {
                **dict(diff.summary or {}),
                "environment_candidate": copy.deepcopy(candidate),
            }
            diff.updated_by = actor_id
            session.flush()
