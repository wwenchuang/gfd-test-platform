"""Live identity policy and SQL predicates for shared API project resources.

Unknown actors retain the standalone services' owner-only compatibility contract.
HTTP sessions are verified against the persistent identity store before reaching here.
No identity or evidence is cached across requests or worker dispatches.
"""

import re

from sqlalchemy import and_, false, or_, select, true

from .models.project import ApiProject
from .models.environment import ApiEnvironment, ApiEnvironmentRevision


class AccessDeniedError(PermissionError):
    def __init__(self, permission="api.view"):
        self.permission = permission
        super().__init__(f"缺少 {permission} 权限或数据授权，请联系管理员")


def get_access_profile(username):
    # Delayed import keeps standalone API tooling independent of identity setup.
    try:
        from task_server.identity import get_access_profile as profile
    except ModuleNotFoundError as error:
        if error.name == "task_server.identity":
            return None
        raise
    return profile(username)


def _active(profile):
    return profile.get("status") == "active" and not profile.get("must_change_password", False)


def require_permission(actor, permission):
    profile = get_access_profile(actor)
    if profile is None:
        return
    if not _active(profile) or not (profile.get("is_superuser") or permission in profile.get("permissions", ())):
        raise AccessDeniedError(permission)


def _scope_predicate(profile, kind, column):
    if not _active(profile):
        return false()
    values = profile.get("scope", {}).get(kind, [])
    if profile.get("is_superuser") or values == "*":
        return true()
    if not isinstance(values, (list, tuple)):
        return false()
    return column.in_([str(value) for value in values])


def project_predicate(actor, model=ApiProject):
    profile = get_access_profile(actor)
    if profile is None:
        return model.owner_id == actor
    column = model.id if model is ApiProject else model.project_id
    return _scope_predicate(profile, "api_projects", column)


def environment_predicate(actor):
    profile = get_access_profile(actor)
    if profile is None:
        return true()
    return and_(project_predicate(actor, ApiEnvironment),
                _scope_predicate(profile, "api_environments", ApiEnvironment.id))


def environment_revision_predicate(actor, column):
    if get_access_profile(actor) is None:
        return true()
    return column.in_(select(ApiEnvironmentRevision.id).join(
        ApiEnvironment, ApiEnvironment.id == ApiEnvironmentRevision.environment_id
    ).where(environment_predicate(actor)))


def resource_predicate(actor, model):
    """Compose scopes in SQL, including indirect IDs, without loading JSON evidence."""
    from .models.case import ApiCase, ApiCaseVersion
    from .models.execution import ApiExecution, ApiExecutionCase
    from .models.source import ApiSource, ApiSourceRevision, ApiSourceEndpoint, ApiSourceDiff
    if model is ApiProject:
        return project_predicate(actor)
    if model is ApiEnvironment:
        return and_(project_predicate(actor, model), environment_predicate(actor))
    if model is ApiEnvironmentRevision:
        return model.environment_id.in_(select(ApiEnvironment.id).where(resource_predicate(actor, ApiEnvironment)))
    if hasattr(model, "project_id"):
        result = project_predicate(actor, model)
        if hasattr(model, "environment_revision_id"):
            # Scheduled latest-revision jobs also have an environment_id.
            if hasattr(model, "environment_id"):
                result = and_(result, model.environment_id.in_(select(ApiEnvironment.id).where(resource_predicate(actor, ApiEnvironment))))
            else:
                result = and_(result, environment_revision_predicate(actor, model.environment_revision_id))
        return result
    parent = {
        ApiSourceRevision: ("source_id", ApiSource),
        ApiSourceDiff: ("source_id", ApiSource),
        ApiSourceEndpoint: ("revision_id", ApiSourceRevision),
        ApiCaseVersion: ("case_id", ApiCase),
        ApiExecutionCase: ("execution_id", ApiExecution),
    }.get(model)
    if parent:
        field, parent_model = parent
        return getattr(model, field).in_(select(parent_model.id).where(resource_predicate(actor, parent_model)))
    return false()


def resource_allowed(session, record, actor):
    if record is None:
        return False
    if get_access_profile(actor) is None:
        return record.owner_id == actor
    model = type(record)
    return session.scalar(select(model.id).where(model.id == record.id, resource_predicate(actor, model))) is not None


def require_resource(session, record, actor, permission="api.view"):
    require_permission(actor, permission)
    if not resource_allowed(session, record, actor):
        raise AccessDeniedError(permission)


def inherited_audit(session, actor, parent_model, parent_id):
    """Inherit ownership with a bounded, transaction-local metadata lookup."""
    owner = actor
    if get_access_profile(actor) is not None:
        cache = session.info.setdefault("api_data_owners", {})
        key = (parent_model, parent_id)
        if key not in cache:
            cache[key] = session.scalar(select(parent_model.owner_id).where(parent_model.id == parent_id))
        owner = cache[key]
        if not owner:
            raise AccessDeniedError("api.edit")
    return {"owner_id": owner, "created_by": actor, "updated_by": actor}


