from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_flow(relative_path: str):
    data = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
    return data["tasks"][0]["flow"]


def _step_text(step) -> str:
    if isinstance(step, dict):
        return "\n".join(str(value) for value in step.values())
    return str(step)


def _flow_text(relative_path: str) -> str:
    return "\n".join(_step_text(step) for step in _load_flow(relative_path))


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def test_obj_bowling_uses_observed_go_print_button_instead_of_hard_next_step():
    flow = _load_flow("server-tasks/3D打印基线/OBJ保龄球打印.yaml")
    texts = [_step_text(step) for step in flow]

    require(
        not any("根据当前页面底部主按钮继续打印流程" in text for text in texts),
        "OBJ bowling flow must not use a broad conditional ai action that can replan-tap 去打印 repeatedly",
    )
    require(
        any("加载中" in text and "去打印" in text and "下一步" in text for text in texts),
        "OBJ bowling flow must accept either 去打印 or 下一步 after the first detail-page print tap",
    )
    require(
        any("保龄球模型详情页" in text and "加载中" in text and "去打印" in text for text in texts),
        "OBJ bowling flow must wait for the model-detail loading overlay to disappear before tapping the first 去打印",
    )
    require(
        any("只判断当前截图" in text and "最多点击一次" in text and "去打印" in text and "下一步" in text for text in texts),
        "OBJ bowling flow must handle the optional post-preview 去打印 as a one-shot pre-next action",
    )
    require(
        any("下一步" in text and "蓝色可点击状态" in text for text in texts),
        "OBJ bowling flow must wait for 下一步 to become enabled after tapping 去打印",
    )
    require(
        any(text.strip() == "底部蓝色「下一步」按钮" for text in texts),
        "OBJ bowling flow must tap the observed bottom 下一步 button explicitly after it is enabled",
    )
    go_print_index = next((i for i, text in enumerate(texts) if "只判断当前截图" in text and "最多点击一次" in text), -1)
    material_index = next((i for i, text in enumerate(texts) if "请确认耗材颜色" in text and "则点击蓝色「确认」按钮" in text), -1)
    next_wait_index = next((i for i, text in enumerate(texts) if "下一步" in text and "蓝色可点击状态" in text), -1)
    require(
        go_print_index != -1 and material_index != -1 and next_wait_index != -1 and go_print_index < material_index < next_wait_index,
        "OBJ bowling flow must confirm material color immediately after the optional 去打印 before waiting for 下一步",
    )
    next_tap_index = next((i for i, text in enumerate(texts) if text.strip() == "底部蓝色「下一步」按钮"), -1)
    post_next_material_index = next(
        (
            i
            for i, text in enumerate(texts)
            if "请确认耗材颜色" in text and "右下角蓝色「确认」按钮" in text
        ),
        -1,
    )
    cancel_wait_index = next((i for i, text in enumerate(texts) if "取消打印" in text and "模型处理进度完成" in text), -1)
    require(
        next_tap_index != -1
        and post_next_material_index != -1
        and cancel_wait_index != -1
        and next_tap_index < post_next_material_index < cancel_wait_index,
        "OBJ bowling flow must handle the material-color dialog that can appear after tapping 下一步 before waiting for 取消打印",
    )
    require(
        any("页面已进入模型处理或打印预览流程" in text and "仍在模型打印编辑页" in text and "下一步" in text for text in texts),
        "OBJ bowling flow must accept the post-下一步 state where the app remains on the edit page",
    )
    post_confirm_next_index = next(
        (
            i
            for i, text in enumerate(texts)
            if "确认耗材后" in text and "再点击一次" in text and "下一步" in text
        ),
        -1,
    )
    require(
        post_next_material_index != -1
        and post_confirm_next_index != -1
        and cancel_wait_index != -1
        and post_next_material_index < post_confirm_next_index < cancel_wait_index,
        "OBJ bowling flow must tap 下一步 again when the material confirmation returns to the edit page",
    )
    post_retry_material_index = next((i for i, text in enumerate(texts) if "再次弹出「请确认耗材颜色」" in text), -1)
    require(
        post_confirm_next_index != -1
        and post_retry_material_index != -1
        and cancel_wait_index != -1
        and post_confirm_next_index < post_retry_material_index < cancel_wait_index,
        "OBJ bowling flow must handle a material-color dialog that appears after the retry 下一步",
    )
    require(
        not any("只执行一次" in text for text in texts),
        "OBJ bowling flow must not rely on a one-shot ai fallback that missed the enabled 下一步 state online",
    )
    require(
        not any("底部出现可点击的「下一步」按钮" in text for text in texts),
        "OBJ bowling flow must not hard-wait for 下一步 after Sonic report showed 去打印",
    )
    require(
        not any(text.strip() == "下一步" for text in texts),
        "OBJ bowling flow must not blindly tap 下一步",
    )
    require(
        not any("如果当前页面有返回按钮且不是 App 首页" in text for text in texts),
        "OBJ bowling flow must not run a broad final return action after the print/cancel flow has already been verified",
    )


