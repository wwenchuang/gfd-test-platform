import unittest
from unittest.mock import patch

from task_server.services import mobile_tracking_plan as plan


class KnownPaths(unittest.TestCase):
    @patch.object(plan, '_post_json', return_value={'http_status': 503})
    def test_category_and_import_paths_keep_original_page(self, gateway):
        rules = [
            {'id': 2, 'action': '点击推荐、热门、最新、玩具等分类', 'event': 'AEC',
             'properties': 'page=home\nele=model_category'},
            {'id': 7, 'action': '点击本地导入', 'event': 'AEC',
             'properties': 'page=home；\nele=localImport；'},
        ]
        items = plan.analyze(rules)
        self.assertEqual([i['path'] for i in items if i['id']==2],
                         [['首页', name] for name in ('推荐','热门','最新','玩具')])
        self.assertEqual(items[-1]['path'], ['首页','导入打印','本地导入'])
        self.assertTrue(all('已实测路径' in item['reason'] for item in items))
