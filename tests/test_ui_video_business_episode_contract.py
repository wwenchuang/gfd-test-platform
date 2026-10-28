"""Rendered contract safety only; actual model/pixel behavior needs separate review."""
import copy
import json
from pathlib import Path
import re
import jsonschema
import pytest
from task_server.services import ai_skill_service as ai

ROOT=Path(__file__).resolve().parents[1]
CORE='业务事件以可见目标或字段身份、页面/对话框/对象/控件/返回边界和有证据的独立响应界定；原始可见证据、业务事件和执行步骤不是同一单位。'
EXAMPLES={
 'observe':{'states':[],'events':[{'action':'aiTap','target':'可见目标','value':'','frame_refs':['原帧'],'observation':'所见动作与变化',
            'uncertainties':[],'trigger_frame':'本包原帧','trigger_kind':'touch','page_before':'s1','page_after':'s2','continuation_of':'',
            'edit_boundary':'none','outcome':{'frame_refs':['结果原帧'],'observation':'具体可见状态'}}],
            'checks':[],'uncertainties':[],'multiple_scenarios':False,'revisit':[]},
 'audit':{'checks':[{'event_id':'给定片段ID','trigger_verdict':'supported','outcome_verdict':'supported','question':''}],
          'issues':[],'multiple_scenarios':False,'revisit':[]}}


def rendered(monkeypatch,version,bounded=False,payload=None):
    monkeypatch.setattr(ai,'load_ai_skill_prompt',lambda name,ver:(ROOT/'ai_skills/prompts'/f'{name}.{ver}.md').read_text())
    return ai.render_ai_skill_prompt('ui_video_trajectory',{'revisit_context_anchors':bounded,**(payload or {})},version=version)


def example(text):
    lines=[line if line.startswith('{"states":')else line.removeprefix('只返回JSON：')
           for line in text.splitlines()if line.startswith('{"states":')or line.startswith('只返回JSON：{')]
    assert len(lines)==1
    return json.JSONDecoder().raw_decode(lines[0])[0]


def test_shared_core_replaces_conflicting_physical_event_rule(monkeypatch):
    texts=[rendered(monkeypatch,v)for v in ('observe','audit')]
    assert all(text.count(CORE)==1 for text in texts)
    assert '分离可见的交互即使最终只有一次业务响应也必须分别保留'not in texts[0]
    for text in texts:
        assert re.search(r'不.*标记.*重现.*物理.*次数',text)
        assert re.search(r'有证据的独立.*重试.*确认.*返回.*保留',text)
        assert re.search(r'原始.*聚焦.*证据.*保留',text)
    assert re.search(r'预备聚焦.*清空.*输入',texts[0])


def test_edit_modality_and_terminal_claims_keep_negative_limits(monkeypatch):
    observer=rendered(monkeypatch,'observe');audit=rendered(monkeypatch,'audit')
    for text in (observer,audit):
        assert 'trigger_kind=edit'in text and re.search(r'不要求.*新触点',text)
        assert re.search(r'最早.*文字修改',text) and re.search(r'触发帧过晚.*仍不合格',text)
        assert re.search(r'仅用outcome最后一帧.*终态谓词',text)
        assert re.search(r'中间.*布局.*不同.*不是矛盾',text)
        assert re.search(r'末帧不支持.*仍不合格',text)
    assert '证据冲突才回看'not in observer
    for text in (observer,audit):
        assert re.search(r'缺少必要触发证据.*也应回看',text) and '无需先证明新触点'in text
    assert 'value保持空串'in observer and 'continuation_of逐字复用'in observer
    assert '光标/键盘状态本身不能补造聚焦点击'in observer
    assert re.search(r'不.*自动清空历史疑问',audit)


@pytest.mark.parametrize('version',['observe','audit'])
@pytest.mark.parametrize('bounded',[False,True])
def test_rendering_preserves_original_schema_example_payload_and_tool_capability(monkeypatch,version,bounded):
    payload={'raw_question':'literal {{revisit_contract}} "question" stays', 'frame_refs':['original-a','original-b']}
    original=copy.deepcopy(payload);text=rendered(monkeypatch,version,bounded,payload)
    actual_payload=json.JSONDecoder().raw_decode(text.split('\n',1)[1])[0]
    assert actual_payload=={'revisit_context_anchors':bounded,**payload}and payload==original
    assert text.count('{{revisit_contract}}')==1 # only the literal inside raw data survives
    assert ('context端点仅作定位'in text)is bounded
    raw=example(text);assert raw==EXAMPLES[version]
    jsonschema.validate(raw,json.loads((ROOT/'ai_skills/schemas/ui_video_trajectory.schema.json').read_text()))
