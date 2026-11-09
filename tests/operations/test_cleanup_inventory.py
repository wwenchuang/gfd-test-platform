import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / 'scripts' / 'audit_cleanup_compatibility.py'


def load_module():
    assert PATH.exists(), 'cleanup compatibility inventory script is required'
    spec = importlib.util.spec_from_file_location('cleanup_inventory', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classification_keeps_only_structure_not_values():
    module = load_module()
    secret = 'sensitive-value-never-output'
    processing = {
        'pre': [{'action': 'copy_variable', 'source': secret, 'target': 'target'}],
        'cleanup_steps': [
            {'enabled': True, 'only_if_variable': secret,
             'polling': {'max_attempts': 2},
             'extractions': [{'target': secret, 'required': False, 'default': secret}]},
            {'enabled': True, 'request': {'body': secret}},
            {'enabled': False},
        ],
    }
    result = module.classify(processing)
    assert result == {'multi_cleanup': True, 'earlier_output': True,
                      'optional_or_default': True, 'conditional': True,
                      'pre_alias': True, 'polling': True, 'indeterminate': False}
    assert secret not in str(result)


def test_malformed_processing_is_explicitly_indeterminate():
    module = load_module()
    assert module.classify({'cleanup_steps': {'oops': 1}})['indeterminate'] is True


def test_single_or_disabled_steps_do_not_indicate_downstream_output():
    module = load_module()
    result = module.classify({'cleanup_steps': [{'extractions': [{'target': 'x'}]}, {'enabled': False}]})
    assert not result['multi_cleanup']
    assert not result['earlier_output']


def test_query_scope_is_a_conservative_superset_and_read_only():
    module = load_module()
    sql = ' '.join(module.INVENTORY_SQL.lower().split())
    assert "b.status = 'active'" in sql
    assert 'j.enabled' in sql
    assert 'c.project_id' in sql
    assert sql.startswith('select ')
    assert not any(x in sql for x in ('delete ', 'update ', 'insert '))


def test_real_postgresql_scope_and_output_are_read_only():
    import json
    import os
    import psycopg
    dsn = os.environ.get('CLEANUP_INVENTORY_TEST_DSN')
    if not dsn:
        pytest.skip('requires an isolated temporary PostgreSQL database')
    module = load_module()
    with psycopg.connect(dsn) as connection:
        result = module.inventory(connection)
        assert result['count'] == 3
        ids = {row['case_version_id'] for row in result['versions']}
        assert ids == {'00000000-0000-0000-0000-000000000001',
                       '00000000-0000-0000-0000-000000000002',
                       '00000000-0000-0000-0000-000000000003'}
        assert 'NEVER_PRINT_SECRET' not in json.dumps(result)
        flagged = next(row for row in result['versions'] if row['case_version_id'].endswith('000000000001'))
        assert flagged['earlier_output'] and flagged['multi_cleanup']
        assert result['exact_schedule_membership_resolved'] is False


def test_inventory_transaction_actually_rejects_writes():
    import os
    from contextlib import contextmanager
    import psycopg
    dsn = os.environ.get('CLEANUP_INVENTORY_TEST_DSN')
    if not dsn:
        pytest.skip('requires an isolated temporary PostgreSQL database')
    module = load_module()
    with psycopg.connect(dsn) as real:
        class ProbeConnection:
            def transaction(self):
                return real.transaction()
            def execute(self, sql):
                return real.execute(sql)
            @contextmanager
            def cursor(self, **kwargs):
                assert real.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
                real.execute('SAVEPOINT probe_write')
                with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                    real.execute("INSERT INTO api_cases VALUES ('should-not-exist', 'probe')")
                real.execute('ROLLBACK TO SAVEPOINT probe_write')
                with real.cursor(**kwargs) as cursor:
                    yield cursor
        result = module.inventory(ProbeConnection())
        assert result['count'] == 3
        assert real.execute("SELECT count(*) FROM api_cases WHERE id='should-not-exist'").fetchone()[0] == 0
