"""Current defaults roll back to 3.7 while historical 3.8 metadata remains valid."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_unconfigured_python_text_and_visual_defaults_are_qwen37():
    env = {key: value for key, value in os.environ.items() if key not in ('DASHSCOPE_MODEL', 'DASHSCOPE_VL_MODEL', 'MIDSCENE_MODEL_NAME')}
    env['MIDSCENE_ENV_FILE'] = '/nonexistent/qwen-default-test.env'
    code = 'from task_server.config import dashscope_text_model, dashscope_vl_model; import json; print(json.dumps([dashscope_text_model(), dashscope_vl_model()]))'
    result = subprocess.run([sys.executable, '-c', code], env=env, cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ['qwen3.7-plus', 'qwen3.7-plus']


@pytest.mark.parametrize('filename', ['mac-midscene-runner.py', 'windows-midscene-runner.py', 'task_server/services/runner_service.py'])
@pytest.mark.parametrize('family', ['', 'qwen2.5-vl'])
def test_qwen37_remains_on_official_qwen3_family(filename, family):
    tree = ast.parse((ROOT / filename).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'infer_midscene_model_family')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    assert namespace['infer_midscene_model_family']('qwen3.7-plus', family) == 'qwen3'


def test_active_max_video_stages_use_qwen37_and_flash_stays_explicit():
    from task_server.services.ui_video_stage_policy import STAGES
    assert {config[3] for stage, config in STAGES.items() if stage not in ('overview', 'semantic')} == {'qwen3.7-plus'}
    assert {STAGES[stage][3] for stage in ('overview', 'semantic')} == {'qwen3.8-flash'}


@pytest.mark.parametrize('model', ['qwen3.7-plus', 'qwen3.8-max', 'qwen3.8-flash'])
def test_actual_model_receipts_preserve_current_and_historical_models(model):
    from task_server.services.ui_video_records import safe_record
    from task_server.services.ui_video_model_jobs import _safe_trace
    from task_server.services.ui_video_stage_receipts import safe_attestation
    raw = {'content': 'historical response', 'gateway_request_sha256': '0' * 64,
           'providerId': 'official', 'model': model, 'selectedModel': model}
    assert safe_record('receipt', raw)['model'] == model
    assert _safe_trace(raw)['selectedModel'] == model
    assert safe_attestation({'model': model})['model'] == model


@pytest.mark.parametrize('model', ['qwen3.7-plus', 'qwen3.8-max'])
def test_native_receipt_accepts_current_and_historical_visual_models(model):
    from task_server.services.ui_video_stage_receipts import _safe_native_transport
    assert _safe_native_transport({'model': model})['model'] == model


def test_qwen37_fine_detail_allows_only_explicit_bounded_thinking(monkeypatch):
    from task_server.services import ai_skill_service as ai
    monkeypatch.setattr(ai, 'dashscope_model_for_images', lambda _: 'qwen3.7-plus')
    monkeypatch.setattr(ai, 'dashscope_base_url', lambda: 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    result = ai.build_dashscope_chat_body('observe', image_assets=[{'name': 'frame-000', 'mime': 'image/jpeg', 'base64': 'YQ=='}],
        bounded_trajectory_detail=True, bounded_trajectory_reasoning=True)
    assert result['model'] == 'qwen3.7-plus'
    assert result['enable_thinking'] is True
    assert result['thinking_budget'] == 1024
    assert result['messages'][1]['content'][1]['min_pixels'] == 1658880
