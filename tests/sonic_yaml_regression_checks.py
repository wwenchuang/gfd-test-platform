"""Regression checks for the sixteen published 智小白3D Android case flows."""

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services.yaml_static_validator import validate_yaml_static_executable


DEVICE = "9888E0094F2A"
CUBE = "qa-import-cube-10mm.stl"
DRAGON = "关节龙打印"
# Published case IDs stay stable even for the two AI cases with different filenames.
CASE_IDS = {
    "我的页面入口跨端验收20261015": "b399ad26499b",
    "智小白3D跨端导航验收": "4e03b147af27",
    "OBJ保龄球打印": "391142ea35f9",
    "姓名牌打印": "b245f8b77517",
    "文字建模": "4f9e47eb53d2",
    "模型导入-微信导入": "0e27ae1d28a0",
    "标牌打印": "38708c2d47ee",
    "客服对话": "bf612450b118",
    "打印记录查看": "117ec9dccef4",
    "普通印章打印": "9d60338c7365",
    "小白客商城-耗材": "9b7d287f912f",
    DRAGON: "f14f42c5f10b",
    "模型导入-本地导入": "6f389b0277e7",
    "我的收藏查看": "a22bf0c3aab6",
    "模型生成记录": "a341e0b47085",
    "十二生肖印章打印": "eae567b5f2ba",
}
TAP, WAIT, ASSERT, INPUT = "aiTap", "aiWaitFor", "aiAssert", "aiInput"
# Match ordered actions and the associated business result, excluding YAML comments.
ROUTES = {
    "我的页面入口跨端验收20261015": [
        (TAP, "我的"), (ASSERT, "我的设备", "打印记录"),
    ],
    "智小白3D跨端导航验收": [
        (TAP, "我的"), (ASSERT, "收藏"), (TAP, "首页"), (ASSERT, "新手必学", "导入打印"),
    ],
    "我的收藏查看": [
        (TAP, "收藏"), (TAP, "主题专区"), (ASSERT, "选中"), (TAP, "模型"), (ASSERT, "模型", "空状态"),
    ],
    "打印记录查看": [
        (TAP, "我的"), (TAP, "打印记录"), (WAIT, "设备筛选栏", "记录", "空状态"),
        (ASSERT, DEVICE, "记录", "空状态"),
    ],
    "模型生成记录": [
        (TAP, "创作"), (TAP, "创作记录"), (ASSERT, "搜索创作标题"), (ASSERT, "年月", "记录"),
    ],
    "客服对话": [
        (TAP, "服务中心"), (TAP, "联系客服"), (INPUT, "消息输入框"), (ASSERT, "发送"), (ASSERT, "清空"),
    ],
    "小白客商城-耗材": [
        (TAP, "耗材商城"), (WAIT, "温馨提示", "立即跳转"), (TAP, "立即跳转"),
        (WAIT, "微信", "打开"), ("ai", "安卓系统", "「打开」"), ("ai", "耗材", "售价"),
        (ASSERT, "耗材", "商品", "价格"),
    ],
    "OBJ保龄球打印": [
        (INPUT, "保龄球"), (TAP, "迷你保龄球套装"), (TAP, "查看全部"), (TAP, "「保龄球套装2」", "22 mm"), (ASSERT, "球体"),
        (TAP, "取消打印"), (TAP, "返回"), (ASSERT, "迷你保龄球套装", "旋转", "下一步"),
    ],
    "姓名牌打印": [
        (INPUT, "姓名牌"), (TAP, "姓名牌", "卡片"), (TAP, "去编辑"), (WAIT, "文本输入框"), (INPUT, "UI自动化"),
        (TAP, "生成模型"),
    ],
    "普通印章打印": [
        (TAP, "单章DIY"), (TAP, "文字"), (INPUT, "UI自动化"), (TAP, "字体"),
        (TAP, "盖印效果"), (ASSERT, "40mm"), (TAP, "试盖效果"), (ASSERT, "文字", "图样"),
        (TAP, "选择把手"), (TAP, "完成"), (ASSERT, "文字印章", "预览"), (TAP, "去打印"), ("ai", "打磨", "知道了"),
    ],
    "十二生肖印章打印": [
        (TAP, "单章DIY"), (TAP, "十二生肖"), (TAP, "牛"), (ASSERT, "牛", "选中"),
        (TAP, "盖印效果"), (ASSERT, "40mm"), (TAP, "试盖效果"), (ASSERT, "牛", "图样"),
        (TAP, "选择把手"), (TAP, "完成"), (ASSERT, "牛印章", "预览"), (TAP, "去打印"), ("ai", "打磨", "知道了"),
    ],
    "标牌打印": [
        (TAP, "标牌"), (INPUT, "UI自动化"), (TAP, "字体"), (TAP, "生成3D预览图"), (TAP, "去打印"),
    ],
    "文字建模": [
        (TAP, "创作"), (TAP, "开始创作"), (INPUT, "硬币"), (TAP, "发送"), (TAP, "不补充"),
        (WAIT, "就这样，生成模型"), (TAP, "就这样，生成模型"), (WAIT, "硬币", "去打印"),
        (TAP, "硬币", "去打印"), (TAP, "返回"), (ASSERT, "居中", "旋转", "下一步"),
    ],
    "模型导入-本地导入": [
        (TAP, "导入打印"), (TAP, "本地导入"), (TAP, "内部存储"), (TAP, "Download"),
        (TAP, "微信导入测试"), (WAIT, CUBE, "仅有一个"), (TAP, CUBE),
        (ASSERT, "立方体", "10"), (TAP, "返回"), (ASSERT, "退出"),
    ],
    "模型导入-微信导入": [
        (TAP, "导入打印"), (TAP, "微信导入"), ("ai", "安卓系统", "「打开」"), (TAP, "选择文件"),
        (TAP, "文件传输助手"), (WAIT, CUBE, "仅有一个"), (TAP, CUBE, "勾选框"),
        (ASSERT, CUBE, "勾选"), (TAP, "确定"), (TAP, "确认导入"), (ASSERT, "立方体", "10"),
    ],
    DRAGON: [
        (ASSERT, DEVICE, "空闲"), (INPUT, "关节龙"), (TAP, "关节龙"), (ASSERT, "关节龙"), (TAP, "去打印"),
        (WAIT, "关节龙", "分节龙形"), (TAP, "下一步"), (WAIT, "分节龙形", "耗材"),
        (ASSERT, "分节龙形", "设备就绪", "网络信号差"), (TAP, "确认打印"), (ASSERT, "网络信号差"),
        (ASSERT, DEVICE, "安全检查"), (TAP, "检查无误确认打印"),
        (TAP, "返回首页"), (ASSERT, DEVICE, "打印任务", "关节龙"),
    ],
}
PREVIEW_CASES = {"文字建模", "标牌打印", "模型导入-微信导入", "OBJ保龄球打印", "姓名牌打印", "普通印章打印", "十二生肖印章打印"}


