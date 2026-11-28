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