def test_stamp_flows_exit_print_preview_with_repeated_cancel_guard():
    for relative_path in (
        "server-tasks/3D打印基线/十二生肖印章打印.yaml",
        "server-tasks-all/3D打印基线/十二生肖印章打印.yaml",
        "server-tasks/3D打印基线/标牌打印.yaml",
        "server-tasks-all/3D打印基线/标牌打印.yaml",
        "server-tasks/3D打印基线/普通印章打印.yaml",
        "server-tasks-all/3D打印基线/普通印章打印.yaml",
        "server-tasks/3D打印基线/姓名牌打印.yaml",
        "server-tasks-all/3D打印基线/姓名牌打印.yaml",
    ):
        flow = _load_flow(relative_path)
        texts = [_step_text(step) for step in flow]
        text = "\n".join(texts)
        require(
            not any("根据当前页面底部主按钮继续打印流程" in item for item in texts),
            f"{relative_path} must not use a broad conditional ai action that can replan-tap the print button repeatedly",
        )
        if "十二生肖" in relative_path:
            require(
                any("加载中" in item and "去打印" in item and "下一步" in item for item in texts),
                f"{relative_path} must wait for print-preview loading to finish before tapping the next print button",
            )
            require(
                any(item.strip() == "底部蓝色「下一步」按钮" for item in texts),
                f"{relative_path} must use the next-button flow proven by the online success report",
            )
            require(
                not any("底部出现可点击的「下一步」按钮" in item for item in texts),
                f"{relative_path} must not hard-wait for 下一步 in the print-flow transition",
            )
            require(
                not any(item.strip() == "下一步" for item in texts),
                f"{relative_path} must not blindly tap 下一步",
            )
            require(
                any("确认耗材后" in item and "再点击一次" in item and "下一步" in item for item in texts),
                f"{relative_path} must retry 下一步 when material confirmation returns to the edit page",
            )
            require(
                any("再次弹出「请确认耗材颜色」" in item for item in texts),
                f"{relative_path} must handle a material-color dialog that appears after the retry 下一步",
            )
        if "普通印章" in relative_path:
            require(
                any("生成完成" in item and "去打印" in item and "模型打印编辑" in item and "下一步" in item for item in texts),
                f"{relative_path} must accept direct navigation to the model edit page after preview generation",
            )
            require(
                any("未设置耗材" in item and "耗材" in item and "下一步" in item for item in texts),
                f"{relative_path} must recover when the model edit page opens with 未设置耗材 and no 下一步 button",
            )
            require(
                any("只判断当前截图" in item and "去打印" in item and "下一步" in item for item in texts),
                f"{relative_path} must not blindly tap 去打印 after preview generation",
            )
            require(
                not any(item.strip() == "去打印" for item in texts),
                f"{relative_path} must not blindly tap 去打印 after online report showed the edit page",
            )
            require(
                any(item.strip() == "底部蓝色「下一步」按钮" for item in texts),
                f"{relative_path} must tap the observed bottom 下一步 button explicitly",
            )
            require(
                any("确认耗材后" in item and "再点击一次" in item and "下一步" in item for item in texts),
                f"{relative_path} must retry 下一步 when material confirmation returns to the edit page",
            )
            require(
                any("网络连接超时" in item and "可安全重试" in item and "下一步" in item for item in texts),
                f"{relative_path} must retry 下一步 after the online report showed a network-timeout toast on the edit page",
            )
            require(
                not any(item.strip() == "下一步" for item in texts),
                f"{relative_path} must not blindly tap 下一步",
            )
            require(
                any("已取消模型处理" in item and "取消打印" in item and "取消处理" in item for item in texts),
                f"{relative_path} must treat already-cancelled processing as a valid cleanup state",
            )
            require(
                any("再次弹出「请确认耗材颜色」" in item for item in texts),
                f"{relative_path} must handle a material-color dialog that appears after the retry 下一步",
            )
            require(
                not any(item.strip() == "取消打印" for item in texts),
                f"{relative_path} must not blindly tap 取消打印 after the online report showed an already-cancelled state",
            )
        if "姓名牌" in relative_path:
            require(
                "首页遗留切片任务清理" not in text,
                f"{relative_path} must not use the broad startup cleanup action that can enter an old print task",
            )
            require(
                any("文字印章" in item and "编辑模型" in item and "加载中" in item and "暂无可编辑参数" in item and "文本输入框" in item for item in texts),
                f"{relative_path} must wait for the editor to finish loading before entering text",
            )
            require(
                any("生成完成" in item and "去打印" in item and "模型打印编辑" in item and "下一步" in item for item in texts),
                f"{relative_path} must accept direct navigation to the model edit page after model generation",
            )
            require(
                any(item.strip() == "底部蓝色「下一步」按钮" for item in texts),
                f"{relative_path} must tap the observed bottom 下一步 button explicitly",
            )
            require(
                any("确认耗材后" in item and "再点击一次" in item and "下一步" in item for item in texts),
                f"{relative_path} must retry 下一步 when material confirmation returns to the edit page",
            )
            require(
                any("网络连接超时" in item and "可安全重试" in item and "下一步" in item for item in texts),
                f"{relative_path} must retry 下一步 after the online report showed a network-timeout toast on the edit page",
            )
            require(
                any("再次弹出「请确认耗材颜色」" in item for item in texts),
                f"{relative_path} must handle a material-color dialog that appears after the retry 下一步",
            )
        if "标牌" in relative_path:
            require(
                any("回顶部" in item and "模型库顶部" in item and "横向功能入口" in item for item in texts),
                f"{relative_path} must return to the model-library top when launch restores a scrolled waterfall list",
            )
            require(
                "input swipe 950 1080 150 1080 500" not in text,
                f"{relative_path} must not use the coordinate swipe that can open the model-import dialog",
            )
            require(
                "如果误进入 AI建模页" not in text,
                f"{relative_path} must not use a broad recovery action after tapping the sign entry",
            )
            require(
                any("最右侧" in item and "绿色" in item and "GO" in item and "标牌" in item for item in texts),
                f"{relative_path} must locate the specific green GO sign entry instead of the whole icon row",
            )
            require(
                any("生成完成" in item and "去打印" in item and "模型打印编辑" in item and "下一步" in item for item in texts),
                f"{relative_path} must accept direct navigation to the model edit page after preview generation",
            )
            require(
                any(item.strip() == "底部蓝色「下一步」按钮" for item in texts),
                f"{relative_path} must tap the observed bottom 下一步 button explicitly",
            )
            require(
                any("确认耗材后" in item and "再点击一次" in item and "下一步" in item for item in texts),
                f"{relative_path} must retry 下一步 when material confirmation returns to the edit page",
            )
            require(
                any("再次弹出「请确认耗材颜色」" in item for item in texts),
                f"{relative_path} must handle a material-color dialog that appears after the retry 下一步",
            )
            require(
                not any(item.strip() == "下一步" for item in texts),
                f"{relative_path} must not blindly tap 下一步",
            )
        require(
            "再次点击「取消打印」" in text,
            f"{relative_path} must retry cancel when still on print preview",
        )
        require(
            "底部不再同时显示「取消打印」和「确认打印」按钮" in text,
            f"{relative_path} must assert the observed print-preview button pair is gone",
        )
        require(
            "已退出打印流程，页面出现返回按钮或回到模型详情相关页面" not in text,
            f"{relative_path} must not use the overly broad exit assertion that failed online",
        )


