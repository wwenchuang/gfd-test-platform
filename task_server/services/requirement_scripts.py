"""Compile requirement-specific QA scripts without publishing them as shared baselines."""
import hashlib
import json
import re
from contextvars import ContextVar

import yaml

from .ui_case_draft_contract import SHORT_TIMEOUT_MS, click_target_condition
from .yaml_static_validator import validate_yaml_static_executable
from .yaml_executable_scorer import score_midscene_yaml_executable
from .yaml_service import dry_run_midscene_yaml

# Only the integration's validated, frozen build plan supplies assets to batch preparation.
active_assets = ContextVar('requirement_script_assets', default=None)

INSTRUCTIONS = """
当前执行器能启动 App、按可见文字或有明显特征的图案点击、等待页面和截图核验。plan.kind 按可用取证能力分类。
截图核对页面、视觉断言、预设图选中态、按钮状态、选择后的界面变化属于 ui。
manual 只用于真实人工前置、无法通过界面取得的证据或有副作用的操作。
从输入提供的入口开始，为至少一个有依据的正向流程产出完整导航。每条用例从独立启动开始，
每一步核对该时刻的状态；expected 只写最后可观察的状态。进入前的状态放在前置或对应步骤，避免在动作完成后同时断言互斥状态。避免依赖其他用例。navigation 必须覆盖准备导航和用例 steps 中每一次业务点击，包括进入页面后再选择预设图等动作；点击目标采用实际控件原文，最后一次点击后的 visible 写目标页状态。输入有 App 竖屏且使用通用业务控件时，给出 Android、iOS 候选，实测分别取证。
为每条移动 UI 用例额外提供 navigation 对象，键为有资料支持的 android 或 ios。
每端值是从应用启动开始、只通过可见文字入口到达目标的有序数组，每项为
{"label":"单个控件的原文","visible":"点击后可见的具体业务状态"}。
只查看已存在的页面或数据，最后将核对用例 expected。需要输入、提交、外跳、改数据、
真实打印或人工准备而无法由当前步骤证明的场景，省略对应端并写明依赖。
以 PRD 和提供的设计图片为依据。参考脚本仅用于了解写法，不得照搬无关入口。
"""
# A tap target must be one visible label, never a model-supplied action or compound instruction.
LABEL = re.compile(r'[\w\u4e00-\u9fff ·（）()《》-]{1,48}')
MUTATION = re.compile(r'删除|清空|支付|购买|下单|提交|发送|确认|确定|取消|开印|开始打印|立即打印|去打印|重打|恢复出厂|解绑|退出登录|保存|授权|安装|升级|充值|同意|分享至|delete|submit|confirm|purchase|send|print now',re.I)


def prepare(case, app_id, platform):
    plan = case.get('plan') or {}
    paths = case.get('navigation')
    path = paths.get(platform) if isinstance(paths,dict) else None
    if plan.get('kind') != 'ui' or platform not in plan.get('platforms',[]) or not isinstance(path,list) or not 1 <= len(path) <= 12:
        return {'ready':False,'reason':'缺少该端可执行的导航路径'}
    expected = case['expected']
    if re.search(r'初始(?:时|状态|情况下).*?(?:点击|选择|切换).*?后',expected):
        return {'ready':False,'reason':'需要把操作前后的状态分别断言'}
    flow = [{'launch':app_id}]
    roles = ['launch']
    for entry in path:
        if not isinstance(entry,dict) or set(entry) != {'label','visible'}:
            return {'ready':False,'reason':'导航步骤结构不完整'}
        label, visible = entry['label'], entry['visible']
        if not isinstance(label,str) or not LABEL.fullmatch(label) or MUTATION.search(label):
            return {'ready':False,'reason':'该操作超出查看页面的执行范围'}
        if not isinstance(visible,str) or not 3 <= len(visible.strip()) <= 300:
            return {'ready':False,'reason':'缺少点击后的可见预期'}
        flow.extend([{'aiWaitFor':click_target_condition(label),'timeout':SHORT_TIMEOUT_MS},
                     {'aiTap':label},{'aiWaitFor':visible,'timeout':SHORT_TIMEOUT_MS}])
        roles.extend(['guard','business','guard'])
    labels = [item['label'] for item in path]
    for instruction in case.get('steps',[]):
        # A quoted control immediately following a tap/select verb must actually be operated on.
        for match in re.finditer(r'(?:点击|轻点|轻触|选择|点选|切换至|勾选)[^。；，、“「《”」》]{0,16}[“「《]([^”」》]{1,48})[”」》]',instruction):
            label = match.group(1)
            if not any(label in target for target in labels):
                return {'ready':False,'reason':'脚本遗漏业务操作：'+label}
    flow.append({'aiAssert':case['expected']})
    roles.append('business')
    source = yaml.safe_dump({'android':{},'tasks':[{'name':case['title'],'flow':flow}]},
                            allow_unicode=True, sort_keys=False, width=65535)
    static = validate_yaml_static_executable(source,strict=True)
    score = score_midscene_yaml_executable(source,generated=True,compiler_roles=[roles])
    dry = dry_run_midscene_yaml(source,app_package=app_id)
    errors = (static['errors'] + static['warnings'] + score['errors'] + dry['errors'])
    if not (static['ok'] and not static['warnings'] and score['ok'] and dry['ok']):
        return {'ready':False,'reason':'；'.join(errors or score['warnings'] or ['脚本未达到执行条件'])[:500]}
    return {'ready':True,'yaml_content':source,'revision':hashlib.sha256(source.encode()).hexdigest(),
            'score':score['score'],'dry_run':dry['mode']}


