"""Reusable host monitoring configurations. Secrets never cross the public boundary."""
import copy
from datetime import datetime, timezone
from urllib.parse import urlsplit
from sqlalchemy import select
from .. import access
from ..crypto import encrypt_secret, decrypt_secret, secret_fingerprint
from ..models.environment import ApiEnvironment, ApiEnvironmentRevision, ApiSecretValue
from ..models.load_monitoring import ApiLoadMonitoringService, ApiLoadMonitoringRevision
from .load_monitoring_prometheus import PrometheusMonitoringClient, build_query


class LoadMonitoringConfigError(ValueError):
    """Validated public input or readiness error; safe to render in HTTP responses."""


class LoadMonitoringConfigService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @staticmethod
    def _environment(session, revision_id, actor, manage=False):
        revision = session.get(ApiEnvironmentRevision, revision_id)
        if revision is None:
            raise LookupError('监控环境不存在')
        environment = session.get(ApiEnvironment, revision.environment_id)
        access.require_resource(session, environment, actor, 'api.environment' if manage else 'api.loadtest.view')
        if manage:
            access.require_environment_configuration(session, environment, actor)
        return environment

    @staticmethod
    def _service(session, service_id, actor, manage=False):
        service = session.get(ApiLoadMonitoringService, service_id)
        if service is None:
            raise LookupError('监控服务不存在')
        environment = session.get(ApiEnvironment, service.environment_id)
        access.require_resource(session, environment, actor, 'api.environment' if manage else 'api.loadtest.view')
        if manage:
            access.require_environment_configuration(session, environment, actor)
        return service

    @staticmethod
    def _view(service, revision):
        return dict(copy.deepcopy(revision.definition), id=service.id, revision_id=revision.id,
                    environment_id=service.environment_id, project_id=service.project_id,
                    status=service.status, has_token=bool(revision.secret_value_id),
                    last_check=copy.deepcopy(service.last_check))

    def list(self, environment_revision_id, *, actor):
        with self._session_factory() as session:
            env = self._environment(session, environment_revision_id, actor)
            records = session.scalars(select(ApiLoadMonitoringService).where(ApiLoadMonitoringService.environment_id == env.id).order_by(ApiLoadMonitoringService.created_at)).all()
            return {'items': [self._view(s, session.get(ApiLoadMonitoringRevision, s.active_revision_id)) for s in records]}

    def _revision(self, session, service, payload, actor, previous=None):
        if not isinstance(payload, dict):
            raise LoadMonitoringConfigError('监控配置必须是对象')
        allowed = {'name', 'description', 'source_url', 'labels', 'metrics', 'step_seconds', 'deployment', 'token', 'authorize_host', 'environment_revision_id'}
        if set(payload) - allowed:
            raise LoadMonitoringConfigError('监控配置包含不支持的字段')
        definition = copy.deepcopy(previous.definition) if previous else {
            'description': '', 'deployment': 'host', 'metrics': ['cpu_percent', 'memory_percent'], 'step_seconds': 15}
        for key in allowed - {'token', 'authorize_host', 'environment_revision_id'}:
            if key in payload:
                definition[key] = copy.deepcopy(payload[key])
        for key, limit in [('name', 160), ('description', 2000), ('source_url', 2048)]:
            value = definition.get(key)
            if not isinstance(value, str) or len(value) > limit or (key != 'description' and not value.strip()):
                raise LoadMonitoringConfigError('监控名称、说明或地址无效')
        step = definition['step_seconds']
        if isinstance(step, bool) or not isinstance(step, int) or not 5 <= step <= 300:
            raise LoadMonitoringConfigError('监控采样间隔应为 5 至 300 秒')
        metrics = definition['metrics']
        if not isinstance(metrics, list) or not metrics or len(metrics) > 2 or any(m not in ('cpu_percent', 'memory_percent') for m in metrics) or len(set(metrics)) != len(metrics):
            raise LoadMonitoringConfigError('请选择支持的 CPU 或内存指标')
        for metric in metrics:
            build_query(metric, definition['deployment'], definition.get('labels'))
        source_changed = previous is None or previous.definition['source_url'] != definition['source_url']
        if source_changed:
            # Approval is an explicit administrator operation, including for standalone
            # callers: the access layer's unknown-actor compatibility is insufficient.
            profile = access.get_access_profile(actor)
            if payload.get('authorize_host') is not True or profile is None:
                raise access.AccessDeniedError('platform.configure')
            access.require_permission(actor, 'platform.configure')
            try:
                host = urlsplit(definition['source_url']).hostname
            except ValueError:
                raise LoadMonitoringConfigError('监控地址无效') from None
            approved_by = actor
        else:
            host, approved_by = previous.authorized_host, previous.authorized_by
        token = payload.get('token')
        if token is not None and not isinstance(token, str):
            raise LoadMonitoringConfigError('监控令牌无效')
        secret_id = previous.secret_value_id if previous else None
        inherited_token = ''
        if token is None and secret_id:
            inherited_token = decrypt_secret(session.get(ApiSecretValue, secret_id).ciphertext)
        PrometheusMonitoringClient(definition['source_url'], token if token is not None else inherited_token, allowed_hosts=[host])
        audit = {'owner_id': service.owner_id, 'created_by': actor, 'updated_by': actor}
        if token is not None:
            secret_id = None
            if token:
                secret = ApiSecretValue(project_id=service.project_id, environment_id=service.environment_id,
                    name='load-monitoring-token', ciphertext=encrypt_secret(token), fingerprint=secret_fingerprint(token), **audit)
                session.add(secret)
                session.flush()
                secret_id = secret.id
        revision = ApiLoadMonitoringRevision(service_id=service.id, definition=definition,
            authorized_host=host, authorized_by=approved_by, secret_value_id=secret_id, **audit)
        session.add(revision)
        session.flush()
        service.active_revision_id = revision.id
        service.updated_by = actor
        service.last_check = {}
        return revision

    def create(self, environment_revision_id, payload, *, actor):
        with self._session_factory.begin() as session:
            env = self._environment(session, environment_revision_id, actor, True)
            service = ApiLoadMonitoringService(environment_id=env.id, project_id=env.project_id,
                status='active', last_check={}, **access.inherited_audit(session, actor, ApiEnvironment, env.id))
            session.add(service)
            session.flush()
            return self._view(service, self._revision(session, service, payload, actor))

    def update(self, service_id, payload, *, actor):
        with self._session_factory.begin() as session:
            service = self._service(session, service_id, actor, True)
            previous = session.get(ApiLoadMonitoringRevision, service.active_revision_id)
            return self._view(service, self._revision(session, service, payload, actor, previous))

    def disable(self, service_id, *, actor):
        with self._session_factory.begin() as session:
            service = self._service(session, service_id, actor, True)
            service.status, service.updated_by = 'disabled', actor
            return self._view(service, session.get(ApiLoadMonitoringRevision, service.active_revision_id))

    def get_snapshot(self, revision_id, *, actor, environment_revision_id, project_id=None):
        with self._session_factory() as session:
            env = self._environment(session, environment_revision_id, actor)
            if project_id is not None and env.project_id != project_id:
                raise access.AccessDeniedError('api.loadtest.view')
            revision = session.get(ApiLoadMonitoringRevision, revision_id)
            if revision is None:
                raise LookupError('监控配置版本不存在')
            service = self._service(session, revision.service_id, actor)
            if service.environment_id != env.id:
                raise access.AccessDeniedError('api.loadtest.view')
            if service.status != 'active':
                raise LoadMonitoringConfigError('监控服务已停用')
            result = self._view(service, revision)
            result.pop('last_check', None)
            result['credential_ref'] = revision.secret_value_id
            result['queries'] = {m: build_query(m, result['deployment'], result['labels']) for m in result['metrics']}
            result['metric_scope'] = 'host'
            result['template_version'] = 'node-exporter-host-v1'
            return result

    def _definition_for_revision(self, revision_id):
        with self._session_factory() as session:
            revision = session.get(ApiLoadMonitoringRevision, revision_id)
            if revision is None:
                raise LookupError('监控配置版本不存在')
            return dict(copy.deepcopy(revision.definition), revision_id=revision.id,
                        id=revision.service_id, template_version='node-exporter-host-v1', metric_scope='host')

    def require_ready(self, revision_id, *, actor, max_age_seconds=300):
        with self._session_factory() as session:
            revision = session.get(ApiLoadMonitoringRevision, revision_id)
            if revision is None:
                raise LookupError('监控配置版本不存在')
            service = self._service(session, revision.service_id, actor)
            check = service.last_check or {}
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(check['checked_at'])).total_seconds()
            except (KeyError, TypeError, ValueError):
                age = -1
            if (service.status != 'active' or check.get('revision_id') != revision_id
                    or check.get('state') != 'ready' or check.get('freshness') != 'verified' or not 0 <= age <= max_age_seconds):
                raise LoadMonitoringConfigError('必选监控需要对当前版本重新检查连接并取得样本')
            return copy.deepcopy(check)

    def _client_for_revision(self, revision_id):
        """Internal worker only; callers must bind an authorized snapshot first."""
        with self._session_factory() as session:
            revision = session.get(ApiLoadMonitoringRevision, revision_id)
            if revision is None:
                raise LookupError('监控配置版本不存在')
            secret = session.get(ApiSecretValue, revision.secret_value_id) if revision.secret_value_id else None
            return PrometheusMonitoringClient(revision.definition['source_url'], decrypt_secret(secret.ciphertext) if secret else '', allowed_hosts=[revision.authorized_host])

    def test_connection(self, service_id, *, actor, revision_id=None):
        profile = access.get_access_profile(actor)
        manager = profile is None or profile.get('is_superuser') or 'api.environment' in profile.get('permissions', [])
        access.require_permission(actor, 'api.environment' if manager else 'api.loadtest.execute')
        with self._session_factory() as session:
            service = self._service(session, service_id, actor, manager)
            if not manager and access.environment_is_production(session, service.environment_id):
                access.require_permission(actor, 'api.production')
            if service.status != 'active':
                raise LoadMonitoringConfigError('监控服务已停用')
            active_revision_id = service.active_revision_id
            if revision_id is not None and (not isinstance(revision_id, str) or not revision_id):
                raise LoadMonitoringConfigError('监控配置版本无效')
            revision = session.get(ApiLoadMonitoringRevision, revision_id or active_revision_id)
            if revision is None or revision.service_id != service.id:
                raise LoadMonitoringConfigError('监控配置版本不属于当前服务')
            revision_id = revision.id
        now = datetime.now(timezone.utc)
        try:
            from .load_monitoring_collection_service import LoadMonitoringCollectionService
            evidence = LoadMonitoringCollectionService(self._session_factory, monitoring_service=self, now=lambda: now).probe([{'revision_id': revision_id}])
            check = (evidence.get('services') or [{}])[0]
            state = check.get('state', 'failed')
            result = {'state': 'ready' if state == 'completed' else ('missing' if state == 'missing' else 'failed'),
                      'message': check.get('message', '监控查询失败'),
                      'freshness': 'verified' if state == 'completed' else 'unverified'}
        except Exception:
            result = {'state': 'failed', 'message': '监控查询失败，请检查地址、网络、只读凭据及数据格式'}
        result['checked_at'] = now.isoformat()
        result['revision_id'] = revision_id
        with self._session_factory.begin() as session:
            service = self._service(session, service_id, actor, manager)
            if service.status == 'active' and service.active_revision_id == active_revision_id:
                service.last_check = result
        return result