def test_local_import_accepts_android_file_picker_and_cancel_guard():
    for relative_path in (
        "server-tasks/3D打印基线/模型导入-本地导入.yaml",
        "server-tasks-all/3D打印基线/模型导入-本地导入.yaml",
    ):
        texts = [_step_text(step) for step in _load_flow(relative_path)]
        text = "\n".join(texts)
        require(
            any("下载内容" in item and "右上角放大镜搜索图标" in item for item in texts),
            f"{relative_path} must accept the observed Android file picker title 下载内容",
        )
        require(
            "模型处理进度完成，页面出现可点击的「取消打印」按钮" not in text,
            f"{relative_path} must stop at the local-import edit page instead of entering the print/cancel flow",
        )
        require(
            any("模型打印编辑页" in item and "3D 模型预览" in item and "下一步" in item for item in texts),
            f"{relative_path} must verify the imported model reaches the edit page",
        )
        require(
            "已退出打印流程，页面出现返回按钮或回到模型导入相关页面" not in text,
            f"{relative_path} must not use the overly broad import exit assertion",
        )
        require(
            any("本地导入收尾" in item and "不要点击「下一步」" in item for item in texts),
            f"{relative_path} must leave the model edit page without entering the print flow",
        )
        require(
            any("App 首页已加载完成" in item and "模型导入" in item for item in texts[-5:]),
            f"{relative_path} must verify it returned to the app home before force-stopping",
        )


