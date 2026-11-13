import tempfile
import unittest
from unittest.mock import Mock, patch
from task_server.services import mobile_tracking as track


class TrackingHistory(unittest.TestCase):
    def setUp(self):
        self.space = tempfile.TemporaryDirectory()
        self.addCleanup(self.space.cleanup)
        settings = patch.object(track, 'LEARNING_DIR', self.space.name)
        settings.start()
        self.addCleanup(settings.stop)

    def test_run_retention_and_owner(self):
        rid = track.start('a','mac',['广场'],track.HONOR)
        report = dict(title='方案',device=track.HONOR,runIds=[rid],rows=[
            dict(action='广场',origin='示例第1行',status='待实测',reason='等待回执')])
        with self.assertRaises(ValueError):
            track.save_report('b', report)
        ident = track.save_report('a',report)
        self.assertEqual(track.save_report('a',report),ident)
        self.assertEqual(len(track.history('a')),1)
        self.assertEqual(track.history('b'),[])
        with track.db() as con:
            con.execute('UPDATE runs SET created=0 WHERE id=?',(rid,))
        track.start('a','mac',['首页'],track.HONOR)
        self.assertEqual(track.read('a',rid)['state'],'done')

    def test_exclusive_device_readiness(self):
        phone=dict(device_id=track.HONOR,status='online',runner_online=True,usage_status='idle',runner_id='mac',
                   runner_capabilities={'tracking_http_receipts':True})
        for online,expected in [([phone],True),([phone,phone],False),([],False),
                                ([dict(phone,runner_capabilities={})],False)]:
            self.assertEqual(track.selectable_devices(online)[0]['ready'],expected)
        track.start('a','mac',['广场'],track.HONOR)
        track.start('b','mac',['广场'],track.DEVICE)
        with self.assertRaises(ValueError):
            track.start('b','mac',['广场'],track.HONOR)

    def test_iphone_uses_its_own_readiness_and_assignment(self):
        phone=dict(device_id=track.IPHONE, platform='ios', runner_id='ios', runner_online=True,
                   status='online', usage_status='idle', runner_capabilities={'tracking_http_receipts':True})
        for ready in (False, True):
            phone['runner_capabilities']['tracking_ios_devices'] = {track.IPHONE:dict(ready=ready,reason='设备检查')}
            self.assertEqual(track.selectable_devices([phone])[-1]['ready'], ready)
        self.assertFalse(track.selectable_devices([dict(phone, usage_status='busy')])[-1]['ready'])
        rid=track.start('tester','ios',['热门'],track.IPHONE)
        self.assertIsNone(track.claim('ios',[track.HONOR]))
        self.assertEqual(track.claim('ios',[track.IPHONE]),rid)
        self.assertEqual(track.assignment_for(rid,'ios')['device'],track.IPHONE)

    def test_sonic_directory_does_not_replace_runner_heartbeat_or_preflight(self):
        phone = dict(device_id=track.IPHONE, platform='ios', runner_online=True,
                     runner_id='ios', status='online', usage_status='idle',
                     runner_capabilities={'tracking_ios_devices': {track.IPHONE: {'ready': False, 'reason': 'WDA 未连接'}}})
        directory = {'__source__': {'available': True},
                     track.HONOR: {'sonic_status': 'ONLINE'}, track.IPHONE: {'sonic_status': 'ONLINE'}}
        options = {o['id']: o for o in track.selectable_devices([phone], directory)}
        honor, iphone = options[track.HONOR], options[track.IPHONE]
        self.assertEqual((honor['sonicStatus'], honor['runnerStatus'], honor['ready']), ('online', 'not_listed', False))
        self.assertEqual((iphone['sonicStatus'], iphone['runnerStatus'], iphone['ready']), ('online', 'online', False))
        self.assertEqual(iphone['runnerReason'], 'WDA 未连接')

    def test_sonic_unknown_absent_busy_and_explicit_offline(self):
        phone = dict(device_id=track.HONOR, runner_id='mac', runner_online=True, status='online',
                     usage_status='idle', runner_capabilities={'tracking_http_receipts': True})
        for source, status, expected in [(False, 'ONLINE', 'unknown'), (True, None, 'not_listed'),
                                         (True, 'OFFLINE', 'offline'), (True, 'DEBUGGING', 'busy'),
                                         (True, 'TESTING', 'busy'), (True, 'UNRECOGNIZED', 'unknown')]:
            with self.subTest(source=source, status=status):
                directory = {'__source__': {'available': source}, 'same-model-different-id': {'sonic_status': 'ONLINE'}}
                if status:
                    directory[track.HONOR] = {'sonic_status': status, 'sonic_user': 'private@example.test'}
                option = track.selectable_devices([phone], directory)[0]
                self.assertEqual(option['sonicStatus'], expected)
                self.assertTrue(option['sonicReason'])
                self.assertNotIn('private@example.test', str(option))
                self.assertEqual(option['runnerStatus'], 'online')
                self.assertEqual(option['ready'], expected != 'busy')

    def test_stale_runner_and_fresh_runner_with_disconnected_phone_are_distinct(self):
        phone = dict(device_id=track.HONOR, runner_online=False, status='online')
        stale = track.selectable_devices([phone])[0]
        self.assertEqual((stale['runnerStatus'], stale['ready']), ('offline', False))
        detached = track.selectable_devices([dict(phone, runner_online=True, status='offline')])[0]
        self.assertEqual((detached['runnerStatus'], detached['ready']), ('online', False))
        self.assertNotEqual(stale['runnerReason'], detached['runnerReason'])

    def test_devices_route_returns_only_known_phones_with_server_side_source(self):
        from task_server import router
        from task_server.services import ui_execution_contract
        handler = Mock()
        handler._body.return_value = {'devices': True, 'sonicStatus': 'offline'}
        directory = {'__source__': {'available': True}, track.HONOR: {'sonic_status': 'ONLINE'},
                     'other-phone': {'sonic_status': 'ONLINE'}}
        with patch.object(ui_execution_contract, 'require_ui_actor', return_value={'user_id': 'tester'}), \
                patch.object(router, 'all_online_devices', return_value=[]), \
                patch.object(router, 'sonic_list_device_statuses', return_value=directory) as lookup:
            router._tracking_user(handler, {})
        lookup.assert_called_once_with()
        options = {o['id']: o for o in handler._json.call_args[0][0]['devices']}
        self.assertEqual(set(options), set(track.DEVICES))
        self.assertEqual((options[track.HONOR]['sonicStatus'], options[track.HONOR]['ready']), ('online', False))
        self.assertNotIn('runner', options[track.HONOR])

    def test_published_files_are_fixed(self):
        with self.assertRaises(ValueError):
            track.published_file('../sessions.sqlite')
        self.assertEqual(track.published_file('honor')[0][:4],b'PK\x03\x04')

    def test_runner_error_keeps_selected_iphone_and_report_ownership(self):
        rid=track.start('a','mac',['首页'],track.IPHONE)
        self.assertEqual(track.claim('mac',[track.IPHONE]),rid)
        track.finish(rid,'mac',{'note':'WDA 连接中断'})
        self.assertEqual(track.read('a',rid)['device'],track.IPHONE)
        data=dict(device=track.IPHONE,rows=[{'action':'首页','status':'待实测'}],runIds=[rid])
        track.save_report('a',data)
        self.assertEqual(track.history('a')[0]['deviceName'],'iPhone 13')
        with self.assertRaises(ValueError):
            track.save_report('a',dict(data,device=track.HONOR))

    def test_claim_rechecks_known_sonic_occupancy_only_for_queued_runs(self):
        from unittest.mock import Mock
        directory = {'__source__': {'available': True}, track.HONOR:{'sonic_status':'TESTING'}}
        lookup = Mock(return_value=directory)
        self.assertIsNone(track.claim('mac',[track.HONOR],lookup))
        lookup.assert_not_called()
        rid=track.start('a','mac',['首页'],track.HONOR)
        self.assertIsNone(track.claim('mac',[track.HONOR],lookup))
        self.assertEqual(track.read('a',rid)['state'],'queued')
        directory[track.HONOR]['sonic_status']='ONLINE'
        self.assertEqual(track.claim('mac',[track.HONOR],lookup),rid)