def require_execution_environment(session, revision_id, actor, project_id=None):
    require_permission(actor, "api.execute")
    row = session.execute(select(ApiEnvironmentRevision.id, ApiEnvironmentRevision.environment_id, ApiEnvironmentRevision.name,
                                 ApiEnvironment.name.label("environment_name"), ApiEnvironment.project_id)
                          .join(ApiEnvironment, ApiEnvironment.id == ApiEnvironmentRevision.environment_id)
                          .where(ApiEnvironmentRevision.id == revision_id,
                                 resource_predicate(actor, ApiEnvironmentRevision))).one_or_none()
    if row is None or (project_id is not None and row.project_id != project_id):
        raise AccessDeniedError("api.execute")
    if environment_is_production(session, row.environment_id):
        require_permission(actor, "api.production")


_PRODUCTION_NAME = r"生产|正式|(^|[^a-z])prod(uction)?($|[^a-z])"


def environment_is_production(session, environment_id):
    from .models.environment import ApiEnvironmentService
    # Revisions are immutable. Classification is sticky across renames, service
    # replacement, restores and source sync, including historical executions.
    marked = select(ApiEnvironmentRevision.id).outerjoin(
        ApiEnvironmentService, ApiEnvironmentService.revision_id == ApiEnvironmentRevision.id
    ).where(ApiEnvironmentRevision.environment_id == environment_id, or_(
        ApiEnvironmentRevision.name.op("~*")(_PRODUCTION_NAME),
        ApiEnvironmentService.metadata_json.contains({"production": True}),
    )).limit(1)
    current_name = select(ApiEnvironment.id).where(
        ApiEnvironment.id == environment_id, ApiEnvironment.name.op("~*")(_PRODUCTION_NAME)
    ).limit(1)
    return session.scalar(marked) is not None or session.scalar(current_name) is not None


def require_environment_configuration(session, environment, actor, *, name="", services=None):
    require_permission(actor, "api.environment")
    if environment is not None:
        require_resource(session, environment, actor, "api.environment")
    marked = re.search(_PRODUCTION_NAME, name, re.I) or any(
        item.get("metadata", {}).get("production") is True
        for item in (services or {}).values()
    )
    if marked or (environment is not None and environment_is_production(session, environment.id)):
        require_permission(actor, "api.production")


def authorize_http(actor, method, segments):
    """Action gate before payload validation; resource resolvers enforce object scope."""
    require_permission(actor, "api.view")
    if method == "GET":
        return
    head = segments[0] if segments else ""
    tail = segments[-1] if segments else ""
    if head in {"load-scenarios", "load-scenario-versions", "load-datasets", "load-runs", "load-agents", "load-agent-enrollments"}:
        if head in {"load-agents", "load-agent-enrollments"} and method != "GET":
            require_permission(actor, "api.loadtest.manage_agents")
        elif head == "load-agent-enrollments":
            require_permission(actor, "api.loadtest.manage_agents")
        elif method == "GET" or tail in {"report", "events", "ai-analysis"}:
            require_permission(actor, "api.loadtest.view")
        elif head == "load-runs":
            require_permission(actor, "api.loadtest.execute")
        else:
            require_permission(actor, "api.loadtest.edit")
        return
    if method == "PUT" and segments == ("workspace",):
        return
    if method == "POST" and head == "executions" and tail == "sse-ticket":
        return
    if head == "notifications" or (head == "executions" and tail == "notify"):
        require_permission(actor, "platform.notify")
        if method == "PUT":
            require_permission(actor, "platform.configure")
        return
    if head in {"environments", "environment-revisions"}:
        require_permission(actor, "api.environment")
        if method == "DELETE":
            require_permission(actor, "api.delete")
        return
    if head == "providers":
        require_permission(actor, "api.environment" if tail == "credential" else "api.edit")
        return
    if head == "baselines" or (head == "case-versions" and tail == "baseline"):
        require_permission(actor, "api.baseline")
        if method == "DELETE":
            require_permission(actor, "api.delete")
        return
    if method == "DELETE" or (head == "executions" and tail in {"archive", "restore"}):
        require_permission(actor, "api.delete")
        return
    if head in {"executions", "regressions", "workflow-steps"} or tail == "run":
        require_permission(actor, "api.execute")
        return
    if head == "scheduled-jobs":
        require_permission(actor, "api.execute")
    require_permission(actor, "api.edit")


def list_identity_scope_options(session_factory=None):
    """Admin integration only: two metadata projections, never credentials/evidence."""
    if session_factory is None:
        from .config import ApiTestingSettings
        if not ApiTestingSettings.from_env().enabled:
            return {"api_projects": [], "api_environments": []}
        from .db import _session_factory
        session_factory = _session_factory()
    with session_factory() as session:
        projects = session.execute(select(ApiProject.id, ApiProject.name).where(ApiProject.status == "active").order_by(ApiProject.name, ApiProject.id))
        result = {"api_projects": [dict(row._mapping) for row in projects]}
        environments = session.execute(select(ApiEnvironment.id, ApiEnvironment.name, ApiEnvironment.project_id)
                                       .join(ApiProject, ApiProject.id == ApiEnvironment.project_id)
                                       .where(ApiProject.status == "active", ApiEnvironment.status == "active")
                                       .order_by(ApiEnvironment.name, ApiEnvironment.id))
        result["api_environments"] = [dict(row._mapping) for row in environments]
        return result
