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
    base_url: str
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


@dataclass(frozen=True)
class RenderedRequest:
    path: Any
    query: Any
    headers: Any
    body: Any


@dataclass(frozen=True, repr=False)
class ResolvedEnvironment:
    revision_id: str
    environment_id: str
    name: str
    base_urls: Mapping[str, str]
    public_variables: Mapping[str, Any]
    secrets: Mapping[str, str]
    headers: Mapping[str, str]
    service_metadata: Mapping[str, Mapping[str, Any]]
    _renderer: Callable[[Any], Any] = field(repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "base_urls", _frozen_mapping(self.base_urls))
        object.__setattr__(self, "public_variables", _frozen_mapping(self.public_variables))
        object.__setattr__(self, "secrets", _frozen_mapping(self.secrets))
        object.__setattr__(self, "headers", _frozen_mapping(self.headers))
        object.__setattr__(self, "service_metadata", _frozen_mapping(self.service_metadata))

    def __repr__(self):
        return (
            "ResolvedEnvironment(revision_id=%r, environment_id=%r, name=%r, "
            "base_urls=%r, public_variables=%r, secret_names=%r, header_names=%r)"
            % (
                self.revision_id,
                self.environment_id,
                self.name,
                dict(self.base_urls),
                dict(self.public_variables),
                tuple(sorted(self.secrets)),
                tuple(sorted(self.headers)),
            )
        )

    __str__ = __repr__

    def render(self, value):
        return self._renderer(copy.deepcopy(value))

    def render_request(self, *, path, query, headers, body):
        return RenderedRequest(
            path=self.render(path),
            query=self.render(query),
            headers=self.render(headers),
            body=self.render(body),
        )
