"""Check that QA install selection doesn't pick an HD, release, or unrelated sender's card."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from feishu_android_build import notice


class BuildNoticeTest(unittest.TestCase):
    profile = dict(sender_id='official-test', title='智小白3D 又有新包了', version='2.0.5', environment='production')

    def card(self, title=None, note='更新内容 : · “上架包”'):
        content = {'title': title or self.profile['title'], 'text': 'APP版本: 2.0.5 版本号: 59 构建编号: 522 '
                   'Android 分支: mobile/release/2.0.6\n' + note, 'url': 'https://www.pgyer.com/oY0S7Zg4'}
        return {'sender': {'id': 'official-test'}, 'message_id': 'test-message', 'body': {'content': json.dumps(content)}}

    def test_mobile_release(self):
        self.assertEqual(notice(self.card(), self.profile)['build'], '522')

    def test_qa_requires_explicit_environment(self):
        qa = dict(self.profile, environment='qa')
        self.assertIsNone(notice(self.card(), qa))
        self.assertEqual(notice(self.card(note='环境：QA\n更新内容：修复问题'), qa)['code'], '59')

    def test_other_product_version_or_sender(self):
        self.assertIsNone(notice(self.card('智小白3D HD 又有新包了'), self.profile))
        self.assertIsNone(notice(self.card(), dict(self.profile, version='2.0.6')))
        self.assertIsNone(notice(self.card(), dict(self.profile, sender_id='other')))

    def test_mixed_environment_is_ambiguous(self):
        with self.assertRaises(ValueError):
            notice(self.card(note='环境：QA\n更新内容：上架包'), self.profile)
