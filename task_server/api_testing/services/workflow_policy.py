"""Deterministic lifecycle policies for generated API case workflows."""

from typing import Mapping


PRINT_TASK_VARIABLE_NAMES = frozenset(
    {
        "printSn",
        "printTaskSn",
        "printJobSn",
        "printId",
        "printTaskId",
        "taskId",
    }
)


def _endpoint_text(endpoint):
    tags = _value(endpoint, "tags", ()) or ()
    if isinstance(tags, str):
        tags = (tags,)
    return " ".join(
        [
            str(_value(endpoint, "summary", "") or ""),
            str(_value(endpoint, "path", "") or ""),
            *(str(item) for item in tags),
        ]
    ).lower()


def _workflow(
    kind,
    label,
    risk,
    *,
    requires_setup,
    requires_cleanup,
    baseline_policy,
    reason,
):
    return {
        "kind": kind,
        "label": label,
        "risk": risk,
        "requires_setup": requires_setup,
        "requires_cleanup": requires_cleanup,
        "baseline_policy": baseline_policy,
        "reason": reason,
    }


def classify_endpoint_workflow(endpoint):
    """Describe lifecycle requirements without inventing endpoint relationships."""

    method = str(_value(endpoint, "method", "GET") or "GET").upper()
    text = _endpoint_text(endpoint)
    read_only = method in {"GET", "HEAD", "OPTIONS"}

    if is_print_dispatch_endpoint(endpoint):
        return _workflow(
            "print_lifecycle",
            "打印生命周期",
            "high",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="动态获取设备和切片产物；打印成功后必须取消本次打印",
        )
    if not read_only and any(label in text for label in ("积分兑换", "账号注销", "真实扣费", "正式支付")):
        return _workflow(
            "irreversible",
            "不可逆业务",
            "critical",
            requires_setup=False,
            requires_cleanup=False,
            baseline_policy="excluded",
            reason="操作不可可靠回滚，默认排除定时基线，仅保留人工验证",
        )
    if not read_only and any(label in text for label in ("切片", "slice")):
        return _workflow(
            "slice_lifecycle",
            "切片生命周期",
            "high",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="动态选择模型和在线设备，并回收本次切片任务及产物",
        )
    if any(label in text for label in ("收藏", "关注", "点赞", "favorite", "follow", "like")) and not read_only:
        return _workflow(
            "reversible_state",
            "可逆状态变更",
            "medium",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="先查询执行前状态，主体变更后必须恢复原状态",
        )
    if any(label in text for label in ("设备", "device", "打印机", "printer")) and not read_only:
        return _workflow(
            "device_control",
            "设备控制",
            "high",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="先确认设备在线并读取原设置，执行后取消动作或恢复原值",
        )
    if not read_only and any(label in text for label in ("上传", "upload", "生成模型", "文件生成")):
        return _workflow(
            "file_lifecycle",
            "文件或生成任务",
            "medium",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="准备稳定测试素材，校验产物后删除本次文件或生成任务",
        )
    if any(label in text for label in ("验证码", "登录", "oauth", "login", "短信")):
        return _workflow(
            "authentication",
            "登录与鉴权",
            "high",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="manual",
            reason="必须使用可刷新测试凭证并在完成后清理临时会话",
        )
    if method == "DELETE" or any(label in text for label in ("删除", "移除", "/delete", "/remove")):
        return _workflow(
            "delete_resource",
            "删除资源",
            "high",
            requires_setup=True,
            requires_cleanup=False,
            baseline_policy="guarded",
            reason="前置创建本次专用临时资源，禁止删除列表中已有的历史数据",
        )
    if any(label in text for label in ("异步", "任务状态", "进度", "status", "progress")) and not read_only:
        return _workflow(
            "async_task",
            "异步任务",
            "medium",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="提取本次任务标识并轮询业务终态，超时后取消任务和清理产物",
        )
    if method == "POST" and any(label in text for label in ("新增", "创建", "添加", "/add", "/create")):
        return _workflow(
            "create_resource",
            "创建资源",
            "medium",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="使用本次唯一数据创建并查询验证，完成后删除本次创建的资源",
        )
    if method in {"PUT", "PATCH"} or any(label in text for label in ("修改", "更新", "设置", "/update")) and method not in {"GET", "HEAD", "OPTIONS"}:
        return _workflow(
            "update_resource",
            "修改资源",
            "medium",
            requires_setup=True,
            requires_cleanup=True,
            baseline_policy="guarded",
            reason="先读取并保存原值，修改验证后恢复原值并再次确认",
        )
    if read_only:
        detail_markers = ("详情", "状态", "detail", "/info", "/status", "{id}", "{sn}")
        if any(label in text for label in detail_markers):
            return _workflow(
                "resource_query",
                "资源详情查询",
                "low",
                requires_setup=True,
                requires_cleanup=False,
                baseline_policy="direct",
                reason="无业务状态变更；优先从同资源列表动态提取有效标识，固定标识失效会以回归失败暴露",
            )
        return _workflow(
            "read_only",
            "只读查询",
            "low",
            requires_setup=False,
            requires_cleanup=False,
            baseline_policy="direct",
            reason="无业务状态变更，可直接校验业务码、结构和关键数据字段",
        )
    return _workflow(
        "unclassified_mutation",
        "待识别变更",
        "high",
        requires_setup=True,
        requires_cleanup=True,
        baseline_policy="manual",
        reason="无法可靠确定资源关系，保存前需人工补全前置、断言和清理步骤",
    )


def _value(item, name, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def is_print_dispatch_endpoint(endpoint):
    path = str(getattr(endpoint, "path", "") or "").lower()
    summary = str(getattr(endpoint, "summary", "") or "").lower()
    if any(label in summary for label in ("下发打印", "开始打印", "发起打印")):
        return True
    print_context = any(label in path for label in ("printjob", "print-job", "/print/"))
    action = path.rstrip("/").rsplit("/", 1)[-1]
    return print_context and action in {"print", "start", "submit", "dispatch"}


def is_print_cancel_endpoint(endpoint):
    path = str(getattr(endpoint, "path", "") or "").lower()
    summary = str(getattr(endpoint, "summary", "") or "").lower()
    if any(label in summary for label in ("取消打印", "停止打印", "终止打印")):
        return True
    print_context = any(label in path for label in ("printjob", "print-job", "/print/"))
    action = path.rstrip("/").rsplit("/", 1)[-1]
    return print_context and action in {"cancel", "stop", "terminate"}


def print_task_extraction_targets(extractions):
    targets = set()
    for extraction in extractions:
        target = _value(extraction, "target", "")
        if target in PRINT_TASK_VARIABLE_NAMES:
            targets.add(target)
    return targets


def is_print_cancel_step(step, task_targets):
    if not _value(step, "enabled", True):
        return False
    request = _value(step, "request", {})
    path = str(_value(request, "path", "") or "").lower()
    name = str(_value(step, "name", "") or "").lower()
    has_cancel_semantics = any(
        label in f"{name} {path}"
        for label in ("取消打印", "停止打印", "cancelprint", "cancel-print", "stopprint", "stop-print")
    ) or (
        "print" in path
        and path.rstrip("/").rsplit("/", 1)[-1] in {"cancel", "stop", "terminate"}
    )
    required = set(_value(step, "required_variables", []) or [])
    return has_cancel_semantics and bool(required & set(task_targets))
