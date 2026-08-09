"""AI Gateway-backed generation of validated, editable API case drafts."""

import copy
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy import func, select

from ..contracts.case import CasePayloadError, CaseVersionView, parse_case_payload
from ..repositories.ai_job_repository import AiJobRepository
from .case_service import CaseService


MAX_ENDPOINTS = 60
DEFAULT_BATCH_SIZE = 10
DEFAULT_PROVIDER_ID = ""
DEFAULT_MODEL = ""
TERMINAL_JOB_STATES = frozenset(
    {"completed", "partial", "failed_validation", "failed_gateway"}
)
TERMINAL_BATCH_STATES = frozenset(
    {"completed", "failed_validation", "failed_gateway"}
)
SENSITIVE_KEY = re.compile(r"(?:token|secret|password|authorization|cookie|api[_-]?key)", re.IGNORECASE)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=._~-]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\bgAAAAA[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{48,}(?![A-Fa-f0-9])"),
    re.compile(
        r"(?<![A-Za-z0-9])(?=[A-Za-z0-9._~+/=@:-]{32,}(?![A-Za-z0-9]))"
        r"(?=[A-Za-z0-9._~+/=@:-]*[A-Za-z])(?=[A-Za-z0-9._~+/=@:-]*[0-9])"
        r"[A-Za-z0-9._~+/=@:-]{32,}(?![A-Za-z0-9])"
    ),
)
NAMED_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:password|api[_-]?key|token|cookie|(?:proxy[-_ ]?)?authorization)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}")
OMITTED_CONTRACT_FIELDS = frozenset({"example", "examples", "default"})


class AiJobNotFoundError(LookupError):
    pass


class AiJobInputError(ValueError):
    pass


class AiGatewayError(RuntimeError):
    pass


class AiCandidateValidationError(ValueError):
    pass


@dataclass(frozen=True)
class AiBatchView:
    id: str
    sequence: int
    state: str
    endpoint_ids: Tuple[str, ...]
    requested_provider_id: str
    requested_model: str
    actual_provider_id: str
    actual_model: str
    fallback_used: bool
    fallback_index: int
    fallback_reason: str
    generated_draft_ids: Tuple[str, ...]
    validation_errors: Tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        object.__setattr__(self, "endpoint_ids", tuple(self.endpoint_ids))
        object.__setattr__(self, "generated_draft_ids", tuple(self.generated_draft_ids))
        object.__setattr__(
            self,
            "validation_errors",
            tuple(MappingProxyType(copy.deepcopy(dict(item))) for item in self.validation_errors),
        )


@dataclass(frozen=True)
class AiJobView:
    id: str
    project_id: str
    environment_revision_id: str
    state: str
    endpoint_ids: Tuple[str, ...]
    requested_provider_id: str
    requested_model: str
    actual_provider_id: str
    actual_model: str
    fallback_used: bool
    summary: Mapping[str, Any]
    batches: Tuple[AiBatchView, ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "endpoint_ids", tuple(self.endpoint_ids))
        object.__setattr__(self, "summary", MappingProxyType(copy.deepcopy(dict(self.summary))))
        object.__setattr__(self, "batches", tuple(self.batches))