def check_order(name, flow, sequence):
    cursor = 0
    for action, *terms in sequence:
        for index in range(cursor, len(flow)):
            step = flow[index]
            if action not in step:
                continue
            text = str(step[action])
            if action == INPUT:
                text += " " + str(step.get("value", ""))
            if all(term in text for term in terms):
                cursor = index + 1
                break
        else:
            raise AssertionError(f"{name}: missing {action} {terms!r} after step {cursor}")


def check_print_actions(name, flow):
    reset_activity = "am start -W -f 0x10008000 -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n com.kfb.model/.splash.SplashActivity"
    allowed_start_commands = {"am force-stop com.kfb.model"}
    if name in {"十二生肖印章打印", DRAGON, "文字建模"}:
        allowed_start_commands.add(reset_activity)
        check_order(name, flow, [("runAdbShell", reset_activity), ("launch", "com.kfb.model")])
    if name == "文字建模":
        allowed_start_commands.discard("am force-stop com.kfb.model")
        check_order(name, flow, [(TAP, "我的"), (WAIT, "打印记录"), (TAP, "房子"), (WAIT, DEVICE, "空闲")])
    submissions = []
    cancellations = []
    for index, step in enumerate(flow):
        assert "javascript" not in step and "runWdaRequest" not in step, f"{name}: unreviewed device action"
        if "runAdbShell" in step:
            command = step["runAdbShell"]
            if name == "客服对话" and command == "input keyevent 123 67 67":
                check_order(name, flow[:index], [(INPUT, "QA"), (ASSERT, "尚未发送"), (TAP, "恢复输入焦点")])
            else:
                assert command in allowed_start_commands, f"{name}: unexpected ADB command {command}"
        for action in (TAP, "ai", "aiAct", "aiAction"):
            if action not in step:
                continue
            target = str(step[action])
            if name.startswith("模型导入-"):
                assert not re.search(r"(第一[个条]|首[个条]|任[意一]).{0,16}(stl|文件)", target, re.I), f"{name}: ambiguous file choice"
            if name == "客服对话":
                assert "发送" not in target, f"{name}: real outbound messages are out of scope"
            if action != TAP and ("去打印" in target or "点击「下一步」" in target):
                assert "一次" in target and ("若" in target or "仅" in target), f"{name}: unbounded print navigation"
            assert not re.search(r"遗留任务|首页遗留|根据当前页面底部主按钮", target), f"{name}: unsafe task recovery"
            if "取消打印" in target or "取消处理" in target:
                # A completed, unsent preview can have both cancel and submit buttons.
                assert action == TAP and "预览" in target, f"{name}: cancellation outside a specific preview"
                cancellations.append(index)
            # The sign, text and WeChat tap locators identify Cancel relative to its neighbour.
            label = target.replace("与「确认打印」并列的「取消打印」", "「取消打印」") if action == TAP else target
            if re.search(r"确认打印|直接打印|开始打印|提交打印|发送打印", label):
                submissions.append(index)
    if name in PREVIEW_CASES or name == DRAGON:
        check_order(name, flow, [(WAIT, DEVICE, "空闲"), (ASSERT, "空闲")])
    if name == DRAGON:
        assert len(submissions) == 2 and not cancellations, f"{name}: expected exactly two separate confirmation taps"
        assert all(TAP in flow[i] for i in submissions), f"{name}: submission must be an explicit tap"
    else:
        assert not submissions, f"{name}: physical printing action at {submissions}"
    if name in PREVIEW_CASES:
        assert len(cancellations) == 1, f"{name}: expected one cancellation of the current preview"
        cancel = cancellations[0]
        guard = " ".join(str(step.get(key, "")) for step in flow[:cancel] for key in (ASSERT, WAIT))
        assert DEVICE in guard, f"{name}: preview must use the designated device"
        next_step = ("ai", "无弹窗", "下一步", "一次", "耗材颜色") if name == "姓名牌打印" else (TAP, "下一步")
        check_order(name, flow[:cancel], [
            (ASSERT, "空闲"), next_step, (WAIT, "模型打印预览", "确认打印"),
            (ASSERT, "本次", "预览", "确认打印"),
        ])
        check_order(name, flow[cancel + 1:], [(WAIT, "已取消模型处理", "返回"), (TAP, "返回"), (ASSERT, "离开")])
    else:
        assert not cancellations, f"{name}: unexpected task cancellation"


