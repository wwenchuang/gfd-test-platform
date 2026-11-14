import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from mobile_tracking_observer import matching_request, request_records, receipt
from mobile_tracking_runner import find_control, intersect, native_bounds, scoped_events, xml_bounds


class TrackingEvidenceTests(unittest.TestCase):
    def test_source_and_receipt_scoped_to_last_action(self):
        sent=1794560401.0
        target=urlsplit('https://event.gongfudou.com/event/zxbEvent?type=UA')
        url=urlsplit('https://event.gongfudou.com/event/zxbEvent?type=CS')
        body={'user':{'tel':'13900000000'}, 'evts':[
            {'type':'CSZ','time':str(int(sent*1000)),'extAttr':{'page':'home'},'conts':[{'resourceName':'private title','extraData':'{"source":"topic","topicId":"12"}'}]},
            {'type':'AOP','time':int(sent)},
            {'type':'AEC','time':str(int((sent-2)*1000))},
        ]}
        self.assertTrue(matching_request(target,url,'POST'))
        self.assertFalse(matching_request(target,url,'GET'))
        rows=request_records(body,sent,url,json.dumps(body))
        actions=[{'label':'首页','time':sent-2},{'label':'广场','time':sent-1,'until':sent+5}]
        events=scoped_events(rows,actions,['首页','广场'])
        self.assertEqual([e['event'] for e in events],['CSZ'])
        self.assertEqual(events[0]['actionIndex'],1)
        self.assertEqual(events[0]['contents'][0]['topicId'],'12')
        self.assertEqual(rows[0]['reqId'],rows[1]['reqId'])
        self.assertNotIn('private title',json.dumps(events))
        self.assertNotIn('13900000000',json.dumps(events))
        self.assertEqual(scoped_events(rows,actions[:1],['首页','广场']),[])
        self.assertEqual(receipt(200,{'code':False})['ack'],'回执未识别')
        self.assertEqual(receipt(200,{'code':-1,'msg':'private'})['responseMessage'],'')
        self.assertEqual(receipt(503,{'code':0})['ack'],'上报失败')

    def test_status_placeholder_has_no_server_receipt(self):
        missing = dict(httpStatus=None, businessCode=None, responseMessage='', ack='回执未识别')
        for status in (999, 0, 600, -1):
            with self.subTest(status=status):
                self.assertEqual(receipt(status, {'code':0, 'msg':'success'}), missing)
        for status in (404, 503):
            self.assertEqual(receipt(status, None), dict(missing, httpStatus=status, ack='上报失败'))
        self.assertEqual(receipt(200, {'code':0})['ack'], '已接收')

    def test_animated_home_selects_only_the_visible_hot_category_for_the_selected_device(self):
        xml = ('<hierarchy><node package="com.kfb.model" text="热门" resource-id="com.kfb.model:id/title" bounds="[2,3][4,5]"/>'
               '<node package="com.kfb.model" text="热门" resource-id="com.kfb.model:id/tv_tab_title" bounds="[127,311][213,361]"/>'
               '<node package="other" text="热门" resource-id="com.kfb.model:id/tv_tab_title" bounds="[1,1][8,8]"/></hierarchy>')
        handset = Mock()
        handset.dump_hierarchy.return_value = xml
        driver = Mock()
        driver.connect.return_value = handset
        active = b'mCurrentFocus=Window{a1 u0 com.kfb.model/com.kfb.model.MainActivity}'
        with patch.dict('sys.modules', {'uiautomator2': driver}):
            self.assertEqual(find_control(Mock(return_value=active), '热门', device='chosen-phone'),
                ('com.kfb.model/com.kfb.model.MainActivity', 170, 336, '可见文字'))
        driver.connect.assert_called_once_with('chosen-phone')
        handset.stop_uiautomator.assert_called_once_with(wait=False)
        duplicate = xml.replace('</hierarchy>', '<node package="com.kfb.model" text="热门" resource-id="com.kfb.model:id/tv_tab_title" bounds="[200,490][300,520]"/></hierarchy>')
        self.assertIsNone(intersect(xml_bounds(duplicate, '热门', 'com.kfb.model:id/tv_tab_title')))

    def test_home_recommendation_uses_the_home_category_instead_of_plaza_or_feed_headline(self):
        xml=('<hierarchy><node text="推荐" package="com.kfb.model" resource-id="com.kfb.model:id/model_plaza_recommend_title" bounds="[0,0][90,80]"/>'
             '<node text="推荐" package="com.kfb.model" resource-id="com.kfb.model:id/tv_tab_title" bounds="[18,120][100,230]"/></hierarchy>')
        active=b'mCurrentFocus=Window{a1 u0 com.kfb.model/com.kfb.model.MainActivity}'
        command=Mock(return_value=active)
        with patch('mobile_tracking_runner.android_hierarchy',return_value=xml):
            selected=find_control(command,'推荐',device='chosen-phone',previous_label='首页')
        self.assertEqual(selected,('com.kfb.model/com.kfb.model.MainActivity',59,175,'可见文字'))
        command.assert_called_once_with('shell','dumpsys','window')

    def test_current_activity_visible_hierarchy_and_unique_bounds(self):
        dump='\n  ACTIVITY com.kfb.model/.MainActivity a1 pid=21\n  View Hierarchy:\n    android.View{abc V..... ........ 0,0-100,100}\n      android.View{def I.... ........ 3,0-20,10 #123 app:id/app_tab_home}\n      android.View{fed V.... ........ 10,20-70,90 #124 app:id/app_tab_home}'
        self.assertEqual(native_bounds(dump,('app_tab_home',),'com.kfb.model/com.kfb.model.MainActivity'),[(10,20,70,90)])
        xml='<hierarchy><node text="首页" package="com.kfb.model" bounds="[2,3][40,52]"/><node text="首页" package="other" bounds="[5,6][8,8]"/></hierarchy>'
        self.assertEqual(xml_bounds(xml,'首页'),[(2,3,40,52)])
        self.assertIsNone(intersect([(0,0,100,10),(0,20,100,40)]))

if __name__=='__main__':unittest.main()