def test_wechat_import_uses_bounded_cancel_cleanup():
    for relative_path in (
        "server-tasks/3D打印基线/模型导入-微信导入.yaml",
        "server-tasks-all/3D打印基线/模型导入-微信导入.yaml",
    ):
        texts = [_step_text(step) for step in _load_flow(relative_path)]
        text = "\n".join(texts)
        require(
            not any(item.strip() == "取消打印" for item in texts),
            f"{relative_path} must not blindly tap 取消打印; online qwen returned an invalid scroll bbox while trying to find it",
        )
        require(
            "再次点击「取消打印」" in text,
            f"{relative_path} must retry cancel only when the print-preview button remains visible",
        )
        require(
            "底部不再同时显示「取消打印」和「确认打印」按钮" in text,
            f"{relative_path} must assert the print-preview button pair is gone after bounded cleanup",
        )
        require(
            any("模型导入后的打印流程" in item and "模型打印编辑页" in item and "下一步" in item for item in texts),
            f"{relative_path} must handle the observed post-import model edit page before waiting for cancel print",
        )
        require(
            any("当前仍在模型打印编辑页" in item and "点击一次该「下一步」" in item for item in texts),
            f"{relative_path} must tap the second print-edit 下一步 before waiting for cancel print",
        )
        require(
            any("第二次下一步提交后" in item and "网络连接超时" in item and "可安全重试" in item for item in texts),
            f"{relative_path} must retry after the online report showed the edit page still had 下一步",
        )
        require(
            "已退出打印流程，页面出现返回按钮或回到模型导入相关页面" not in text,
            f"{relative_path} must not use the broad exit assertion that hides cleanup-state mistakes",
        )
        require(
            not any(item.strip() == "返回" for item in texts),
            f"{relative_path} must not blindly tap 返回 after cleanup",
        )


