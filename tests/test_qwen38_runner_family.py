import ast
from pathlib import Path
import pytest

@pytest.mark.parametrize('filename',['mac-midscene-runner.py','windows-midscene-runner.py','task_server/services/runner_service.py'])
@pytest.mark.parametrize('family',['','qwen2.5-vl'])
def test_qwen38_uses_official_qwen3_localization_adapter(filename,family):
    tree=ast.parse((Path(__file__).parents[1]/filename).read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='infer_midscene_model_family')
    ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),filename,'exec'),ns)
    assert ns['infer_midscene_model_family']('qwen3.8-max',family)=='qwen3'
