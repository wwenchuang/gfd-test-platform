"""Editable API environments with encrypted secrets and strict runtime rendering."""

import copy
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

from .. import access

from ..contracts.environment import (
    EnvironmentAssetView,
    EnvironmentRevisionSummary,
    EnvironmentServiceView,
    EnvironmentView,
    ResolvedEnvironment,
    SecretVariableView,
    UnresolvedServiceError,
)
from ..crypto import decrypt_secret, encrypt_secret, secret_fingerprint
from ..repositories.environment_repository import EnvironmentRepository


VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
PLACEHOLDER = re.compile(r"{{([A-Za-z_][A-Za-z0-9_.-]*)}}")
MAX_PLACEHOLDER_DEPTH = 10
SENSITIVE_HEADER = re.compile(r"authorization|cookie|token|password|secret|api[-_]?key|signature", re.I)


def _header_is_template(name, value):
    if not value or PLACEHOLDER.fullmatch(value):
        return True
    if "authorization" in name.lower():
        scheme, _, credential = value.partition(" ")
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", scheme) and PLACEHOLDER.fullmatch(credential))
    return False


class EnvironmentNotFoundError(LookupError):
    pass


class EnvironmentInputError(ValueError):
    pass


class UnresolvedVariableError(EnvironmentInputError):
    pass


class PlaceholderSyntaxError(EnvironmentInputError):
    pass


class PlaceholderCycleError(EnvironmentInputError):
    pass


class PlaceholderDepthError(EnvironmentInputError):
    pass


class SecretOverrideError(EnvironmentInputError):
    pass


def _mapping(value, label):
    if not isinstance(value, dict):
        raise EnvironmentInputError(f"{label} must be an object")
    return copy.deepcopy(value)


def _text(value, label, *, allow_empty=False):
    if not isinstance(value, str):
        raise EnvironmentInputError(f"{label} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise EnvironmentInputError(f"{label} must not be empty")
    return normalized


def _variable_name(value):
    name = _text(value, "variable name")
    if not VARIABLE_NAME.fullmatch(name):
        raise EnvironmentInputError(f"invalid variable name: {name}")
    return name


def _validate_hostname(hostname):
    rendered = PLACEHOLDER.sub("placeholder", hostname)
    if "{{" in rendered or "}}" in rendered:
        raise EnvironmentInputError("service URL contains an invalid host placeholder")
    try:
        ipaddress.ip_address(rendered)
        return
    except ValueError:
        pass
    try:
        ascii_hostname = rendered.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        raise EnvironmentInputError("service URL contains an invalid host") from None
    if not ascii_hostname or len(ascii_hostname) > 253:
        raise EnvironmentInputError("service URL contains an invalid host")
    for label in ascii_hostname.split("."):
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or re.fullmatch(r"[A-Za-z0-9-]+", label) is None
        ):
            raise EnvironmentInputError("service URL contains an invalid host")


