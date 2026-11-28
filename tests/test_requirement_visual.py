import pytest
from task_server.services import requirement_visual as visual

def test_page_lists_business_frames_and_repeated_names():
    page={'id':'1:1','type':'CANVAS','children':[
        {'id':'2:1','type':'FRAME','name':'入口','absoluteBoundingBox':{'width':390,'height':844},
         'children':[{'type':'TEXT','characters':'华容道'}]},
        {'id':'2:2','type':'FRAME','name':'入口','absoluteBoundingBox':{'width':1194,'height':834}},
        {'id':'2:3','type':'FRAME','name':'隐藏','visible':False,'absoluteBoundingBox':{'width':390,'height':844}}]}
    frames=visual.frame_choices(page)
    assert [f['id'] for f in frames]==['2:1','2:2']
    assert frames[1]['width']==1194

def test_link_must_be_official():
    with pytest.raises(ValueError):visual.target('https://other.example/design/abc?node-id=2-3')
    assert visual.target('https://www.figma.com/design/abc?node-id=2-3')==('abc','2:3')

def test_history_keeps_private_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(visual.design,'_root',lambda:tmp_path)
    key=visual.save('one',{'scope':'scope','job':'first'},b'fig',b'live')
    assert visual.result('one',key)['scope']=='scope'
    assert visual.recent('two','scope')==[]
    with pytest.raises(visual.design.Missing):visual.result('two',key)

def test_report_ignores_javascript_containing_fake_dump(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(visual.config,'REPORT_DIR',str(tmp_path))
    dump={'executions':[{'tasks':[{'subType':'Assert','uiContext':{'screenshot':{'id':'sample'}},'param':{'dataDemand':'首页可见'}}]}]}
    (tmp_path/'own.html').write_text("""<script>let template='<script type="midscene_web_dump"'+n+'>';</script>"""
            +'<script type="midscene_web_dump" data-group-id="a">'+json.dumps(dump)+'</script>')
    assert visual.report_steps({'report_url':'/reports/own.html'})==[{'id':'sample','label':'首页可见'}]


def test_launch_identifies_edition_across_steps(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(visual.config, 'REPORT_DIR', str(tmp_path))
    def action(action_type, param, sid):
        return {'subType': action_type, 'param': param, 'uiContext': {'screenshot': {'id': sid}}}
    dump = {'executions': [
        {'tasks': [action('Launch', {'uri': 'com.kfb.model'}, 'launch')]},
        {'tasks': [action('Assert', {'prompt': '拼图'}, 'phone'),
                   action('Launch', {'uri': 'com.kfb.model.hd'}, 'hd-launch'),
                   action('Assert', {}, 'hd'), action('Launch', {'uri': 'other.app'}, 'other')]}]}
    (tmp_path / 'editions.html').write_text('<script type="midscene_web_dump">' + json.dumps(dump) + '</script>')
    rows = visual.report_steps({'report_url': '/reports/editions.html'})
    assert [row.get('app_edition') for row in rows] == ['手机版','手机版','HD 平板版','HD 平板版', None]


def test_mismatched_figma_is_rejected_before_export():
    hd = visual.design_edition('mTqhy1AQBFFZiELl01ww8b')
    phone = visual.design_edition('2jTQVOVDORiU3yQlQQBsoJ')
    assert hd == 'HD 平板版' and phone == '手机版'
    for design, app in [(hd, phone), (phone, hd)]:
        with pytest.raises(ValueError, match='请选择同一端'):
            visual.check_editions(design, app)
    visual.check_editions(phone, phone)
    assert visual.design_edition('unregistered') is None


def test_compare_rejects_hd_file_for_phone_job_before_model(monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(visual.design, '_db', lambda: nullcontext(None))
    monkeypatch.setattr(visual.design, '_source', lambda *args: (None, {'title':'拼图'}))
    monkeypatch.setattr(visual, 'job_record', lambda *args: {})
    monkeypatch.setattr(visual, 'report_steps', lambda *args: ({'app_edition':'手机版'}, b'screenshot'))
    monkeypatch.setattr(visual, 'boards', lambda *args: pytest.fail('should stop before exporting a board'))
    with pytest.raises(ValueError, match='设计稿为HD 平板版，此步骤执行的是手机版'):
        visual.compare('owner', 'user', {'source':'s','environment':'正式','job':'j','step':'s',
                                        'link':'https://www.figma.com/design/mTqhy1AQBFFZiELl01ww8b/'})
