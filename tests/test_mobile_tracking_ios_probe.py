import base64
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import time
import unittest
from unittest.mock import patch

import mobile_tracking_ios_probe as probe


class IosProxyProbeTest(unittest.TestCase):
    peer = '192.168.7.21'
    local = '192.168.7.10'

    def run_probe(self, sources, events=None, restore=True, fail_read=False):
        targets = iter(sources)
        opened = []
        def launch(device, bundle, url=None, timeout=11):
            self.assertEqual(device, probe.IPHONE)
            opened.append((bundle, url))
            return url is not None or restore
        def read(domains, deadline):
            if domains == probe.TARGETS:
                if fail_read:
                    raise probe.DiagnosisError(probe.REASONS['proxy'])
                return events or []
            url = opened[-1][1]
            self.assertTrue(url.startswith('http://' + domains[0] + '/'))
            return [{'request': {'url': url}, '_remoteDeviceIP': p} for p in next(targets)]
        with ExitStack() as stack:
            stack.enter_context(patch.object(probe, '_mac_addresses', return_value={self.local}))
            stack.enter_context(patch.object(probe, '_launch', side_effect=launch))
            stack.enter_context(patch.object(probe, '_entries', side_effect=read))
            stack.enter_context(patch.object(probe.time, 'sleep'))
            result = probe.probe(probe.IPHONE, probe.APP)
        self.assertEqual(opened[-1], (probe.APP, None))
        text = json.dumps(result)
        self.assertNotIn(self.peer, text)
        self.assertNotIn('iphone-source-', text)
        self.assertIn(result['reason'], probe.PUBLIC_REASONS)
        return result, opened

    def test_two_distinct_nonces_then_restore_and_observe(self):
        result, opened = self.run_probe([[self.peer], [self.peer]])
        self.assertNotEqual(opened[0][1], opened[1][1])
        self.assertEqual(result['probeMatches'], 2)
        self.assertTrue(result['sourceVerified'] and result['appRestored'] and result['observationComplete'])
        self.assertEqual([result[k] for k in probe.COUNTS], [0] * 5)
        self.assertEqual(result['stage'], 'complete')

    def test_sources_must_be_unique_repeatable_and_different_from_host(self):
        for targets in ([[self.peer], ['192.168.7.20']], [[self.local]], [['198.18.0.2']], [[self.local,self.peer]], [[], []]):
            with self.subTest(targets=targets):
                result, _ = self.run_probe(targets)
                self.assertFalse(result['sourceVerified'])
                self.assertTrue(result['appRestored'])
                self.assertFalse(result['observationComplete'])
                self.assertIsNone(result['postCount'])

    def test_error_and_restore_keep_reason_fixed_and_counts_unknown(self):
        result, _ = self.run_probe([[self.peer], [self.peer]], fail_read=True)
        self.assertTrue(result['sourceVerified'])
        self.assertEqual(result['stage'], 'observe')
        self.assertIsNone(result['eventCount'])
        result, _ = self.run_probe([[self.peer], [self.peer]], restore=False)
        self.assertEqual(result['stage'], 'restore')
        self.assertFalse(result['appRestored'])
        self.assertIsNone(result['connectCount'])

    def test_foreign_device_or_app_never_touched(self):
        with patch.object(probe, '_launch') as launch, patch.object(probe, '_mac_addresses') as mac:
            for device,bundle in [('other',probe.APP),(probe.IPHONE,'other')]:
                self.assertEqual(probe.probe(device,bundle)['stage'], 'preflight')
            launch.assert_not_called()
            mac.assert_not_called()
        with self.assertRaises(probe.DiagnosisError):
            probe._remaining(time.monotonic()-1, 2)

    def test_counts_check_source_method_path_time_and_business_code(self):
        start = 1000000000
        def entry(method='POST', host=probe.TARGETS[0], code=0, status=200, peer=self.peer, t=start, path='/event/zxbEvent'):
            post = json.dumps({'evts':[{'type':'AV','secret':'private payload'}]})
            reply = base64.b64encode(json.dumps({'code':code}).encode()).decode()
            return {'_remoteDeviceIP':peer,'startedDateTime':datetime.fromtimestamp(t,timezone.utc).isoformat(),
                    'request':{'url':'https://'+host+path+'?type=AV','method':method,'postData':{'text':post}},
                    'response':{'status':status,'content':{'text':reply,'encoding':'base64'}}}
        samples = [entry(),entry(host=probe.TARGETS[1], code=11),entry(code=None),entry(method='CONNECT', code=None),
                   entry(t=start-1),entry(t=start+10),entry(path='/other'),entry(host='unrelated.example'),entry(peer=self.local)]
        self.assertEqual(probe._counts(samples,self.peer,start,start+5),
                         dict(connectCount=1,postCount=3,receiptCount=2,acceptedCount=1,eventCount=3))