def test_published_baselines():
    assert (ROOT / "deploy" / "3d-daily" / "fixtures" / CUBE).is_file(), f"Missing import fixture {CUBE}"
    manifest = json.loads((ROOT / "deploy" / "3d-daily" / "baselines.json").read_text())
    pins = {(row["module"], row["filename"]): row["sha256"] for row in manifest}
    assert len(pins) == len(manifest) == len(CASE_IDS)
    seen = set()
    for module in ("3D打印基线", "AI测试"):
        directory = Path(module)
        files = sorted((ROOT / "server-tasks" / directory).glob("*.yaml"))
        for path in files:
            source = path.read_text(encoding="utf-8")
            task, = yaml.safe_load(source)["tasks"]
            name = task["name"]
            if module == "AI测试" and name not in CASE_IDS:
                continue
            assert name in CASE_IDS and name not in seen, f"{module}: unexpected or duplicate {name}"
            seen.add(name)
            twin = ROOT / "server-tasks-all" / directory / path.name
            assert twin.read_bytes() == path.read_bytes(), f"{name}: task mirrors differ"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == pins[module, path.name], f"{name}: stale scheduled revision"
            assert re.findall(r"(?m)^\s*# baseline\.case_id:\s*(\S+)$", source) == ["COM_KFB_MODEL_" + CASE_IDS[name]], name
            check = validate_yaml_static_executable(source, strict=True)
            assert check["executionLevel"] == "executable", (name, check["errors"], check["warnings"])
            flow = task["flow"]
            check_order(name, flow, ROUTES[name])
            check_print_actions(name, flow)
    assert seen == CASE_IDS.keys() == ROUTES.keys(), f"Missing cases {CASE_IDS.keys() - seen}"


if __name__ == "__main__":
    test_published_baselines()
    print("16 published 智小白3D case flows passed regression checks")
