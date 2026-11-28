import unittest
from unittest.mock import patch
from task_server.services import puzzle_investigation as investigation


class PuzzleInvestigationTests(unittest.TestCase):
    def test_requires_phone_and_visual_difference(self):
        meta = {'file': '2jTQVOVDORiU3yQlQQBsoJ', 'board': {'id': '9924:9938'},
                'step': {'app_edition': '手机版'}, 'findings': {'differences': ['预设图名称不一致']}}
        self.assertTrue(investigation.supports(meta))
        self.assertFalse(investigation.supports(dict(meta, step={'app_edition':'HD 平板版'})))
        self.assertFalse(investigation.supports(dict(meta, findings={'differences':[]})))

    def test_known_issue_requires_matching_evidence(self):
        pair = {'design_name':'太空冒险','device_name':'太空探险','api_name':'太空探险'}
        meta = {'environment':'正式','scope':investigation.PUZZLE_SCOPE}
        self.assertTrue(investigation.issue_for(meta,pair,investigation.ASTRONAUT_HASH))
        self.assertFalse(investigation.issue_for(meta,pair,'other-picture'))
        self.assertFalse(investigation.issue_for(dict(meta,environment='测试'),pair,investigation.ASTRONAUT_HASH))
        self.assertFalse(investigation.issue_for(meta,dict(pair,api_name='其他'),investigation.ASTRONAUT_HASH))

    def test_query_failure_keeps_inconclusive_with_no_link_or_fabricated_image(self):
        receipt = dict(ok=False, presets=[], host='qa.example', path='/presets', request_id='',
                       error='缺少测试环境')
        with patch.object(investigation.puzzle_preset_api, 'fetch_presets', return_value=receipt) as get, \
             patch.object(investigation.puzzle_preset_api, 'fetch_resource') as resource:
            result = investigation.examine('user', {'environment':'测试'}, b'', b'')
        get.assert_called_once_with('user','测试')
        resource.assert_not_called()
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(result['issue_url'], '')
        self.assertNotIn('evidence_image',result)

    def test_layout_position_is_insufficient_to_confirm_a_name_difference(self):
        meta = {'environment':'正式','findings':{'differences':['示例卡片名不一致']}}
        receipt = dict(ok=True, presets=[{'name':'示例','imageUrl':'https://host/card.png'}],
                       host='prod.example',path='/presets',request_id='sample')
        with patch.object(investigation.puzzle_preset_api, 'fetch_presets', return_value=receipt), \
             patch.object(investigation.puzzle_preset_api, 'fetch_resource',return_value=b'image'), \
             patch.object(investigation.puzzle_image_evidence,'match_preset',return_value={'matched':False}), \
             patch.object(investigation,'_read_labels') as labels:
            result = investigation.examine('user',meta,b'board',b'shot')
        labels.assert_not_called()
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(result['issue_url'],'')
