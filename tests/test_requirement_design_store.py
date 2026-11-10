import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from task_server.services import requirement_design as store
from task_server.services import requirement_design_model as model
from task_server.services.week1_probe import FIXTURE


class DesignStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bind(store, '_root', return_value=self.root)
        self.claim = self.bind(store, 'acquire_heavy_workload_slot', return_value=True)
        self.release = self.bind(store, 'release_heavy_workload_slot')
        self.profile = {'user_id': 'owner', 'status': 'active', 'permissions': ['platform.configure', 'ui.view'],
                        'scope': {'ui_apps': '*'}}
        self.access = self.bind(store.identity, 'get_access_profile', return_value=self.profile)
        self.body = {'title': '首页入口', 'app': 'app', 'version': 'v1', 'clauses': ['现在显示搜索入口'],
                     'image_base64': base64.b64encode(FIXTURE.read_bytes()).decode()}
        self.sid = store.import_source('owner', self.body)
        self.request = 'a' * 32

    def bind(self, obj, name, **kwargs):
        ctx = patch.object(obj, name, **kwargs)
        self.addCleanup(ctx.stop)
        return ctx.start()

    def start(self, key=None):
        return store.start('owner', 'username', self.sid, key or self.request)

    def orphan(self):
        rid = 'b' * 32
        with store._db() as db:
            db.execute('INSERT INTO designs (id,owner,source,request,status,created) VALUES (?,?,?,?,?,?)',
                       (rid, 'owner', self.sid, self.request, 'running', time.time()))
        return rid

    def wait_worker(self):
        for _ in range(200):
            rows = store.overview('owner', self.sid)['runs']
            if rows and rows[0]['status'] != 'running':
                return rows[0]
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_import_is_frozen_deduplicated_and_private(self):
        self.assertEqual(self.sid, store.import_source('owner', self.body))
        self.assertNotEqual(self.sid, store.import_source('owner', dict(self.body, clauses=['新需求'])))
        view = store.overview('owner', self.sid)
        self.assertEqual(len(view['sources']), 2)
        self.assertEqual(view['source']['clauses'], [{'id': 'P1', 'text': '现在显示搜索入口'}])
        image = store.prototype('owner', self.sid, 'V1')
        self.assertEqual(hashlib.sha256(image).hexdigest(), view['source']['prototypes'][0]['sha256'])
        self.assertEqual((self.root / 'requirement-design.sqlite3').stat().st_mode & 0o777, 0o600)
        self.assertEqual(store.overview('outsider')['sources'], [])
        for operation in (lambda: store.overview('outsider', self.sid),
                          lambda: store.prototype('outsider', self.sid, 'V1'),
                          lambda: store.start('outsider', 'name', self.sid, self.request)):
            with self.assertRaises(store.Missing):
                operation()
        self.claim.assert_not_called()

    def test_invalid_png_does_not_create_source(self):
        with self.assertRaises(ValueError):
            store.import_source('owner', dict(self.body, image_base64=base64.b64encode(b'fake').decode()))
        self.assertEqual(len(store.overview('owner')['sources']), 1)

    def test_shared_sample_visible_but_other_owners_runs_are_private(self):
        directory = self.root / 'requirement-design-materials'
        directory.mkdir()
        raw = FIXTURE.read_bytes()
        (directory / 'demo.png').write_bytes(raw)
        meta = dict(title='示例', app='app', version='v1', prd_file_sha256='f' * 64,
                    clauses=[dict(id='P1', text='显示入口')],
                    prototypes=[dict(id='V1', file='demo.png', caption='原型', sha256=hashlib.sha256(raw).hexdigest())])
        (directory / 'source.json').write_text(json.dumps(meta))
        sid = store.overview('owner')['source']['id']
        self.assertEqual(store.prototype('outsider', sid, 'V1'), raw)
        self.bind(model, 'generate', return_value={'points': [], 'cases': [], 'questions': ['入口？']})
        self.sid = sid
        self.start()
        self.assertEqual(self.wait_worker()['status'], 'completed')
        self.assertEqual(store.overview('outsider', sid)['runs'], [])

    def test_permission_profile_requires_active_identity_full_scope_and_permissions(self):
        self.assertTrue(store.authorized('owner', 'username'))
        for override in ({'user_id': 'other'}, {'status': 'disabled'}, {'must_change_password': True},
                         {'permissions': ['ui.view']}, {'scope': {'ui_apps': []}}):
            self.access.return_value = dict(self.profile, **override)
            with self.subTest(override=override):
                self.assertFalse(store.authorized('owner', 'username'))

    def test_retry_orphan_finalizes_same_run_without_starting_model(self):
        rid = self.orphan()
        result = self.start()
        self.assertEqual(result['id'], rid)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['finished'])
        self.assertEqual(result, self.start())
        self.claim.assert_not_called()

    def test_busy_slot_after_recovery_does_not_undo_orphan_terminal_state(self):
        rid = self.orphan()
        self.claim.return_value = False
        with self.assertRaises(store.Busy):
            self.start('c' * 32)
        with store._db() as db:
            self.assertEqual(db.execute('SELECT status FROM designs WHERE id=?', (rid,)).fetchone()[0], 'failed')

    def test_thread_launch_error_persists_and_releases_slot_and_lock(self):
        start = self.bind(threading.Thread, 'start', side_effect=RuntimeError('private diagnostic'))
        failed = self.start()
        self.assertEqual(failed['status'], 'failed')
        self.assertTrue(failed['finished'])
        self.assertNotIn('private', failed['error'])
        self.assertEqual(self.start(), failed)
        start.assert_called_once()
        self.release.assert_called_once_with('sync')
        fd = store._lock()
        self.assertIsNotNone(fd)
        os.close(fd)

    def test_overlapping_retries_keep_one_run_and_refuse_different_request(self):
        entered, finish = threading.Event(), threading.Event()

        def generate(source, directory):
            self.assertEqual((directory / 'prototype.png').read_bytes(), store.prototype('owner', self.sid, 'V1'))
            entered.set()
            if not finish.wait(5):
                raise TimeoutError()
            return {'questions': ['待确认']}

        model_call = self.bind(model, 'generate', side_effect=generate)
        first = self.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.start(), first)
            with self.assertRaises(store.Busy):
                self.start('c' * 32)
            other = store.import_source('owner', dict(self.body, title='另一份需求'))
            with self.assertRaises(ValueError):
                store.start('owner', 'username', other, self.request)
        finally:
            finish.set()
        result = self.wait_worker()
        self.assertEqual(result['id'], first['id'])
        self.assertEqual(result['review'], 'pending')
        self.assertEqual(self.start(), result)
        self.assertEqual(len(store.overview('owner', self.sid)['runs']), 1)
        model_call.assert_called_once()

    def test_same_request_during_initial_claim_waits_for_persisted_id(self):
        from concurrent.futures import ThreadPoolExecutor
        claimed, proceed = threading.Event(), threading.Event()

        def hold_initial_claim(*args, **kwargs):
            claimed.set()
            return proceed.wait(5)

        self.claim.side_effect = hold_initial_claim
        generate = self.bind(model, 'generate', return_value={'questions': ['待确认']})
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.start)
            self.assertTrue(claimed.wait(3))
            retry = pool.submit(self.start)
            try:
                time.sleep(.04)
            finally:
                proceed.set()
            self.assertEqual(first.result()['id'], retry.result()['id'])
        self.assertEqual(self.wait_worker()['status'], 'completed')
        generate.assert_called_once()

    def test_revoked_permission_before_or_after_call_discards_worker_result(self):
        for during in (False, True):
            self.access.side_effect = [self.profile, None] if during else [None]
            with patch.object(model, 'generate', return_value={'result': 'private'}) as model_call:
                self.start(('d' if during else 'e') * 32)
                failed = self.wait_worker()
                self.assertEqual(failed['status'], 'failed')
                self.assertNotIn('result', failed)
                self.assertEqual(model_call.call_count, int(during))