def test_baseline_flows_recover_restored_print_preview_before_home_actions():
    for base in ("server-tasks", "server-tasks-all"):
        for path in (ROOT / base / "3D打印基线").glob("*.yaml"):
            relative_path = str(path.relative_to(ROOT))
            texts = [_step_text(step) for step in _load_flow(relative_path)]
            first_home_wait = next((i for i, item in enumerate(texts) if "App 首页已加载完成" in item), len(texts))
            startup_texts = texts[:first_home_wait]
            require(
                any("启动恢复" in item and "已取消模型处理" in item and "不要点击「重新编辑」" in item for item in startup_texts),
                f"{relative_path} must recover if launch restores an already-cancelled print preview",
            )
            require(
                any("启动二次恢复" in item and "模型打印编辑" in item and "不要点击「下一步」" in item for item in startup_texts),
                f"{relative_path} must continue recovering when the cancelled preview returns to the print edit page",
            )
            require(
                any("启动三次恢复" in item and "模型详情" in item and "不要点击「去打印」" in item for item in startup_texts),
                f"{relative_path} must recover if launch restores a model detail page with a go-print button",
            )


def test_wechat_import_retries_restored_cancelled_preview_before_home_wait():
    for relative_path in (
        "server-tasks/3D打印基线/模型导入-微信导入.yaml",
        "server-tasks-all/3D打印基线/模型导入-微信导入.yaml",
    ):
        texts = [_step_text(step) for step in _load_flow(relative_path)]
        first_home_wait = next((i for i, item in enumerate(texts) if "App 首页已加载完成" in item), len(texts))
        startup_texts = texts[:first_home_wait]
        cancelled_preview_recoveries = [
            item
            for item in startup_texts
            if "启动" in item
            and "已取消模型处理" in item
            and "模型打印预览" in item
            and "最多点击一次" in item
        ]
        require(
            len(cancelled_preview_recoveries) >= 2,
            f"{relative_path} must retry cancelled print-preview recovery before waiting for the App home page",
        )


def test_print_flows_stop_after_cancel_cleanup_without_returning_home():
    for relative_path in (
        "server-tasks/3D打印基线/模型导入-微信导入.yaml",
        "server-tasks-all/3D打印基线/模型导入-微信导入.yaml",
        "server-tasks/3D打印基线/OBJ保龄球打印.yaml",
        "server-tasks-all/3D打印基线/OBJ保龄球打印.yaml",
        "server-tasks/3D打印基线/普通印章打印.yaml",
        "server-tasks-all/3D打印基线/普通印章打印.yaml",
        "server-tasks/3D打印基线/姓名牌打印.yaml",
        "server-tasks-all/3D打印基线/姓名牌打印.yaml",
        "server-tasks/3D打印基线/标牌打印.yaml",
        "server-tasks-all/3D打印基线/标牌打印.yaml",
        "server-tasks/3D打印基线/十二生肖印章打印.yaml",
        "server-tasks-all/3D打印基线/十二生肖印章打印.yaml",
    ):
        texts = [_step_text(step) for step in _load_flow(relative_path)]
        cleanup_index = next(
            (
                i
                for i, item in enumerate(texts)
                if "取消清理" in item or "取消确认" in item or item.strip() == "取消打印"
            ),
            -1,
        )
        require(
            cleanup_index != -1,
            f"{relative_path} must still cancel or stop print processing before ending",
        )
        tail_texts = texts[cleanup_index:]
        forbidden_home_cleanup_labels = (
            "取消后回首页",
            "取消后二次回首页",
            "取消后三次回首页",
            "收尾首页确认",
            "收尾返回",
        )
        require(
            not any(label in item for item in tail_texts for label in forbidden_home_cleanup_labels),
            f"{relative_path} must not return to App home after the main print flow because each case restarts the app",
        )
        require(
            any("am force-stop com.kfb.model" in item for item in tail_texts),
            f"{relative_path} must still force-stop the app after the main flow finishes",
        )


if __name__ == "__main__":
    test_obj_bowling_uses_observed_go_print_button_instead_of_hard_next_step()
    test_stamp_flows_exit_print_preview_with_repeated_cancel_guard()
    test_local_import_accepts_android_file_picker_and_cancel_guard()
    test_wechat_import_uses_bounded_cancel_cleanup()
    test_baseline_flows_recover_restored_print_preview_before_home_actions()
    test_wechat_import_retries_restored_cancelled_preview_before_home_wait()
    test_print_flows_stop_after_cancel_cleanup_without_returning_home()
