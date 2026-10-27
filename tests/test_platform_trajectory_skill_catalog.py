"""Only the trajectory observer has a required three-stage prompt catalog."""
import pytest
from task_server.services import platform_service as platform


def catalog(tmp_path,monkeypatch,skill,versions):
    (tmp_path/'prompts').mkdir();(tmp_path/'schemas').mkdir()
    for version in versions:(tmp_path/'prompts'/f'{skill}.{version}.md').write_text('prompt')
    (tmp_path/'schemas'/f'{skill}.schema.json').write_text('{}')
    monkeypatch.setattr(platform,'AI_SKILLS_DIR',str(tmp_path))
    return platform.ai_skills_status()


def test_complete_trajectory_stages_are_ready_without_v1(tmp_path,monkeypatch):
    result=catalog(tmp_path,monkeypatch,'ui_video_trajectory',['overview','observe','audit'])
    assert result['ready'] is True
    assert result['missing_prompts']==[] and result['missing_schemas']==[]
    assert result['skills']==['ui_video_trajectory'] and result['prompt_count']==1


@pytest.mark.parametrize('missing',['overview','observe','audit'])
def test_missing_trajectory_stage_is_not_ready(tmp_path,monkeypatch,missing):
    result=catalog(tmp_path,monkeypatch,'ui_video_trajectory',[p for p in ('overview','observe','audit') if p!=missing])
    assert result['ready'] is False and result['missing_prompts']==['ui_video_trajectory']


def test_v1_cannot_replace_trajectory_stages(tmp_path,monkeypatch):
    result=catalog(tmp_path,monkeypatch,'ui_video_trajectory',['v1'])
    assert result['ready'] is False and result['missing_prompts']==['ui_video_trajectory']


@pytest.mark.parametrize('versions,ready',[(['overview','observe','audit'],False),(['v1'],True)])
def test_other_skills_keep_v1_requirement(tmp_path,monkeypatch,versions,ready):
    result=catalog(tmp_path,monkeypatch,'ordinary_skill',versions)
    assert result['ready'] is ready
    assert result['missing_prompts']==([] if ready else ['ordinary_skill'])