class AiGatewayClient:
    """Small default client for the dedicated API case-generation contract."""

    def __init__(self, base_url=None):
        self.base_url = str(
            base_url or os.getenv("AI_GATEWAY_URL", "http://127.0.0.1:8090")
        ).rstrip("/")

    def chat(self, *, messages, provider_id, model, timeout_seconds):
        payload = {
            "messages": copy.deepcopy(list(messages)),
            "providerId": provider_id,
            "model": model,
            "temperature": 0.2,
            "timeoutMs": int(timeout_seconds * 1000),
        }
        request = urllib.request.Request(
            self.base_url + "/ai/api-case-generation",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AiGatewayError(f"AI Gateway HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AiGatewayError("AI Gateway request failed") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AiGatewayError("AI Gateway returned invalid JSON") from exc
        if not isinstance(parsed, dict) or parsed.get("success") is not True:
            raise AiGatewayError("AI Gateway returned an unsuccessful response")
        return parsed


class _BoundSessionFactory:
    """Expose one outer transaction through the CaseService session protocol."""

    def __init__(self, session):
        self.session = session

    def begin(self):
        return nullcontext(self.session)

    def __call__(self):
        return nullcontext(self.session)


class AiCaseService:
    def __init__(
        self,
        session_factory,
        *,
        gateway_client=None,
        batch_size=DEFAULT_BATCH_SIZE,
        gateway_timeout_seconds=120,
    ):
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_ENDPOINTS:
            raise ValueError("AI batch size must be between 1 and 60")
        self.session_factory = session_factory
        self.gateway_client = gateway_client or AiGatewayClient()
        self.batch_size = batch_size
        self.gateway_timeout_seconds = gateway_timeout_seconds
        root = Path(__file__).resolve().parents[3]
        self.skill_text = (root / "ai_skills" / "api_case_generation.v1.md").read_text(
            encoding="utf-8"
        )
        self.output_schema = json.loads(
            (root / "ai_skills" / "schemas" / "api_case_generation.v1.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(self.output_schema)
        self.output_validator = Draft202012Validator(self.output_schema)

    def submit(
        self,
        endpoint_ids,
        environment_revision_id,
        actor_id,
        model_config=None,
        intent="",
    ):
        identifiers = self._endpoint_ids(endpoint_ids)
        actor = self._text(actor_id, "actor id", 128)
        intent_text = self._text(intent, "intent", 10_000, allow_empty=True)
        provider_id, model = self._model_config(model_config)
        with self.session_factory.begin() as session:
            repository = AiJobRepository(session)
            endpoints = repository.get_endpoints(identifiers)
            if len(endpoints) != len(identifiers):
                raise AiJobInputError("all selected API endpoints must exist")
            ordered_endpoints = [endpoints[item] for item in identifiers]
            revision_ids = {item.revision_id for item in ordered_endpoints}
            if len(revision_ids) != 1:
                raise AiJobInputError("selected endpoints must share one source revision")
            source_revision = repository.get_source_revision(next(iter(revision_ids)))
            source = repository.get_source(source_revision.source_id) if source_revision else None
            if (
                source is None
                or source_revision.status != "active"
                or source.active_revision_id != source_revision.id
            ):
                raise AiJobInputError("selected endpoints must belong to the active source revision")
            environment_revision = repository.get_environment_revision(
                environment_revision_id
            )
            environment = (
                repository.get_environment(environment_revision.environment_id)
                if environment_revision
                else None
            )
            if environment is None or environment.project_id != source.project_id:
                raise AiJobInputError("environment and endpoints must belong to the same project")
            job = repository.create_job(
                project_id=source.project_id,
                environment_revision_id=environment_revision.id,
                endpoint_ids=identifiers,
                requested_provider_id=provider_id,
                requested_model=model,
                intent=intent_text,
                actor_id=actor,
            )
            for sequence, offset in enumerate(range(0, len(identifiers), self.batch_size), 1):
                repository.create_batch(
                    job_id=job.id,
                    sequence=sequence,
                    endpoint_ids=identifiers[offset : offset + self.batch_size],
                    requested_provider_id=provider_id,
                    requested_model=model,
                    actor_id=actor,
                )
            return self._job_view(repository, job)

    def process(self, job_id):
        lock_session = self.session_factory()
        lock_key = self._advisory_lock_key(job_id)
        acquired = False
        try:
            acquired = bool(
                lock_session.scalar(select(func.pg_try_advisory_lock(lock_key)))
            )
            if not acquired:
                repository = AiJobRepository(lock_session)
                job = repository.get_job(job_id)
                if job is None:
                    raise AiJobNotFoundError("AI case generation job was not found")
                return self._job_view(repository, job)
            return self._process_with_lock(job_id)
        finally:
            if acquired:
                lock_session.scalar(select(func.pg_advisory_unlock(lock_key)))
            lock_session.close()

    def _process_with_lock(self, job_id):
        with self.session_factory.begin() as session:
            repository = AiJobRepository(session)
            job = repository.get_job_for_update(job_id)
            if job is None:
                raise AiJobNotFoundError("AI case generation job was not found")
            if job.state in TERMINAL_JOB_STATES:
                return self._job_view(repository, job)
            summary = copy.deepcopy(job.summary)
            if job.state == "running":
                for batch in repository.list_batches(job.id):
                    if batch.state == "running":
                        repository.update_batch(
                            batch,
                            state="queued",
                            actual_model=batch.actual_model,
                            result=batch.result,
                            error=batch.error,
                            actor_id=job.updated_by,
                        )
            repository.update_job(
                job,
                state="running",
                actual_model=job.actual_model,
                summary=summary,
                actor_id=job.updated_by,
            )
            actor_id = job.updated_by
            batch_ids = [item.id for item in repository.list_batches(job.id)]

        for batch_id in batch_ids:
            try:
                self._process_batch(job_id, batch_id, actor_id)
            except Exception as exc:
                self._finish_failed_batch(
                    batch_id,
                    "failed_validation",
                    "worker_error",
                    self._safe_error(exc),
                    actor_id,
                )
        return self._finalize(job_id, actor_id)

    def list_generated_drafts(self, job_id):
        with self.session_factory() as session:
            repository = AiJobRepository(session)
            job = repository.get_job(job_id)
            if job is None:
                raise AiJobNotFoundError("AI case generation job was not found")
            version_ids = []
            for batch in repository.list_batches(job.id):
                version_ids.extend(batch.result.get("draft_version_ids", []))
            version_ids = list(dict.fromkeys(version_ids))
        case_service = CaseService(self.session_factory)
        return tuple(case_service.get_version(item) for item in version_ids)

    def get_job(self, job_id):
        with self.session_factory() as session:
            repository = AiJobRepository(session)
            job = repository.get_job(job_id)
            if job is None:
                raise AiJobNotFoundError("AI case generation job was not found")
            return self._job_view(repository, job)

    def _process_batch(self, job_id, batch_id, actor_id):
        with self.session_factory.begin() as session:
            repository = AiJobRepository(session)
            batch = repository.get_batch_for_update(batch_id)
            if batch is None or batch.job_id != job_id:
                raise AiJobNotFoundError("AI case generation batch was not found")
            if batch.state in TERMINAL_BATCH_STATES or batch.state == "running":
                return
            repository.update_batch(
                batch,
                state="running",
                actual_model=batch.actual_model,
                result=batch.result,
                error={},
                actor_id=actor_id,
            )
            prompt, requested_provider_id, requested_model = self._batch_prompt(
                repository, job_id, batch
            )

        try:
            response = self.gateway_client.chat(
                messages=prompt,
                provider_id=requested_provider_id,
                model=requested_model,
                timeout_seconds=self.gateway_timeout_seconds,
            )
            evidence = self._model_evidence(
                response, requested_provider_id, requested_model
            )
        except (AiGatewayError, TimeoutError, OSError) as exc:
            self._finish_failed_batch(
                batch_id,
                "failed_gateway",
                "gateway_error",
                self._safe_error(exc),
                actor_id,
            )
            return
        try:
            candidates = self._parse_output(response)
        except AiCandidateValidationError as exc:
            self._finish_failed_batch(
                batch_id,
                "failed_validation",
                "output_validation_error",
                self._safe_error(exc),
                actor_id,
                evidence=evidence,
            )
            return

        errors = []
        allowed_endpoint_ids = set(batch.endpoint_ids)
        for index, candidate in enumerate(candidates):
            fingerprint = self._candidate_fingerprint(candidate)
            try:
                if candidate["endpoint_id"] not in allowed_endpoint_ids:
                    raise AiCandidateValidationError(
                        "candidate endpoint is outside the current batch"
                    )
                self._create_validated_draft(
                    candidate["endpoint_id"],
                    candidate["case"],
                    job_id,
                    batch_id,
                    fingerprint,
                    actor_id,
                )
            except (AiCandidateValidationError, CasePayloadError, ValueError, LookupError) as exc:
                errors.append(
                    {
                        "candidate_index": index,
                        "endpoint_id": candidate.get("endpoint_id", ""),
                        "code": "candidate_validation_error",
                        "message": self._safe_error(exc),
                        "candidate_fingerprint": fingerprint,
                    }
                )

        with self.session_factory.begin() as session:
            repository = AiJobRepository(session)
            batch = repository.get_batch_for_update(batch_id)
            result = copy.deepcopy(batch.result)
            known_errors = {
                item.get("candidate_fingerprint")
                for item in result.get("validation_errors", [])
                if item.get("candidate_fingerprint")
            }
            result["validation_errors"] = list(
                result.get("validation_errors", [])
            ) + [
                item
                for item in errors
                if item["candidate_fingerprint"] not in known_errors
            ]
            result["model_evidence"] = evidence
            generated_ids = list(dict.fromkeys(result.get("draft_version_ids", [])))
            result["draft_version_ids"] = generated_ids
            state = "completed" if generated_ids else "failed_validation"
            repository.update_batch(
                batch,
                state=state,
                actual_model=evidence["actual_model"],
                result=result,
                error={} if state == "completed" else {"code": "candidate_validation_error"},
                actor_id=actor_id,
            )

    def _batch_prompt(self, repository, job_id, batch):
        job = repository.get_job(job_id)
        endpoints = repository.get_endpoints(batch.endpoint_ids)
        variables = repository.get_environment_variables(job.environment_revision_id)
        services = repository.get_environment_services(job.environment_revision_id)
        environment = {
            "variable_names": [
                self._sanitize_contract(item.name)
                for item in variables
                if item.enabled and not item.is_secret
            ],
            "services": [
                {
                    "name": self._sanitize_contract(item.service_name),
                    "resolved": bool(item.base_url),
                }
                for item in services
            ],
        }
        contracts = []
        for endpoint_id in batch.endpoint_ids:
            endpoint = endpoints[endpoint_id]
            contracts.append(
                {
                    "endpoint_id": endpoint.id,
                    "operation_id": self._sanitize_contract(endpoint.operation_id),
                    "method": endpoint.method,
                    "path": self._sanitize_contract(endpoint.path),
                    "summary": self._sanitize_contract(endpoint.summary),
                    "tags": self._sanitize_contract(list(endpoint.tags)),
                    "operation": self._sanitize_contract(endpoint.operation),
                }
            )
        payload = {
            "intent": self._sanitize_contract(job.summary.get("intent", "")),
            "endpoints": contracts,
            "environment": environment,
            "output_schema": self.output_schema,
        }
        messages = [
            {"role": "system", "content": self.skill_text},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        return (
            messages,
            str(batch.result.get("requested_provider_id", DEFAULT_PROVIDER_ID)),
            batch.requested_model,
        )

    def _create_validated_draft(
        self, endpoint_id, payload, job_id, batch_id, fingerprint, actor_id
    ):
        self._assert_no_literal_secrets(payload)
        parsed = parse_case_payload(payload)
        request_path = parsed["request"]["path"]
        parsed_path = urlsplit(request_path)
        if parsed_path.scheme or parsed_path.netloc or request_path.startswith("//"):
            raise AiCandidateValidationError("AI case request path must be relative")
        with self.session_factory.begin() as session:
            repository = AiJobRepository(session)
            batch = repository.get_batch_for_update(batch_id)
            if batch is None or batch.job_id != job_id:
                raise AiJobNotFoundError("AI case generation batch was not found")
            result = copy.deepcopy(batch.result)
            checkpoints = dict(result.get("candidate_checkpoints", {}))
            if fingerprint in checkpoints:
                return str(checkpoints[fingerprint])
            bound_service = CaseService(_BoundSessionFactory(session))
            draft = bound_service.create_draft(endpoint_id, parsed, "ai", actor_id)
            job = repository.get_job(job_id)
            metadata = self._environment_metadata(
                repository, job.environment_revision_id
            )
            validation = bound_service.validate_case(draft.id, metadata)
            if not validation.valid:
                details = "; ".join(
                    f"{item.code}:{item.field}" for item in validation.errors[:5]
                )
                raise AiCandidateValidationError(
                    "deterministic case validation failed: " + details
                )
            checkpoints[fingerprint] = draft.id
            draft_ids = list(dict.fromkeys(result.get("draft_version_ids", []) + [draft.id]))
            result["candidate_checkpoints"] = checkpoints
            result["draft_version_ids"] = draft_ids
            repository.update_batch(
                batch,
                state="running",
                actual_model=batch.actual_model,
                result=result,
                error=batch.error,
                actor_id=actor_id,
            )
            return draft.id

    def _finish_failed_batch(
        self, batch_id, state, code, message, actor_id, *, evidence=None
    ):
        with self.session_factory.begin() as session:
            repository = AiJobRepository(session)
            batch = repository.get_batch_for_update(batch_id)
            result = copy.deepcopy(batch.result)
            errors = list(result.get("validation_errors", []))
            if state == "failed_validation":
                errors.append(
                    {
                        "candidate_index": 0,
                        "endpoint_id": "",
                        "code": code,
                        "message": message,
                    }
                )
                result["validation_errors"] = errors
            if evidence:
                result["model_evidence"] = copy.deepcopy(evidence)
            repository.update_batch(
                batch,
                state=state,
                actual_model=(evidence or {}).get("actual_model", batch.actual_model),
                result=result,
                error={"code": code, "message": message},
                actor_id=actor_id,
            )

    def _finalize(self, job_id, actor_id):
        with self.session_factory.begin() as session:
            repository = AiJobRepository(session)
            job = repository.get_job_for_update(job_id)
            batches = repository.list_batches(job.id)
            draft_count = sum(
                len(item.result.get("draft_version_ids", [])) for item in batches
            )
            invalid_count = sum(
                len(item.result.get("validation_errors", [])) for item in batches
            )
            gateway_failures = sum(item.state == "failed_gateway" for item in batches)
            if draft_count and (invalid_count or gateway_failures):
                state = "partial"
            elif draft_count:
                state = "completed"
            elif gateway_failures:
                state = "failed_gateway"
            else:
                state = "failed_validation"
            evidence = [
                item.result.get("model_evidence", {})
                for item in batches
                if item.result.get("model_evidence")
            ]
            actual_providers = {
                item.get("actual_provider_id", "") for item in evidence if item.get("actual_provider_id")
            }
            actual_models = {
                item.get("actual_model", "") for item in evidence if item.get("actual_model")
            }
            actual_provider_id = self._single_or_mixed(actual_providers)
            actual_model = self._single_or_mixed(actual_models)
            summary = copy.deepcopy(job.summary)
            summary.update(
                {
                    "generated_drafts": draft_count,
                    "invalid_candidates": invalid_count,
                    "gateway_failures": gateway_failures,
                    "actual_provider_id": actual_provider_id,
                    "actual_models": sorted(actual_models),
                    "fallback_used": any(
                        bool(item.get("fallback_used")) for item in evidence
                    ),
                }
            )
            repository.update_job(
                job,
                state=state,
                actual_model=actual_model,
                summary=summary,
                actor_id=actor_id,
            )
            return self._job_view(repository, job)

    @staticmethod
    def _environment_metadata(repository, revision_id):
        variables = repository.get_environment_variables(revision_id)
        services = repository.get_environment_services(revision_id)
        return {
            "variables": {
                item.name: {"configured": True, "secret": bool(item.is_secret)}
                for item in variables
                if item.enabled
            },
            "services": {
                item.service_name: {"resolved": bool(item.base_url)} for item in services
            },
        }

    def _parse_output(self, response):
        if not isinstance(response, dict) or response.get("success") is not True:
            raise AiGatewayError("AI Gateway returned an unsuccessful response")
        content = response.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AiCandidateValidationError("AI Gateway content must be non-empty JSON")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AiCandidateValidationError("AI Gateway content is not strict JSON") from exc
        try:
            self.output_validator.validate(payload)
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "$"
            raise AiCandidateValidationError(
                f"AI output schema validation failed at {location}: {exc.validator}"
            ) from None
        candidates = payload["candidates"]
        normalized = []
        for candidate in candidates:
            normalized.append(
                {
                    "endpoint_id": candidate["endpoint_id"],
                    "case": copy.deepcopy(candidate["case"]),
                }
            )
        return normalized

    @staticmethod
    def _model_evidence(response, requested_provider_id, requested_model):
        actual_provider_id = response.get("providerId")
        actual_model = response.get("model")
        fallback_used = response.get("fallbackUsed", False)
        fallback_index = response.get("fallbackIndex", 0)
        fallback_reason = response.get("fallbackReason", "")
        if not isinstance(actual_provider_id, str) or not actual_provider_id:
            raise AiGatewayError("AI Gateway omitted provider evidence")
        if not isinstance(actual_model, str) or not actual_model:
            raise AiGatewayError("AI Gateway omitted model evidence")
        if (
            AiCaseService._redact_text(actual_provider_id) != actual_provider_id
            or AiCaseService._redact_text(actual_model) != actual_model
        ):
            raise AiGatewayError("AI Gateway returned unsafe model evidence")
        if (
            not isinstance(fallback_used, bool)
            or not isinstance(fallback_index, int)
            or isinstance(fallback_index, bool)
            or fallback_index < 0
            or not isinstance(fallback_reason, str)
        ):
            raise AiGatewayError("AI Gateway returned invalid fallback evidence")
        safe_reason = AiCaseService._safe_error(fallback_reason) if fallback_reason else ""
        if fallback_used:
            if fallback_index <= 0 or not fallback_reason.strip():
                raise AiGatewayError("AI Gateway returned incomplete fallback evidence")
        elif fallback_index != 0 or fallback_reason:
            raise AiGatewayError("AI Gateway returned contradictory fallback evidence")
        explicit_selection = bool(requested_provider_id or requested_model)
        changed = (
            (bool(requested_provider_id) and actual_provider_id != requested_provider_id)
            or (bool(requested_model) and actual_model != requested_model)
        )
        if explicit_selection and changed != fallback_used:
            raise AiGatewayError(
                "AI Gateway fallback evidence contradicts the selected model"
            )
        return {
            "requested_provider_id": requested_provider_id,
            "requested_model": requested_model,
            "actual_provider_id": actual_provider_id,
            "actual_model": actual_model,
            "fallback_used": fallback_used,
            "fallback_index": fallback_index,
            "fallback_reason": safe_reason,
        }

    @classmethod
    def _sanitize_contract(cls, value, key=""):
        if isinstance(value, dict):
            output = {}
            for raw_key, item in value.items():
                name = str(raw_key)
                if name in OMITTED_CONTRACT_FIELDS:
                    continue
                if SENSITIVE_KEY.search(name) and not isinstance(item, (dict, list)):
                    output[name] = "<redacted>"
                else:
                    output[name] = cls._sanitize_contract(item, name)
            return output
        if isinstance(value, list):
            return [cls._sanitize_contract(item, key) for item in value]
        if isinstance(value, str):
            return cls._redact_text(value)
        return copy.deepcopy(value)

    @classmethod
    def _redact_text(cls, value):
        text = str(value)
        for pattern in SENSITIVE_VALUE_PATTERNS:
            text = pattern.sub("<redacted>", text)
        text = NAMED_CREDENTIAL_PATTERN.sub("<redacted>", text)
        return text

    @classmethod
    def _safe_error(cls, error):
        text = str(error).replace("\r", " ").replace("\n", " ").strip()
        text = cls._redact_text(text)
        if len(text) >= 24 and re.fullmatch(r"[A-Za-z0-9._~+/=@:-]+", text):
            text = "<redacted>"
        return text[:500] or error.__class__.__name__

    @classmethod
    def _assert_no_literal_secrets(cls, value, path="case"):
        request = value.get("request", {}) if isinstance(value, dict) else {}
        headers = request.get("headers", {}) if isinstance(request, dict) else {}
        for name, item in headers.items():
            if SENSITIVE_KEY.search(str(name)) and cls._is_nonempty(item):
                cls._require_full_placeholder(item, f"{path}.request.headers.{name}")
        cookies = request.get("cookies", {}) if isinstance(request, dict) else {}
        for name, item in cookies.items():
            if cls._is_nonempty(item):
                cls._require_full_placeholder(item, f"{path}.request.cookies.{name}")
        cls._assert_sensitive_mapping(
            request.get("body") if isinstance(request, dict) else None,
            f"{path}.request.body",
        )
        for index, row in enumerate(value.get("data_rows", [])):
            cls._assert_sensitive_mapping(
                row.get("values"), f"{path}.data_rows[{index}].values"
            )
        processing = value.get("processing", {})
        for phase in ("pre", "post"):
            for index, action in enumerate(processing.get(phase, [])):
                if (
                    action.get("action") == "set_variable"
                    and SENSITIVE_KEY.search(str(action.get("name", "")))
                    and cls._is_nonempty(action.get("value"))
                ):
                    cls._require_full_placeholder(
                        action.get("value"),
                        f"{path}.processing.{phase}[{index}].value",
                    )
        cls._assert_no_credential_shapes(value, path)

    @classmethod
    def _assert_no_credential_shapes(cls, value, path):
        if isinstance(value, dict):
            for raw_key, item in value.items():
                key = str(raw_key)
                cls._assert_no_credential_shapes(item, f"{path}.{key}")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                cls._assert_no_credential_shapes(item, f"{path}[{index}]")
            return
        if not isinstance(value, str) or PLACEHOLDER_PATTERN.fullmatch(value.strip()):
            return
        if cls._redact_text(value) != value:
            raise AiCandidateValidationError(
                f"literal credential is not allowed at {path}; use a variable placeholder"
            )

    @classmethod
    def _assert_sensitive_mapping(cls, value, path):
        if isinstance(value, dict):
            for raw_key, item in value.items():
                key = str(raw_key)
                item_path = f"{path}.{key}"
                if SENSITIVE_KEY.search(key) and cls._is_nonempty(item):
                    cls._require_full_placeholder(item, item_path)
                else:
                    cls._assert_sensitive_mapping(item, item_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                cls._assert_sensitive_mapping(item, f"{path}[{index}]")

    @staticmethod
    def _is_nonempty(value):
        return value not in (None, "", [], {})

    @staticmethod
    def _require_full_placeholder(value, path):
        if not isinstance(value, str) or not PLACEHOLDER_PATTERN.fullmatch(value.strip()):
            raise AiCandidateValidationError(
                f"sensitive value at {path} must use a complete variable placeholder"
            )

    @staticmethod
    def _candidate_fingerprint(candidate):
        canonical = json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _advisory_lock_key(job_id):
        digest = hashlib.sha256(str(job_id).encode("utf-8")).digest()[:8]
        return int.from_bytes(digest, byteorder="big", signed=True)

    @staticmethod
    def _single_or_mixed(values):
        if not values:
            return ""
        return next(iter(values)) if len(values) == 1 else "mixed"

    @staticmethod
    def _text(value, label, maximum, *, allow_empty=False):
        if not isinstance(value, str):
            raise AiJobInputError(f"{label} must be a string")
        normalized = value.strip()
        if not allow_empty and not normalized:
            raise AiJobInputError(f"{label} must not be empty")
        if len(normalized) > maximum:
            raise AiJobInputError(f"{label} is too long")
        return normalized

    @classmethod
    def _endpoint_ids(cls, endpoint_ids):
        if not isinstance(endpoint_ids, (list, tuple)):
            raise AiJobInputError("endpoint_ids must be an array")
        identifiers = []
        seen = set()
        for value in endpoint_ids:
            identifier = cls._text(value, "endpoint id", 36)
            if identifier not in seen:
                seen.add(identifier)
                identifiers.append(identifier)
        if not identifiers:
            raise AiJobInputError("at least one endpoint is required")
        if len(identifiers) > MAX_ENDPOINTS:
            raise AiJobInputError("endpoint_ids must contain at most 60 entries")
        return tuple(identifiers)

    @classmethod
    def _model_config(cls, model_config):
        if model_config is None:
            return DEFAULT_PROVIDER_ID, DEFAULT_MODEL
        if not isinstance(model_config, dict):
            raise AiJobInputError("model_config must be an object")
        unknown = set(model_config) - {"providerId", "model"}
        if unknown:
            raise AiJobInputError("model_config contains unsupported fields")
        provider_id = cls._text(
            model_config.get("providerId", DEFAULT_PROVIDER_ID),
            "providerId",
            200,
            allow_empty=True,
        )
        model = cls._text(
            model_config.get("model", DEFAULT_MODEL),
            "model",
            200,
            allow_empty=True,
        )
        return provider_id, model

    @classmethod
    def _batch_view(cls, batch):
        result = batch.result if isinstance(batch.result, dict) else {}
        evidence = result.get("model_evidence", {})
        return AiBatchView(
            id=batch.id,
            sequence=batch.sequence,
            state=batch.state,
            endpoint_ids=tuple(batch.endpoint_ids),
            requested_provider_id=str(
                result.get("requested_provider_id", evidence.get("requested_provider_id", ""))
            ),
            requested_model=batch.requested_model,
            actual_provider_id=str(evidence.get("actual_provider_id", "")),
            actual_model=batch.actual_model,
            fallback_used=bool(evidence.get("fallback_used", False)),
            fallback_index=int(evidence.get("fallback_index", 0)),
            fallback_reason=str(evidence.get("fallback_reason", "")),
            generated_draft_ids=tuple(result.get("draft_version_ids", [])),
            validation_errors=tuple(result.get("validation_errors", [])),
        )

    @classmethod
    def _job_view(cls, repository, job):
        summary = job.summary if isinstance(job.summary, dict) else {}
        batches = tuple(cls._batch_view(item) for item in repository.list_batches(job.id))
        return AiJobView(
            id=job.id,
            project_id=job.project_id,
            environment_revision_id=job.environment_revision_id,
            state=job.state,
            endpoint_ids=tuple(job.endpoint_ids),
            requested_provider_id=str(summary.get("requested_provider_id", "")),
            requested_model=job.requested_model,
            actual_provider_id=str(summary.get("actual_provider_id", "")),
            actual_model=job.actual_model,
            fallback_used=bool(summary.get("fallback_used", False)),
            summary=summary,
            batches=batches,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
