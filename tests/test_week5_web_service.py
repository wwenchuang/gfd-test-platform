import io
import json
import os
import sys
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from task_server.services import week5_web_service as service


def trace_bytes():
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('trace.trace', '{"event":"ready"}')
    return out.getvalue()


PROFILE = {'user_id':'user1', 'username':'user', 'status':'active', 'must_change_password':False,
           'permissions':['ui.view'], 'is_superuser':False, 'scope':{'ui_apps':['app1']}}
TOKEN = 'x' * 43
WORKER = {'scenario':'ui_catalog_count', 'version':1, 'status':'passed', 'error_kind':None, 'total':0}
PNG = b'\x89PNG\r\n\x1a\n' + b'test-pixels'


class ServiceTest(unittest.TestCase):
    def test_workflows_require_their_own_asset_and_actual_device_or_app_detail(self):
        asset = {'app_id':'a','case_id':'one','revision':'e'*64}
        base = dict(version=1,phase='workflow',status='passed',error_kind=None,total=1,asset=asset)
        def parse(scenario, **extra):
            payload = dict(base,scenario=scenario,**extra)
            command = [sys.executable,'-c','print(' + repr(json.dumps(payload)) + ')']
            return service._bounded_process(command,{'PATH':'/usr/bin:/bin'},TOKEN,'user1',scenario=scenario)
        self.assertEqual(parse('ui_start_unready',device_count=2)['device_count'],2)
        self.assertEqual(parse('ui_start_switch',switched_app_id='b')['switched_app_id'],'b')
        self.assertEqual(parse('ui_catalog_paging',pages_checked=1)['pages_checked'],1)
        for key,extra in (('ui_catalog_paging',{}),('ui_catalog_paging',{'pages_checked':2}),
                          ('ui_catalog_paging',{'pages_checked':True}),('ui_case_revision',{'pages_checked':1}),
                          ('ui_start_unready',{}),('ui_start_switch',{'switched_app_id':'a'}),
                          ('ui_case_revision',{'asset':None}),('ui_case_selection',{'device_count':9})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse(key,**extra)

    def test_drill_requires_a_real_mismatch_and_cannot_label_normal_scenarios(self):
        scenario = 'ui_catalog_failure_drill'
        failed = dict(scenario=scenario, version=1, phase='drill', status='failed',
                      error_kind='drill_assertion', total=3, expected_total=0)

        def result(**changes):
            payload = dict(failed, **changes)
            command = [sys.executable, '-c', 'print(' + repr(json.dumps(payload)) + ')']
            return service._bounded_process(command, {'PATH':'/usr/bin:/bin'}, TOKEN, 'user1', scenario=payload['scenario'])

        self.assertEqual(result(), failed)
        for changes in (dict(scenario='ui_catalog_count'), dict(expected_total=3), dict(expected_total=False),
                        dict(status='passed'), dict(phase='initial'), dict(error_kind='business')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                result(**changes)
        self.assertEqual(result(total=0, expected_total=1)['expected_total'], 1)

    def test_drill_does_not_publish_evidence_from_an_unverified_browser_identity(self):
        scenario = 'ui_catalog_failure_drill'
        result = dict(scenario=scenario, version=1, phase='initial', status='blocked', error_kind='authentication')
        with tempfile.TemporaryDirectory() as tmp, patch.object(service, '_profile', return_value=PROFILE), \
             patch.object(service, '_execute', return_value=(result, PNG, trace_bytes())):
            db, root = Path(tmp)/'runs.db', Path(tmp)/'evidence'
            saved = service.run('user1','user',TOKEN,scenario=scenario,db_path=db,root=root)
            self.assertEqual(saved['error_kind'], 'authentication')
            self.assertNotIn('id', saved)
            self.assertEqual(service.list_results('user1','user',TOKEN,db_path=db), [])
            self.assertFalse(root.exists() and any(root.iterdir()))

    def test_initial_page_failure_before_filter_target_is_not_discarded(self):
        worker={'scenario':'ui_catalog_filter','version':1,'phase':'initial','status':'failed','error_kind':'business'}
        command=[sys.executable,'-c','import json; print(json.dumps('+repr(worker)+'))']
        result=service._bounded_process(command,{'PATH':'/usr/bin:/bin'},TOKEN,'user1',scenario='ui_catalog_filter')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['phase'],'initial')
        with tempfile.TemporaryDirectory() as tmp, patch.object(service,'_profile',return_value=PROFILE), \
             patch.object(service,'_execute',return_value=(result,PNG,trace_bytes())):
            db,root=Path(tmp)/'runs.db',Path(tmp)/'files'
            saved=service.run('user1','user',TOKEN,scenario='ui_catalog_filter',db_path=db,root=root)
            self.assertEqual(saved['status'],'failed')
            self.assertEqual(service.artifact('user1','user',TOKEN,saved['id'],'screenshot',db_path=db,root=root),PNG)
            self.assertEqual(service.artifact('user1','user',TOKEN,saved['id'],'trace',db_path=db,root=root),trace_bytes())
        worker['phase']='filter'
        command=[sys.executable,'-c','import json; print(json.dumps('+repr(worker)+'))']
        with self.assertRaises(ValueError):
            service._bounded_process(command,{'PATH':'/usr/bin:/bin'},TOKEN,'user1',scenario='ui_catalog_filter')

    def test_filter_result_records_owned_asset_revision_and_cannot_relabel_another_scenario(self):
        asset={'app_id':'app1','case_id':'case-a','revision':'a'*64}
        worker={'scenario':'ui_catalog_filter','version':1,'phase':'filter','status':'passed','error_kind':None,'total':1,'asset':asset}
        command=[sys.executable,'-c','import json; print(json.dumps('+repr(worker)+'))']
        result=service._bounded_process(command,{'PATH':'/usr/bin:/bin'},TOKEN,'user1',scenario='ui_catalog_filter')
        self.assertEqual(result['asset'],asset)
        with self.assertRaises(ValueError):
            service._bounded_process(command,{'PATH':'/usr/bin:/bin'},TOKEN,'user1',scenario='ui_catalog_empty')
        with tempfile.TemporaryDirectory() as tmp, patch.object(service,'_profile',return_value=PROFILE), \
             patch.object(service,'_execute',return_value=(result,PNG,trace_bytes())):
            db,root=Path(tmp)/'runs.db',Path(tmp)/'files'
            saved=service.run('user1','user',TOKEN,scenario='ui_catalog_filter',db_path=db,root=root)
            self.assertEqual(saved['asset'],asset)
            self.assertEqual(service.list_results('user1','user',TOKEN,db_path=db)[0]['asset'],asset)
            with patch.object(service,'_profile',return_value=dict(PROFILE,scope={'ui_apps':[]})):
                self.assertNotIn('asset',service.list_results('user1','user',TOKEN,db_path=db)[0])

    def test_identity_error_during_ready_update_rolls_back_to_saving(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            db,root=Path(tmp)/'runs.db',Path(tmp)/'files'
            with patch.object(service, '_profile', side_effect=[PROFILE,PROFILE,PROFILE,PROFILE,OSError('unavailable')]):
                result=service.run('user1','user',TOKEN,db_path=db,root=root)
            self.assertEqual(result['status'],'blocked')
            self.assertNotIn('total',result)
            with patch.object(service, '_profile',return_value=PROFILE):
                rows=service.list_results('user1','user',TOKEN,db_path=db)
                self.assertTrue(rows[0]['deletion_pending'])
                with self.assertRaises(ValueError):
                    service.artifact('user1','user',TOKEN,rows[0]['id'],'trace',db_path=db,root=root)

    def test_sqlite_ready_update_failure_keeps_saving_record(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile',return_value=PROFILE), \
             patch.object(service, '_execute',return_value=(WORKER, PNG, trace_bytes())):
            db,root=Path(tmp)/'runs.db',Path(tmp)/'files'
            with service.week1_smoke._db(db) as conn:
                conn.execute("CREATE TRIGGER reject_ready BEFORE UPDATE ON results BEGIN SELECT RAISE(FAIL,'fixture'); END")
            result=service.run('user1','user',TOKEN,db_path=db,root=root)
            self.assertEqual(result['status'],'blocked')
            rows=service.list_results('user1','user',TOKEN,db_path=db)
            self.assertTrue(rows[0]['deletion_pending'])
            with self.assertRaises(ValueError):
                service.artifact('user1','user',TOKEN,rows[0]['id'],'trace',db_path=db,root=root)

    def test_revocation_during_lock_or_file_write_never_returns_or_exposes_result(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            db,root=Path(tmp)/'runs.db',Path(tmp)/'files'
            with patch.object(service, '_profile', side_effect=[PROFILE, PROFILE, None]):
                result=service.run('user1','user',TOKEN,db_path=db,root=root)
            self.assertEqual(result['status'],'blocked')
            self.assertNotIn('total',result)
            self.assertFalse(root.exists())
            with service.week1_smoke._db(db) as conn: conn.execute('UPDATE lease SET until_time=0')
            with patch.object(service, '_profile', side_effect=[PROFILE, PROFILE, PROFILE, None]):
                result=service.run('user1','user',TOKEN,db_path=db,root=root)
            self.assertEqual(result['status'],'blocked')
            self.assertNotIn('total',result)
            with patch.object(service, '_profile', return_value=PROFILE):
                rows=service.list_results('user1','user',TOKEN,db_path=db)
                self.assertEqual(len(rows),1)
                self.assertTrue(rows[0]['deletion_pending'])
                with self.assertRaises(ValueError):
                    service.artifact('user1','user',TOKEN,rows[0]['id'],'trace',db_path=db,root=root)
            with service.week1_smoke._db(db) as conn: conn.execute('UPDATE lease SET until_time=0')
            with patch.object(service, '_profile', side_effect=[PROFILE, PROFILE, PROFILE, PROFILE, None]):
                after_commit=service.run('user1','user',TOKEN,db_path=db,root=root)
            self.assertEqual(after_commit['status'],'blocked')
            self.assertNotIn('total',after_commit)
            with patch.object(service, '_profile', return_value=PROFILE):
                rows=service.list_results('user1','user',TOKEN,db_path=db)
                self.assertEqual(len(rows),2)
                self.assertTrue(all(row['deletion_pending'] for row in rows))
                for row in rows:
                    with self.assertRaises(ValueError):
                        service.artifact('user1','user',TOKEN,row['id'],'trace',db_path=db,root=root)

    def test_crash_during_evidence_write_remains_owned_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile', return_value=PROFILE):
            db, root = Path(tmp)/'runs.db', Path(tmp)/'files'
            fixture=Path(__file__).with_name('week5_crash_fixture.py')
            done=subprocess.run([sys.executable,str(fixture),str(db),str(root)],timeout=10,capture_output=True)
            self.assertEqual(done.returncode,72,done.stderr.decode(errors='replace'))
            rows=service.list_results('user1','user',TOKEN,db_path=db)
            self.assertEqual(len(rows),1)
            self.assertTrue(rows[0]['deletion_pending'])
            self.assertEqual(rows[0]['artifacts'],[])
            with self.assertRaises(ValueError):
                service.artifact('user1','user',TOKEN,rows[0]['id'],'screenshot',db_path=db,root=root)
            self.assertTrue(service.delete('user1','user',TOKEN,rows[0]['id'],db_path=db,root=root))
            self.assertEqual(service.list_results('user1','user',TOKEN,db_path=db),[])

    def test_all_owned_quota_records_can_be_managed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(service, '_profile', return_value=PROFILE):
            db=Path(tmp)/'runs.db'
            with service.week1_smoke._db(db) as conn:
                for i in range(51):
                    run_id=f'{i:032x}'
                    payload={'scope':'old', 'result':{'id':run_id,'created':i}}
                    conn.execute('INSERT INTO results VALUES (?,?,?,?)',(run_id,'user1',i,json.dumps(payload)))
            self.assertEqual(len(service.list_results('user1','user',TOKEN,db_path=db)),51)

    def test_scope_change_redacts_but_still_allows_owner_confirmed_removal(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile', return_value=PROFILE), \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            db, root = Path(tmp)/'runs.db', Path(tmp)/'files'
            result=service.run('user1','user',TOKEN,db_path=db,root=root)
            reduced=dict(PROFILE,scope={'ui_apps':[]})
            with patch.object(service, '_profile', return_value=reduced):
                items=service.list_results('user1','user',TOKEN,db_path=db)
                self.assertEqual(len(items),1)
                self.assertEqual(items[0]['id'],result['id'])
                self.assertTrue(items[0]['scope_changed'])
                self.assertNotIn('total',items[0])
                with self.assertRaises(ValueError):
                    service.artifact('user1','user',TOKEN,result['id'],'trace',db_path=db,root=root)
                self.assertTrue(service.delete('user1','user',TOKEN,result['id'],db_path=db,root=root))
                self.assertEqual(service.list_results('user1','user',TOKEN,db_path=db),[])

    def test_retry_after_partial_file_removal_does_not_touch_another_run(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile', return_value=PROFILE), \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            db, root = Path(tmp)/'runs.db', Path(tmp)/'files'
            first = service.run('user1','user',TOKEN,db_path=db,root=root)
            # The production lease has a short cooldown; advance only the test DB clock.
            with service.week1_smoke._db(db) as conn:
                conn.execute('UPDATE lease SET until_time=0')
            second = service.run('user1','user',TOKEN,db_path=db,root=root)
            real_unlink=os.unlink
            calls=[]
            def fail_after_one(*args, **kwargs):
                calls.append(args[0])
                if len(calls) == 2:
                    raise OSError('fixture interruption')
                return real_unlink(*args,**kwargs)
            with patch.object(service.os, 'unlink', side_effect=fail_after_one):
                self.assertFalse(service.delete('user1','user',TOKEN,first['id'],db_path=db,root=root))
            self.assertTrue(service.delete('user1','user',TOKEN,first['id'],db_path=db,root=root))
            self.assertEqual(service.artifact('user1','user',TOKEN,second['id'],'screenshot',db_path=db,root=root),PNG)

    def test_delete_serializes_with_evidence_read(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile', return_value=PROFILE), \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            db, root = Path(tmp)/'runs.db', Path(tmp)/'files'
            result = service.run('user1','user',TOKEN,db_path=db,root=root)
            inside, proceed, read_done = threading.Event(), threading.Event(), threading.Event()
            outcomes=[]
            original=service._remove_evidence
            def slow_remove(*args):
                inside.set()
                self.assertTrue(proceed.wait(3))
                return original(*args)
            def remove():
                outcomes.append(('delete', service.delete('user1','user',TOKEN,result['id'],db_path=db,root=root)))
            def read():
                try:
                    service.artifact('user1','user',TOKEN,result['id'],'trace',db_path=db,root=root)
                    outcomes.append(('read','unexpected'))
                except ValueError:
                    outcomes.append(('read','blocked'))
                finally:
                    read_done.set()
            with patch.object(service, '_remove_evidence', side_effect=slow_remove):
                deleter=threading.Thread(target=remove);deleter.start();self.assertTrue(inside.wait(3))
                reader=threading.Thread(target=read);reader.start()
                self.assertFalse(read_done.wait(.1))
                proceed.set();deleter.join(3);reader.join(3)
            self.assertCountEqual(outcomes,[('delete',True),('read','blocked')])

    def test_shared_memory_low_space_or_run_growth_terminates_child(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_SHM_FREE_RESERVE', 0), \
             patch.object(service, '_SHM_RUN_LIMIT', 2048), \
             patch.object(service, '_TIMEOUT_SECONDS', 5):
            command = [sys.executable, '-c',
                       'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_bytes(b"x"*4096); time.sleep(30)',
                       str(Path(tmp)/'growth')]
            with self.assertRaises(service.ResourceLimit):
                service._bounded_process(command, {'PATH':'/usr/bin:/bin'}, TOKEN, 'user1', scratch=Path(tmp))
        with tempfile.TemporaryDirectory() as tmp, patch.object(service, '_SHM_FREE_RESERVE', 10**30):
            self.assertFalse(service._scratch_within_limit(Path(tmp)))

    def test_delete_is_owned_recoverable_and_hides_pending_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile', return_value=PROFILE), \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            db, root = Path(tmp)/'runs.db', Path(tmp)/'files'
            result=service.run('user1','user',TOKEN,db_path=db,root=root)
            with self.assertRaises(ValueError):
                service.delete('other','user',TOKEN,result['id'],db_path=db,root=root)
            with patch.object(service, '_remove_evidence', side_effect=OSError('simulated crash')):
                self.assertFalse(service.delete('user1','user',TOKEN,result['id'],db_path=db,root=root))
            rows=service.list_results('user1','user',TOKEN,db_path=db)
            self.assertEqual(rows[0]['deletion_pending'],True)
            self.assertEqual(rows[0]['artifacts'],[])
            with self.assertRaises(ValueError):
                service.artifact('user1','user',TOKEN,result['id'],'trace',db_path=db,root=root)
            self.assertTrue(service.delete('user1','user',TOKEN,result['id'],db_path=db,root=root))
            self.assertEqual(service.list_results('user1','user',TOKEN,db_path=db),[])
            self.assertFalse((root/result['id']).exists())

    def test_child_and_its_exec_inherit_disabled_core_dumps(self):
        cmd = service._core_limited_args([sys.executable, '-c',
                                          'import resource; print(resource.getrlimit(resource.RLIMIT_CORE))'])
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), '(0, 0)')

    def test_quota_rejects_low_free_disk_and_per_user_or_global_limits(self):
        gib = 1024 * 1024 * 1024
        self.assertFalse(service._within_quota(0, 0, 0, 0, 1, gib))
        self.assertFalse(service._within_quota(service._OWNER_BYTES, 0, 0, 0, 1, 4 * gib))
        self.assertFalse(service._within_quota(0, service._GLOBAL_BYTES, 0, 0, 1, 4 * gib))
        self.assertFalse(service._within_quota(0, 0, service._OWNER_RUNS, 0, 1, 4 * gib))
        self.assertFalse(service._within_quota(0, 0, 0, service._GLOBAL_RUNS, 1, 4 * gib))
        self.assertTrue(service._within_quota(0, 0, 0, 0, 1, 4 * gib))

    def test_timed_out_process_group_is_killed_and_reaped(self):
        real_kill = os.killpg
        with patch.object(service, '_TIMEOUT_SECONDS', .2), \
             patch.object(service.os, 'killpg', wraps=real_kill) as kill_group:
            with self.assertRaises(TimeoutError):
                service._bounded_process([sys.executable, '-c', 'import time;time.sleep(30)'],
                                         {'PATH':'/usr/bin:/bin'}, TOKEN, 'user1')
            kill_group.assert_called_once()
            with self.assertRaises(ProcessLookupError):
                os.kill(kill_group.call_args.args[0], 0)

    def test_current_session_and_ui_view_permission_are_required(self):
        session = {'user':'user', 'user_id':'user1'}
        with patch.object(service.auth, 'verify_session_token', return_value=session), \
             patch.object(service.identity, 'get_access_profile', return_value=PROFILE):
            self.assertEqual(service._profile('user1','user',TOKEN), PROFILE)
            self.assertIsNone(service._profile('other','user',TOKEN))
        with patch.object(service.auth, 'verify_session_token', return_value=None):
            self.assertIsNone(service._profile('user1','user',TOKEN))
        with patch.object(service.auth, 'verify_session_token', return_value=session), \
             patch.object(service.identity, 'get_access_profile', return_value=dict(PROFILE, permissions=[])):
            self.assertIsNone(service._profile('user1','user',TOKEN))

    def test_revoked_session_during_run_never_saves_or_exposes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile', side_effect=[PROFILE, None]), \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            result = service.run('user1','user',TOKEN,db_path=Path(tmp)/'runs.db',root=Path(tmp)/'files')
            self.assertEqual((result['status'], result['error_kind']), ('blocked','authentication'))
            self.assertFalse((Path(tmp)/'files').exists())

    def test_owner_scope_and_digest_gate_private_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(service, '_profile', return_value=PROFILE), \
             patch.object(service, '_execute', return_value=(WORKER, PNG, trace_bytes())):
            db, root = Path(tmp)/'runs.db', Path(tmp)/'files'
            result=service.run('user1','user',TOKEN,db_path=db,root=root)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(service.list_results('user1','user',TOKEN,db_path=db), [result])
            with patch.object(service, '_profile', side_effect=[PROFILE, None]):
                self.assertEqual(service.list_results('user1','user',TOKEN,db_path=db), [])
            self.assertEqual(service.list_results('different','user',TOKEN,db_path=db), [])
            self.assertEqual(service.artifact('user1','user',TOKEN,result['id'],'screenshot',db_path=db,root=root),PNG)
            with patch.object(service, '_profile', side_effect=[PROFILE, None]):
                with self.assertRaises(ValueError):
                    service.artifact('user1','user',TOKEN,result['id'],'screenshot',db_path=db,root=root)
            with self.assertRaises(ValueError):
                service.artifact('different','user',TOKEN,result['id'],'screenshot',db_path=db,root=root)
            changed = dict(PROFILE, scope={'ui_apps': []})
            with patch.object(service, '_profile', return_value=changed):
                with self.assertRaises(ValueError):
                    service.artifact('user1','user',TOKEN,result['id'],'screenshot',db_path=db,root=root)
            (root / result['id'] / 'evidence' / 'screenshot.png').write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                service.artifact('user1','user',TOKEN,result['id'],'screenshot',db_path=db,root=root)