def build_plan(cfg, design, sid, platform, revision):
    target = cfg.get('generated_targets',{}).get(platform)
    if not target:
        return {'status':'awaiting_qa_target'}
    assets = {}
    deferred = []
    for case in design['cases']:
        script = case.get('scripts',{}).get(platform,{})
        if not script.get('ready'):
            deferred.append({'title':case.get('title',case['id']),
                             'reason':script.get('reason') or '本端没有可执行 UI 步骤'})
            continue
        case_id = 'req-'+sid[:24]+'-'+case['id']+'-'+platform
        assets[case_id] = dict(case_id=case_id,app_package=cfg['app_id'],platform=platform,
            task_name=case['title'],goal=case['title'],expected=case['expected'],status='requirement_script',
            generated_plan=revision,source_id=sid,design_case=case['id'],yaml_content=script['yaml_content'],
            revision=script['revision'])
    if not assets:
        return {'status':'no_admitted_scripts'}
    cases = [{'case_id':a['case_id'],'revision':a['revision']} for a in assets.values()]
    # Start with one case so even a two-case feature has a distinct gated regression phase.
    request = dict(app_id=cfg['app_id'],cases=cases[:1],targets=[dict(target,platform=platform)],
                   notifications={'enabled':False},release_report={'enabled':True})
    return {'smoke':request,'remaining':cases[1:],'assets':assets,'deferred':deferred}


def validate_snapshot(batch, attempt, snapshot):
    from . import requirement_design
    from .ui_case_compiler import compile_ui_case
    from .ui_attempt_progress import stable_step_ids
    meta = snapshot['metadata']
    with requirement_design._db() as db:
        row = db.execute('SELECT plan FROM feishu_qa_plans WHERE owner=? AND app=? AND revision=?',
                         (batch['owner_user_id'],batch['app_id'],meta['generated_plan'])).fetchone()
    if not row:
        return False
    plan = json.loads(row['plan'])
    asset = plan.get('assets',{}).get(attempt['case_id'])
    build = batch['selection'].get('expected_build')
    if not asset or not build or plan.get('build') != build or plan['smoke']['targets'] != batch['selection']['targets']:
        return False
    source = asset['yaml_content']
    revision = hashlib.sha256(source.encode()).hexdigest()
    if (revision != attempt['case_revision'] or source != snapshot['source_yaml'] or
            asset['platform'] != attempt['platform']):
        return False
    compiled = compile_ui_case({'yaml_content':source,'step_ids':stable_step_ids(asset['case_id'],revision,source)},
                               attempt['target_resolution'])
    return compiled['ok'] and compiled == attempt['compiled_case']


def installed_qa(owner, username, sid, design_id, platform, expected_version):
    """Exercise an owned workbench design against a server-registered QA device."""
    from . import requirement_design, requirement_intake as intake, feishu_build_plan
    from .ui_execution_contract import require_ui_actor, resolve_ui_target
    from .operation_attribution import current_actor
    source, report = requirement_design.completed_design(owner,sid,design_id)
    ref = source.get('requirement_ref') or {}
    if ref.get('source') != 'workbench' or report['status'] != 'completed':
        raise ValueError('请选择本账号已完成设计的工作台需求')
    app = ref['app_id']
    actor = current_actor()
    require_ui_actor(actor, app_id=app)
    config = requirement_design._root() / 'requirement-qa-targets.json'
    targets = json.loads(config.read_text()) if config.is_file() else {}
    target = targets.get(app,{}).get(platform)
    if not target:
        raise ValueError('该应用尚未登记此平台的 QA 测试机')
    selection = dict(target,platform=platform)
    resolved = resolve_ui_target(app,selection,actor)
    versions = (resolved.get('device_snapshot') or {}).get('app_versions',[])
    if not resolved.get('ok') or len(versions)!=1 or versions[0].get('installed') is not True:
        raise ValueError('无法核对指定 QA 机上的应用版本')
    v = versions[0]
    build = dict(package=resolved['target_package'],version_name=str(v['version_name']),
                 version_code=str(v['version_code']),build_id='installed-'+str(v['version_code']),environment='qa')
    if expected_version != build['version_name']+' ('+build['version_code']+')':
        raise ValueError('机上 QA 版本已经变化，请核对后重新发起')
    event = dict(ref,title=source['title'],acceptance_criteria=[c['text'] for c in source['clauses']])
    cfg = dict(user_id=owner,username=username,app_id=app,generated_targets={platform:target})
    identity = dict(build,source_id=sid,design_id=design_id,target=selection)
    return feishu_build_plan.submit_design(cfg,event,sid,platform,identity,report['result'],origin='workbench')
