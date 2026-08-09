"""Public contracts for editable API environment revisions."""

from dataclasses import dataclass, field
from datetime import datetime
import copy
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Tuple


def _frozen_mapping(value):
    return MappingProxyType(copy.deepcopy(dict(value)))


@dataclass(frozen=True)
class SecretVariableView:
    name: str
    configured: bool
    fingerprint: Optional[str]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class EnvironmentServiceView:
    name: str
    module_name: str
    base_url: Optional[str]
    unresolved: bool
    metadata: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True)
class EnvironmentView:
    id: str
    project_id: str
    source_id: Optional[str]
    revision_id: str
    source_revision_id: Optional[str]
    revision: int
    name: str
    description: str
    status: str
    services: Mapping[str, EnvironmentServiceView]
    variables: Mapping[str, Any]
    default_headers: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "services", MappingProxyType(dict(self.services)))
        object.__setattr__(self, "variables", _frozen_mapping(self.variables))
        object.__setattr__(self, "default_headers", _frozen_mapping(self.default_headers))


@dataclass(frozen=True, repr=False)
class RenderedRequest:
    path: Any
    query: Any
    headers: Any
    body: Any

    def __repr__(self):
        return "RenderedRequest(path=<redacted>, query=<redacted>, headers=<redacted>, body=<redacted>)"

    __str__ = __repr__


class UnresolvedServiceError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class ResolvedEnvironment:
    revision_id: str
    environment_id: str
    name: str
    base_urls: Mapping[str, Optional[str]]
    public_variables: Mapping[str, Any]
    secrets: Mapping[str, str]
    headers: Mapping[str, str]
    service_metadata: Mapping[str, Mapping[str, Any]]
    unresolved_services: Tuple[str, ...]
    _renderer: Callable[[Any], Any] = field(repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "base_urls", _frozen_mapping(self.base_urls))
        object.__setattr__(self, "public_variables", _frozen_mapping(self.public_variables))
        object.__setattr__(self, "secrets", _frozen_mapping(self.secrets))
        object.__setattr__(self, "headers", _frozen_mapping(self.headers))
        object.__setattr__(self, "service_metadata", _frozen_mapping(self.service_metadata))
        object.__setattr__(self, "unresolved_services", tuple(self.unresolved_services))

    def __repr__(self):
        return (
            "ResolvedEnvironment(revision_id=%r, environment_id=%r, name=%r, "
            "service_count=%d, public_variable_count=%d, secret_count=%d, "
            "header_count=%d, unresolved_service_count=%d)"
            % (
                self.revision_id,
                self.environment_id,
                self.name,
                len(self.base_urls),
                len(self.public_variables),
                len(self.secrets),
                len(self.headers),
                len(self.unresolved_services),
            )
        )

    __str__ = __repr__

    def render(self, value):
        return self._renderer(copy.deepcopy(value))

    def base_url_for(self, service_name):
        if service_name not in self.base_urls:
            raise UnresolvedServiceError(f"environment service is not defined: {service_name}")
        value = self.base_urls[service_name]
        if value is None:
            raise UnresolvedServiceError(f"environment service URL is unresolved: {service_name}")
        return value

    def render_request(self, *, path, query, headers, body):
        return RenderedRequest(
            path=self.render(path),
            query=self.render(query),
            headers=self.render(headers),
            body=self.render(body),
        )
