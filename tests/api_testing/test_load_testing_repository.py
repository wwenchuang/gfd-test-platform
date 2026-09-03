"""Persistence contracts for the performance-testing domain."""

import os
from datetime import datetime, timezone

from alembic import command
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from task_server.api_testing.db import engine_for_url
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.load_testing import (
    ApiLoadAgent,
    ApiLoadMetricBucket,
    ApiLoadSample,
    ApiLoadScenario,
)
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.repositories.load_testing_repository import (
    InvalidLoadRunTransition,
    LoadTestingRepository,
)
from tests.api_testing.test_migrations import (
    _alembic_config,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


def _audit(actor="load-owner"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


@pytest.fixture(scope="module")
def load_factory():
    database_url = _database_url()
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    with _without_database_environment():
        command.upgrade(_alembic_config(schema_url), "head")
    engine = engine_for_url(schema_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture()
def load_records(load_factory):
    suffix = os.urandom(5).hex()
    with load_factory.begin() as session:
        project = ApiProject(
            name="load project " + suffix,
            slug="load-project-" + suffix,
            **_audit(),
        )
        session.add(project)
        session.flush()
        environment = ApiEnvironment(
            project_id=project.id,
            name="performance " + suffix,
            **_audit(),
        )
        session.add(environment)
        session.flush()
        environment_revision = ApiEnvironmentRevision(
            environment_id=environment.id,
            revision_number=1,
            name="performance " + suffix,
            **_audit(),
        )
        session.add(environment_revision)
        session.flush()
        environment.active_revision_id = environment_revision.id
        agent = ApiLoadAgent(
            name="load-agent-" + suffix,
            status="online",
            scheduling_tier="preferred",
            credential_hash="a" * 64,
            hard_limits={"max_vus": 100},
            soft_limits={"max_vus": 80},
            **_audit("admin"),
        )
        session.add(agent)
        session.flush()
        return {
            "project": project,
            "environment_revision": environment_revision,
            "agent": agent,
        }


@pytest.fixture()
def load_run_with_shard(load_factory, load_records):
    repository = LoadTestingRepository.from_factory(load_factory)
    scenario = repository.create_scenario(
        load_records["project"].id,
        "search workflow",
        "workflow",
        "load-owner",
    )
    version = repository.create_scenario_version(
        scenario.id,
        {
            "steps": [
                {
                    "id": "search",
                    "request": {"method": "GET", "path": "/search"},
                }
            ]
        },
        "compiler-v1",
        "load-owner",
    )
    run = repository.create_run(
        version.id,
        load_records["environment_revision"].id,
        {"executor": "constant-vus", "vus": 10, "duration_seconds": 30},
        "load-owner",
    )
    shard = repository.create_shard(
        run.id,
        load_records["agent"].id,
        0,
        {"vus": 10, "dataset_start": 0, "dataset_end": 10},
        "load-owner",
    )
    return repository, run, shard


def test_scenario_versions_are_immutable_numbered_snapshots(load_factory, load_records):
    repository = LoadTestingRepository.from_factory(load_factory)
    scenario = repository.create_scenario(
        load_records["project"].id,
        "model detail",
        "single_interface",
        "load-owner",
    )
    first = repository.create_scenario_version(
        scenario.id,
        {"steps": [{"id": "detail", "request": {"path": "/models/1"}}]},
        "compiler-v1",
        "load-owner",
    )
    second = repository.create_scenario_version(
        scenario.id,
        {"steps": [{"id": "detail", "request": {"path": "/models/2"}}]},
        "compiler-v1",
        "load-owner",
    )

    assert first.version_number == 1
    assert second.version_number == 2
    with load_factory() as session:
        persisted_scenario = session.get(ApiLoadScenario, scenario.id)
        assert persisted_scenario.active_version_id == second.id
    assert first.definition["steps"][0]["request"]["path"] == "/models/1"


def test_metric_bucket_upsert_is_idempotent(load_factory, load_run_with_shard):
    repository, run, shard = load_run_with_shard
    started_at = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)

    repository.upsert_metric_bucket(
        run.id, shard.id, "search", started_at, {"requests": 10, "p95_ms": 80}
    )
    repository.upsert_metric_bucket(
        run.id, shard.id, "search", started_at, {"requests": 12, "p95_ms": 95}
    )

    with load_factory() as session:
        buckets = tuple(
            session.scalars(
                select(ApiLoadMetricBucket).where(ApiLoadMetricBucket.run_id == run.id)
            )
        )
    assert len(buckets) == 1
    assert buckets[0].metrics == {"requests": 12, "p95_ms": 95}


def test_finished_run_cannot_transition_back_to_running(load_run_with_shard):
    repository, run, _ = load_run_with_shard
    repository.transition_run(run.id, ("draft",), "preflighting")
    repository.transition_run(run.id, ("preflighting",), "queued")
    repository.transition_run(run.id, ("queued",), "starting")
    repository.transition_run(run.id, ("starting",), "running")
    repository.transition_run(run.id, ("running",), "finished", summary={"requests": 20})

    with pytest.raises(InvalidLoadRunTransition):
        repository.transition_run(run.id, ("finished",), "running")


def test_samples_are_bounded_but_preserve_occurrence_count(load_factory, load_run_with_shard):
    repository, run, shard = load_run_with_shard
    for sequence in range(3):
        repository.append_bounded_sample(
            run.id,
            shard.id,
            "search",
            "http_error",
            {"sequence": sequence, "status_code": 503},
            limit=2,
        )

    with load_factory() as session:
        samples = tuple(
            session.scalars(
                select(ApiLoadSample)
                .where(
                    ApiLoadSample.run_id == run.id,
                    ApiLoadSample.shard_id == shard.id,
                    ApiLoadSample.scenario_step_id == "search",
                    ApiLoadSample.kind == "http_error",
                )
                .order_by(ApiLoadSample.created_at, ApiLoadSample.id)
            )
        )
        occurrences = session.scalar(
            select(func.coalesce(func.sum(ApiLoadSample.occurrence_count), 0)).where(
                ApiLoadSample.run_id == run.id,
                ApiLoadSample.shard_id == shard.id,
                ApiLoadSample.scenario_step_id == "search",
                ApiLoadSample.kind == "http_error",
            )
        )

    assert len(samples) == 2
    assert occurrences == 3
    assert [sample.payload["sequence"] for sample in samples] == [0, 1]