def _validate_service_url(value):
    url = _text(value, "service URL")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise EnvironmentInputError("service URL must not contain whitespace")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise EnvironmentInputError("service URL is invalid") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EnvironmentInputError("service URL must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise EnvironmentInputError("service URL must not include credentials")
    if port is not None and port <= 0:
        raise EnvironmentInputError("service URL contains an invalid port")
    _validate_hostname(hostname)
    return url


def _normalize_service_url(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnvironmentInputError("service URL must be a string or null")
    if not value.strip():
        return None
    return _validate_service_url(value)


def _normalize_services(value):
    if isinstance(value, dict):
        entries = []
        for name, item in value.items():
            if isinstance(item, str):
                entries.append({"name": name, "base_url": item})
            elif isinstance(item, dict):
                entries.append({"name": name, **copy.deepcopy(item)})
            else:
                raise EnvironmentInputError("environment services must contain objects")
    elif isinstance(value, list):
        entries = copy.deepcopy(value)
    else:
        raise EnvironmentInputError("environment services must be a list or object")
    normalized = {}
    for item in entries:
        if not isinstance(item, dict):
            raise EnvironmentInputError("environment services must contain objects")
        name = _text(item.get("name", item.get("service_name", "")), "service name")
        if name in normalized:
            raise EnvironmentInputError("environment service names must be unique")
        normalized[name] = {
            "name": name,
            "module_name": _text(
                item.get("module", item.get("module_name", "default")),
                "service module",
            ),
            "base_url": _normalize_service_url(
                item.get("base_url", item.get("url", ""))
            ),
            "metadata": _mapping(item.get("metadata", {}), "service metadata"),
        }
    if not normalized:
        raise EnvironmentInputError("environment must define at least one service URL")
    return normalized


def _normalize_headers(value):
    headers = _mapping(value, "default headers")
    normalized = {}
    for raw_name, raw_value in headers.items():
        name = _text(raw_name, "header name")
        if re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None:
            raise EnvironmentInputError("header name is invalid")
        if not isinstance(raw_value, str):
            raise EnvironmentInputError("default header values must be strings")
        if any(existing.lower() == name.lower() for existing in normalized):
            raise EnvironmentInputError("default header names must be unique")
        if "\r" in raw_value or "\n" in raw_value:
            raise EnvironmentInputError("default headers must not contain line breaks")
        normalized[name] = raw_value
    return normalized


def _normalize_public_variables(value):
    variables = _mapping(value, "environment variables")
    return {_variable_name(name): copy.deepcopy(item) for name, item in variables.items()}


def _source_payload(payload):
    source = _mapping(payload, "source environment")
    services_value = source.get("services")
    if services_value is None:
        services_value = source.get("base_urls", source.get("baseUrls"))
    if services_value is None and source.get("base_url"):
        services_value = {"default": source["base_url"]}
    return {
        "project_id": _text(source.get("project_id", ""), "project id"),
        "source_id": source.get("source_id") or None,
        "source_revision_id": source.get("source_revision_id") or None,
        "name": _text(source.get("name", ""), "environment name"),
        "description": _text(
            source.get("description", ""), "environment description", allow_empty=True
        ),
        "services": _normalize_services(services_value),
        "variables": _normalize_public_variables(source.get("variables", {})),
        "default_headers": _normalize_headers(
            source.get("default_headers", source.get("defaultHeaders", {}))
        ),
    }


class _PlaceholderResolver:
    def __init__(self, values):
        self._values = copy.deepcopy(dict(values))
        self._cache = {}

    def resolve_all(self):
        return {name: self._resolve_name(name, ()) for name in self._values}

    def render(self, value):
        return self._render(value, ())

    def _resolve_name(self, name, stack):
        if name in self._cache:
            return copy.deepcopy(self._cache[name])
        if name not in self._values:
            raise UnresolvedVariableError(f"undefined environment variable: {name}")
        if name in stack:
            cycle = stack[stack.index(name) :] + (name,)
            raise PlaceholderCycleError(
                "environment variable cycle: " + " -> ".join(cycle)
            )
        if len(stack) >= MAX_PLACEHOLDER_DEPTH:
            raise PlaceholderDepthError(
                f"environment variable maximum depth is {MAX_PLACEHOLDER_DEPTH}"
            )
        resolved = self._render(self._values[name], stack + (name,))
        self._cache[name] = copy.deepcopy(resolved)
        return resolved

    def _render_string(self, value, stack):
        matches = list(PLACEHOLDER.finditer(value))
        remainder = PLACEHOLDER.sub("", value)
        if "{{" in remainder or "}}" in remainder:
            raise PlaceholderSyntaxError("invalid environment placeholder syntax")
        if not matches:
            return value
        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            return copy.deepcopy(self._resolve_name(matches[0].group(1), stack))
        output = []
        cursor = 0
        for match in matches:
            output.append(value[cursor : match.start()])
            replacement = self._resolve_name(match.group(1), stack)
            if not isinstance(replacement, str):
                raise EnvironmentInputError(
                    f"embedded placeholder {match.group(1)} must resolve to a string"
                )
            output.append(replacement)
            cursor = match.end()
        output.append(value[cursor:])
        return "".join(output)

    def _render(self, value, stack):
        if isinstance(value, str):
            return self._render_string(value, stack)
        if isinstance(value, list):
            return [self._render(item, stack) for item in value]
        if isinstance(value, tuple):
            return tuple(self._render(item, stack) for item in value)
        if isinstance(value, dict):
            rendered = {}
            for key, item in value.items():
                rendered_key = self._render_string(key, stack) if isinstance(key, str) else key
                if not isinstance(rendered_key, (str, int, float, bool, type(None))):
                    raise EnvironmentInputError("rendered object key must be a JSON scalar")
                if rendered_key in rendered:
                    raise EnvironmentInputError("rendered object keys must be unique")
                rendered[rendered_key] = self._render(item, stack)
            return rendered
        return copy.deepcopy(value)


class EnvironmentService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def import_from_source(self, payload, actor_id):
        access.require_permission(actor_id, "api.environment")
        source = _source_payload(payload)
        actor = _text(actor_id, "actor id")
        with self._session_factory.begin() as session:
            return self._import_normalized_in_session(session, source, actor)

    def import_from_source_in_session(self, session, payload, actor_id):
        access.require_permission(actor_id, "api.environment")
        return self._import_normalized_in_session(
            session, _source_payload(payload), _text(actor_id, "actor id")
        )

    def upsert_from_source_in_session(self, session, payload, actor_id):
        access.require_permission(actor_id, "api.environment")
        source = _source_payload(payload)
        actor = _text(actor_id, "actor id")
        repository = EnvironmentRepository(session)
        project, source_record, source_revision = self._validate_source_scope(
            repository, source
        )
        access.require_resource(session, project, actor, "api.environment")
        environment = repository.find_environment_for_update(
            project.id,
            source_record.id if source_record else None,
            source["name"],
        )
        if environment is None:
            return self._create_imported_revision(
                repository, source, project, source_record, source_revision, actor
            )

        access.require_environment_configuration(session, environment, actor)
        previous = repository.get_revision(environment.active_revision_id)
        if previous is None:
            raise EnvironmentNotFoundError(
                "API environment active revision was not found"
            )
        _, previous_public_variables, previous_secrets = self._revision_state(
            repository, previous.id
        )
        previous_scopes = self._public_variable_scopes(repository, previous.id)
        source_variables = {
            name: value
            for name, value in source["variables"].items()
            if name not in previous_secrets
        }
        platform_variables = {
            name: value
            for name, value in previous_public_variables.items()
            if previous_scopes.get(name, "environment") != "source"
        }
        public_variables = copy.deepcopy(source_variables)
        public_variables.update(copy.deepcopy(platform_variables))
        variable_scopes = {name: "source" for name in source_variables}
        variable_scopes.update({name: "environment" for name in platform_variables})
        default_headers = copy.deepcopy(previous.default_headers)
        default_headers.update(source["default_headers"])
        default_headers = self._protect_headers(repository, environment, default_headers, previous_secrets, public_variables, actor, previous.default_headers)
        revision = repository.create_revision(
            environment.id,
            source_revision.id if source_revision else None,
            repository.next_revision_number(environment.id),
            source["name"],
            source["description"],
            default_headers,
            actor,
        )
        self._persist_services(repository, revision.id, source["services"], actor)
        self._persist_public_variables(
            repository,
            revision.id,
            environment.id,
            public_variables,
            actor,
            scopes=variable_scopes,
        )
        for secret_name, secret in sorted(previous_secrets.items()):
            repository.add_secret_variable(
                revision.id, environment.id, secret_name, secret.id, actor
            )
        environment.source_id = source_record.id if source_record else None
        environment.active_revision_id = revision.id
        environment.updated_by = actor
        repository.flush()
        return self._view(repository, environment, revision)

    def _import_normalized_in_session(self, session, source, actor):
        repository = EnvironmentRepository(session)
        project, source_record, source_revision = self._validate_source_scope(
            repository, source
        )
        access.require_resource(session, project, actor, "api.environment")
        return self._create_imported_revision(
            repository, source, project, source_record, source_revision, actor
        )

    @staticmethod
    def _validate_source_scope(repository, source):
        project = repository.get_project(source["project_id"])
        if project is None:
            raise EnvironmentNotFoundError("API testing project was not found")
        source_record = None
        source_revision = None
        if source["source_id"]:
            source_record = repository.get_source(source["source_id"])
            if source_record is None or source_record.project_id != project.id:
                raise EnvironmentNotFoundError(
                    "API source was not found in this project"
                )
        if source["source_revision_id"]:
            source_revision = repository.get_source_revision(
                source["source_revision_id"]
            )
            if source_revision is None or (
                source_record and source_revision.source_id != source_record.id
            ):
                raise EnvironmentNotFoundError("API source revision was not found")
            if source_record is None:
                source_record = repository.get_source(source_revision.source_id)
                if source_record is None or source_record.project_id != project.id:
                    raise EnvironmentNotFoundError(
                        "API source revision was not found in this project"
                    )
        return project, source_record, source_revision

    def _create_imported_revision(
        self, repository, source, project, source_record, source_revision, actor
    ):
        access.require_environment_configuration(repository.session, None, actor, name=source["name"], services=source["services"])
        environment = repository.create_environment(
            project.id,
            source_record.id if source_record else None,
            source["name"],
            actor,
        )
        secrets = {}
        headers = self._protect_headers(repository, environment, source["default_headers"], secrets, source["variables"], actor)
        revision = repository.create_revision(
            environment.id,
            source_revision.id if source_revision else None,
            1,
            source["name"],
            source["description"],
            headers,
            actor,
        )
        self._persist_services(repository, revision.id, source["services"], actor)
        self._persist_public_variables(
            repository,
            revision.id,
            environment.id,
            source["variables"],
            actor,
            scopes={name: "source" for name in source["variables"]},
        )
        for secret_name, secret in sorted(secrets.items()):
            repository.add_secret_variable(revision.id, environment.id, secret_name, secret.id, actor)
        environment.active_revision_id = revision.id
        repository.flush()
        return self._view(repository, environment, revision)

    def create_revision(self, environment_id, payload, secret_updates, actor_id):
        access.require_permission(actor_id, "api.environment")
        changes = _mapping(payload, "environment revision")
        unknown = set(changes) - {
            "name",
            "description",
            "services",
            "variables",
            "default_headers",
        }
        if unknown:
            raise EnvironmentInputError("unsupported environment revision fields")
        secret_changes = _mapping(secret_updates, "secret updates")
        secret_changes = {_variable_name(name): value for name, value in secret_changes.items()}
        actor = _text(actor_id, "actor id")

        with self._session_factory.begin() as session:
            repository = EnvironmentRepository(session)
            environment = repository.get_environment_for_update(environment_id)
            if environment is None:
                raise EnvironmentNotFoundError("API environment was not found")
            access.require_environment_configuration(session, environment, actor, name=str(changes.get("name") or ""))
            previous = repository.get_revision(environment.active_revision_id)
            if previous is None:
                raise EnvironmentNotFoundError("API environment active revision was not found")
            previous_services, public_variables, previous_secrets = self._revision_state(
                repository, previous.id
            )
            variable_scopes = self._public_variable_scopes(repository, previous.id)
            variable_changes = (
                _normalize_public_variables(changes["variables"])
                if "variables" in changes
                else {}
            )
            conflicting = set(variable_changes) & set(previous_secrets)
            if conflicting:
                raise SecretOverrideError(
                    "public variables cannot override secret variables: "
                    + ", ".join(sorted(conflicting))
                )
            public_variables.update(variable_changes)
            variable_scopes.update(
                {name: "environment" for name in variable_changes}
            )
            for secret_name, secret_value in secret_changes.items():
                if secret_name in public_variables:
                    del public_variables[secret_name]
                    variable_scopes.pop(secret_name, None)
                if secret_value is None:
                    previous_secrets.pop(secret_name, None)
                    continue
                if not isinstance(secret_value, str) or not secret_value:
                    raise EnvironmentInputError("secret updates must be non-empty strings or null")
                encrypted = encrypt_secret(secret_value)
                secret = repository.create_secret(
                    environment.project_id,
                    environment.id,
                    secret_name,
                    encrypted,
                    secret_fingerprint(secret_value),
                    actor,
                )
                previous_secrets[secret_name] = secret

            services = (
                _normalize_services(changes["services"])
                if "services" in changes
                else previous_services
            )
            headers = (
                _normalize_headers(changes["default_headers"])
                if "default_headers" in changes
                else copy.deepcopy(previous.default_headers)
            )
            headers = self._protect_headers(repository, environment, headers, previous_secrets, public_variables, actor, previous.default_headers)
            name = (
                _text(changes["name"], "environment name")
                if "name" in changes
                else previous.name
            )
            description = (
                _text(
                    changes["description"],
                    "environment description",
                    allow_empty=True,
                )
                if "description" in changes
                else previous.description
            )
            revision = repository.create_revision(
                environment.id,
                previous.source_revision_id,
                repository.next_revision_number(environment.id),
                name,
                description,
                headers,
                actor,
            )
            self._persist_services(repository, revision.id, services, actor)
            self._persist_public_variables(
                repository,
                revision.id,
                environment.id,
                public_variables,
                actor,
                scopes=variable_scopes,
            )
            for secret_name, secret in sorted(previous_secrets.items()):
                repository.add_secret_variable(
                    revision.id, environment.id, secret_name, secret.id, actor
                )
            environment.name = name
            environment.active_revision_id = revision.id
            environment.updated_by = actor
            repository.flush()
            return self._view(repository, environment, revision)

    def restore_revision(self, revision_id, actor_id):
        access.require_permission(actor_id, "api.environment")
        actor = _text(actor_id, "actor id")
        with self._session_factory.begin() as session:
            repository = EnvironmentRepository(session)
            source_revision = repository.get_revision(revision_id)
            if source_revision is None:
                raise EnvironmentNotFoundError("API environment revision was not found")
            environment = repository.get_environment_for_update(
                source_revision.environment_id
            )
            if environment is None:
                raise EnvironmentNotFoundError("API environment was not found")
            access.require_environment_configuration(session, environment, actor)
            project = repository.get_project(environment.project_id)
            if not access.resource_allowed(session, environment, actor):
                raise EnvironmentNotFoundError("API environment was not found")
            if project is None or not access.resource_allowed(session, project, actor):
                raise EnvironmentNotFoundError("API environment was not found")

            services, public_variables, secrets = self._revision_state(
                repository, source_revision.id
            )
            headers = self._protect_headers(repository, environment, source_revision.default_headers, secrets, public_variables, actor)
            revision = repository.create_revision(
                environment.id,
                source_revision.source_revision_id,
                repository.next_revision_number(environment.id),
                source_revision.name,
                source_revision.description,
                headers,
                actor,
            )
            self._persist_services(repository, revision.id, services, actor)
            self._persist_public_variables(
                repository,
                revision.id,
                environment.id,
                public_variables,
                actor,
                scopes=self._public_variable_scopes(
                    repository,
                    source_revision.id,
                ),
            )
            for secret_name, secret in sorted(secrets.items()):
                repository.add_secret_variable(
                    revision.id, environment.id, secret_name, secret.id, actor
                )

            environment.name = source_revision.name
            environment.active_revision_id = revision.id
            environment.status = "active"
            environment.updated_by = actor
            repository.flush()
            return self._view(repository, environment, revision)

    def get_environment(self, environment_id):
        with self._session_factory() as session:
            repository = EnvironmentRepository(session)
            environment = repository.get_environment(environment_id)
            if environment is None or not environment.active_revision_id:
                raise EnvironmentNotFoundError("API environment was not found")
            revision = repository.get_revision(environment.active_revision_id)
            return self._view(repository, environment, revision)

    def list_assets(self, project_id, actor_id, status="active"):
        access.require_permission(actor_id, "api.view")
        project_identifier = _text(project_id, "project id")
        actor = _text(actor_id, "actor id")
        normalized_status = _text(status, "environment status")
        if normalized_status not in {"active", "archived", "all"}:
            raise EnvironmentInputError("environment status is invalid")
        with self._session_factory() as session:
            repository = EnvironmentRepository(session)
            project = repository.get_project(project_identifier)
            if project is None or not access.resource_allowed(session, project, actor):
                raise EnvironmentNotFoundError("API testing project was not found")
            assets = []
            for environment in repository.list_environments(
                project_identifier, normalized_status, actor
            ):
                if not environment.active_revision_id:
                    continue
                revision = repository.get_revision(environment.active_revision_id)
                if revision is None:
                    continue
                services, public_variables, secret_records = self._revision_state(
                    repository, revision.id
                )
                assets.append(
                    EnvironmentAssetView(
                        id=environment.id,
                        project_id=environment.project_id,
                        source_id=environment.source_id,
                        active_revision_id=revision.id,
                        source_revision_id=revision.source_revision_id,
                        revision=revision.revision_number,
                        name=revision.name,
                        description=revision.description,
                        status=environment.status,
                        service_count=len(services),
                        public_variable_count=len(public_variables),
                        secret_count=len(secret_records),
                        created_at=environment.created_at,
                        updated_at=environment.updated_at,
                    )
                )
            return tuple(assets)

    def list_revisions(self, environment_id, actor_id):
        access.require_permission(actor_id, "api.view")
        actor = _text(actor_id, "actor id")
        with self._session_factory() as session:
            repository = EnvironmentRepository(session)
            environment = repository.get_environment(environment_id)
            if environment is None:
                raise EnvironmentNotFoundError("API environment was not found")
            project = repository.get_project(environment.project_id)
            if not access.resource_allowed(session, environment, actor):
                raise EnvironmentNotFoundError("API environment was not found")
            if project is None or not access.resource_allowed(session, project, actor):
                raise EnvironmentNotFoundError("API environment was not found")
            return tuple(
                EnvironmentRevisionSummary(
                    id=revision.id,
                    environment_id=revision.environment_id,
                    source_revision_id=revision.source_revision_id,
                    revision=revision.revision_number,
                    name=revision.name,
                    description=revision.description,
                    status=revision.status,
                    created_at=revision.created_at,
                    updated_at=revision.updated_at,
                )
                for revision in repository.list_revisions(environment.id)
            )

    def archive(self, environment_id, actor_id):
        access.require_permission(actor_id, "api.environment")
        return self._set_status(environment_id, actor_id, "archived")

    def restore(self, environment_id, actor_id):
        access.require_permission(actor_id, "api.environment")
        return self._set_status(environment_id, actor_id, "active")

    def _set_status(self, environment_id, actor_id, status):
        actor = _text(actor_id, "actor id")
        with self._session_factory.begin() as session:
            repository = EnvironmentRepository(session)
            environment = repository.get_environment_for_update(environment_id)
            if environment is None:
                raise EnvironmentNotFoundError("API environment was not found")
            access.require_environment_configuration(session, environment, actor)
            project = repository.get_project(environment.project_id)
            if not access.resource_allowed(session, environment, actor):
                raise EnvironmentNotFoundError("API environment was not found")
            if project is None or not access.resource_allowed(session, project, actor):
                raise EnvironmentNotFoundError("API environment was not found")
            environment.status = status
            environment.updated_by = actor
            repository.flush()
            revision = repository.get_revision(environment.active_revision_id)
            if revision is None:
                raise EnvironmentNotFoundError(
                    "API environment active revision was not found"
                )
            services, public_variables, secret_records = self._revision_state(
                repository, revision.id
            )
            return EnvironmentAssetView(
                id=environment.id,
                project_id=environment.project_id,
                source_id=environment.source_id,
                active_revision_id=revision.id,
                source_revision_id=revision.source_revision_id,
                revision=revision.revision_number,
                name=revision.name,
                description=revision.description,
                status=environment.status,
                service_count=len(services),
                public_variable_count=len(public_variables),
                secret_count=len(secret_records),
                created_at=environment.created_at,
                updated_at=environment.updated_at,
            )

    def get_revision(self, revision_id):
        with self._session_factory() as session:
            repository = EnvironmentRepository(session)
            revision = repository.get_revision(revision_id)
            if revision is None:
                raise EnvironmentNotFoundError("API environment revision was not found")
            environment = repository.get_environment(revision.environment_id)
            return self._view(repository, environment, revision)

    def resolve_runtime(self, environment_revision_id, overrides, service_name=None):
        runtime_overrides = _normalize_public_variables(overrides)
        with self._session_factory() as session:
            repository = EnvironmentRepository(session)
            revision = repository.get_revision(environment_revision_id)
            if revision is None:
                raise EnvironmentNotFoundError("API environment revision was not found")
            environment = repository.get_environment(revision.environment_id)
            services, public_variables, secret_records = self._revision_state(
                repository, revision.id
            )
            secret_names = set(secret_records)
            forbidden = secret_names & set(runtime_overrides)
            if forbidden:
                raise SecretOverrideError(
                    "runtime override cannot replace secret variables: "
                    + ", ".join(sorted(forbidden))
                )
            secrets = {
                name: decrypt_secret(secret.ciphertext)
                for name, secret in secret_records.items()
            }

        raw_public = copy.deepcopy(public_variables)
        raw_public.update(runtime_overrides)
        resolver = _PlaceholderResolver({**raw_public, **secrets})
        configured_resolver = _PlaceholderResolver({**public_variables, **secrets})
        resolved_values = resolver.resolve_all()
        resolved_public = {name: resolved_values[name] for name in raw_public}
        resolved_secrets = {name: resolved_values[name] for name in secrets}
        base_urls = {}
        unresolved_services = []
        for name, item in services.items():
            if item["base_url"] is None:
                base_urls[name] = None
                unresolved_services.append(name)
                continue
            try:
                base_url = configured_resolver.render(item["base_url"])
                if not isinstance(base_url, str):
                    raise EnvironmentInputError("resolved service URL must be a string")
                base_urls[name] = _validate_service_url(base_url)
            except UnresolvedVariableError:
                base_urls[name] = None
                unresolved_services.append(name)
        headers = {name: resolver.render(value) for name, value in revision.default_headers.items()}
        if any(not isinstance(value, str) for value in headers.values()):
            raise EnvironmentInputError("resolved default header values must be strings")
        runtime = ResolvedEnvironment(
            revision_id=revision.id,
            environment_id=environment.id,
            name=revision.name,
            base_urls=base_urls,
            public_variables=resolved_public,
            secrets=resolved_secrets,
            headers=headers,
            service_metadata={
                name: copy.deepcopy(item["metadata"]) for name, item in services.items()
            },
            unresolved_services=tuple(sorted(unresolved_services)),
            _renderer=resolver.render,
        )
        if service_name is not None:
            runtime.base_url_for(_text(service_name, "service name"))
        return runtime

    @staticmethod
    def _protect_headers(repository, environment, headers, secrets, public_variables, actor, previous=None):
        protected = _normalize_headers(headers)
        previous = {name.lower(): value for name, value in (previous or {}).items()}
        for name, value in protected.items():
            if not SENSITIVE_HEADER.search(name):
                continue
            if "***" in value or "[redacted]" in value.lower():
                value = previous.get(name.lower())
                if value is None or "***" in value or "[redacted]" in value.lower():
                    raise EnvironmentInputError("redacted headers cannot be saved as credentials")
            if _header_is_template(name, value):
                protected[name] = value
                continue
            secret_name = "__header_" + hashlib.sha256(name.lower().encode()).hexdigest()[:24]
            if secret_name in public_variables:
                raise EnvironmentInputError("header secret name conflicts with a public variable")
            secrets[secret_name] = repository.create_secret(
                environment.project_id, environment.id, secret_name,
                encrypt_secret(value), secret_fingerprint(value), actor,
            )
            protected[name] = "{{" + secret_name + "}}"
        return protected

    @staticmethod
    def _persist_services(repository, revision_id, services, actor_id):
        access.require_environment_configuration(repository.session, None, actor_id, services=services)
        for service in services.values():
            repository.add_service(
                revision_id,
                service["name"],
                service["module_name"],
                service["base_url"] or "",
                service["metadata"],
                actor_id,
            )

    @staticmethod
    def _persist_public_variables(
        repository,
        revision_id,
        environment_id,
        variables,
        actor_id,
        *,
        scopes=None,
    ):
        scopes = scopes or {}
        for name, value in sorted(variables.items()):
            repository.add_public_variable(
                revision_id,
                environment_id,
                name,
                value,
                actor_id,
                scope=scopes.get(name, "environment"),
            )

    @staticmethod
    def _public_variable_scopes(repository, revision_id):
        return {
            row.name: str(row.scope or "environment")
            for row in repository.get_variables(revision_id)
            if not row.is_secret
        }

    @staticmethod
    def _revision_state(repository, revision_id):
        services = {
            item.service_name: {
                "name": item.service_name,
                "module_name": item.module_name,
                "base_url": item.base_url or None,
                "metadata": copy.deepcopy(item.metadata_json),
            }
            for item in repository.get_services(revision_id)
        }
        rows = repository.get_variables(revision_id)
        secret_by_id = repository.get_secrets(
            row.secret_value_id for row in rows if row.is_secret
        )
        public = {
            row.name: copy.deepcopy(row.value) for row in rows if not row.is_secret
        }
        secrets = {
            row.name: secret_by_id[row.secret_value_id]
            for row in rows
            if row.is_secret
        }
        return services, public, secrets

    @classmethod
    def _view(cls, repository, environment, revision):
        services, public_variables, secrets = cls._revision_state(
            repository, revision.id
        )
        variables = copy.deepcopy(public_variables)
        variables.update(
            {
                name: SecretVariableView(
                    name=name,
                    configured=True,
                    fingerprint=secret.fingerprint,
                    updated_at=secret.updated_at,
                )
                for name, secret in secrets.items()
            }
        )
        return EnvironmentView(
            id=environment.id,
            project_id=environment.project_id,
            source_id=environment.source_id,
            revision_id=revision.id,
            source_revision_id=revision.source_revision_id,
            revision=revision.revision_number,
            name=revision.name,
            description=revision.description,
            status=revision.status,
            services={
                name: EnvironmentServiceView(
                    name=name,
                    module_name=item["module_name"],
                    base_url=item["base_url"],
                    unresolved=item["base_url"] is None,
                    metadata=item["metadata"],
                )
                for name, item in services.items()
            },
            variables=variables,
            default_headers={name: "***" if SENSITIVE_HEADER.search(name) and not _header_is_template(name, value) else value
                             for name, value in revision.default_headers.items()},
            created_at=revision.created_at,
            updated_at=revision.updated_at,
        )
