import os

from alembic import command
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.api_testing.models.provider import ApiProviderCredential
from task_server.api_testing.services.provider_service import (
    ProviderCredentialNotFoundError,
    ProviderService,
)
from tests.api_testing.test_migrations import (
    _alembic_config,
    _assert_current_test_schema,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


@pytest.fixture(scope="module")
def provider_database():
    database_url = _database_url()
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    with _without_database_environment():
        command.upgrade(_alembic_config(schema_url), "head")
    _assert_current_test_schema(schema_url, schema_name)
    try:
        yield schema_url
    finally:
        _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture(scope="module")
def session_factory(provider_database):
    engine = create_engine(provider_database, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv(
        "API_TESTING_SECRET_KEY",
        "provider-test-only-secret-key-9pR7xQ4mL2vN8cK5",
    )


@pytest.fixture()
def service(session_factory):
    return ProviderService(session_factory)


def test_apifox_token_is_encrypted_and_public_view_is_redacted(
    service, session_factory
):
    token = f"afxp_{os.urandom(12).hex()}"

    view = service.save_apifox_credential("admin", token, "admin")

    assert view.provider == "apifox"
    assert view.configured is True
    assert view.fingerprint
    assert token not in repr(view)
    assert token not in str(view)
    assert service.require_apifox_token("admin") == token
    with session_factory() as session:
        stored = session.scalar(
            select(ApiProviderCredential).where(
                ApiProviderCredential.owner_id == "admin",
                ApiProviderCredential.provider == "apifox",
            )
        )
        assert stored.ciphertext != token
        assert token not in stored.ciphertext


def test_apifox_credential_upsert_keeps_one_owner_scoped_record(
    service, session_factory
):
    first = f"afxp_{os.urandom(12).hex()}"
    second = f"afxp_{os.urandom(12).hex()}"

    service.save_apifox_credential("owner-upsert", first, "owner-upsert")
    updated = service.save_apifox_credential("owner-upsert", second, "owner-upsert")

    assert updated.fingerprint
    assert service.require_apifox_token("owner-upsert") == second
    with session_factory() as session:
        records = session.scalars(
            select(ApiProviderCredential).where(
                ApiProviderCredential.owner_id == "owner-upsert"
            )
        ).all()
        assert len(records) == 1


def test_apifox_credential_is_not_visible_to_another_owner(service):
    service.save_apifox_credential("owner-a", "afxp_owner_a_secret", "owner-a")

    assert service.get_apifox_credential("owner-b").configured is False
    with pytest.raises(ProviderCredentialNotFoundError):
        service.require_apifox_token("owner-b")
