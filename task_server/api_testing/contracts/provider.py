"""Public, token-free Apifox discovery contracts."""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ApifoxProject:
    id: str
    name: str
    description: str = ""
    team_name: str = ""


@dataclass(frozen=True)
class ApifoxBranch:
    id: str
    name: str
    is_default: bool = False


@dataclass(frozen=True)
class ApifoxEnvironmentService:
    name: str
    module_name: str
    base_url: Optional[str]
    provider_id: str = ""


@dataclass(frozen=True)
class ApifoxEnvironmentVariable:
    name: str
    value: str
    sensitive: bool
    scope: str = "environment"


@dataclass(frozen=True)
class ApifoxEnvironment:
    id: str
    name: str
    services: Tuple[ApifoxEnvironmentService, ...]
    variables: Tuple[ApifoxEnvironmentVariable, ...]


@dataclass(frozen=True)
class ApifoxProjectContext:
    project: ApifoxProject
    branches: Tuple[ApifoxBranch, ...]
    environments: Tuple[ApifoxEnvironment, ...]
    cli_version: str
