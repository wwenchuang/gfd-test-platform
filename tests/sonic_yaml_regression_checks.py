"""Regression checks for the scheduled 智小白3D Android case flows."""

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services.yaml_service import stable_case_id
from task_server.services.yaml_static_validator import validate_yaml_static_executable


DEVICE = "9888E0094F2A"
CUBE = "qa-import-cube-10mm.stl"
DRAGON = "关节龙打印"
DRAGON_NATIVE_SHA256 = (
    "d8310d8c1180ea3e05813dff88579ab2e7670412866f53f3e923f328cd88fc2f",  # start slice window
    "0560decdc9ab2b793fd30ab58944bf7ee697b6c94ffd92c2489e16050d7d561f",  # open the fresh record for the selected printer
    "dea1e66ff7e5c2c66a3438532a9fb20b40617d13333333dae4dbd81a65656a3e",  # consume the bound record at the native safety dialog
)
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
# Observable end state for each extended route. The original routes below also
# enforce the ordering and ownership of high-impact actions.
EXTENDED_RESULTS = {
    "首页热门与最新模型": ("最新", "模型卡片"),
    "首页搜索模型详情": ("迷你保龄球套装", "查看全部"),
    "浏览主题专区模型": ("光隅小灯具", "模型卡片"),
    "主题配套套件介绍": ("适配模型", "套件包含内容"),
    "新手必学课程目录": ("机器操作", "已完成"),
    "广场推荐和最新作品": ("最新", "互动数据"),
    "广场作品详情": ("作品详情", "互动数据"),
    "个人作品按类型筛选": ("作品", "印章", "选中"),
    "个人导入文件保存期限": ("30天", "过期自动删除"),
    "邀请打印规则与记录": ("邀请记录", "明细"),
    "勋章挑战与已获勋章": ("成长成就", "勋章"),
    "服务中心设备与自助问题": (DEVICE, "猜你想问"),
    "服务中心故障解答": ("已解决", "未解决"),
    "成长乐园教材分科": ("英语", "学习卡片"),
    "成长乐园建模大冒险": ("当前任务", "魔法涂鸦", "魔法积木"),
    "成长乐园科创项目": ("科创课", "立即学习"),
    "我的设备状态与控制入口": (DEVICE, "更多设置"),
    "设备详情与部件保养": (DEVICE, "最大打印尺寸", "100 mm"),
    "设备耗材颜色面板": ("选择耗材颜色", "基础", "釉光"),
    "个人成长能力报告": ("经验值", "最近成长"),
    "掐丝珐琅手工教程播放": ("倍速", "播放"),
    "掐丝珐琅挂件选图指引": ("主体纯粹", "杂乱遮挡"),
    "魔法涂鸦创作模式": ("自由画布", "粗细", "下一步"),
    "标牌文字八字边界": ("春夏秋冬东南西北", "未写入"),
    "印章图库分类切换": ("十二生肖", "牛", "虎"),
    "印章推荐文案快速替换": ("好好学习", "平安喜乐"),
    "印章阴刻红色试盖": ("红色", "牛图案"),
    "印章设计退出确认保留编辑": ("56mm", "牛图样"),
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
        (TAP, "创作"), (TAP, "开始创作"), (INPUT, "硬币"), (TAP, "发送"),
        (WAIT, "UI自动化硬币图片", "没有喜欢的", "不补充 直接生成", "立即生成"),
        ("ai", "没有喜欢的", "不补充 直接生成", "立即生成"),
        (WAIT, "硬币", "主体", "细节", "用途", "不补充 直接生成", "立即生成"),
        (TAP, "硬币", "主体", "细节", "用途", "不补充 直接生成", "立即生成"),
        (WAIT, "就这样，生成模型"), (TAP, "就这样，生成模型"), (WAIT, "硬币", "去打印"),
        (TAP, "硬币", "去打印"), (TAP, "返回"), (ASSERT, "居中", "旋转", "下一步"),
    ],
    "模型导入-本地导入": [
        (TAP, "导入打印"), (TAP, "本地导入"), (TAP, "内部存储"), (TAP, "Download"),
        (TAP, "微信导入测试"), (WAIT, CUBE, "仅有一个"), (TAP, CUBE),
        (ASSERT, "立方体", "10"), (TAP, "返回"), (ASSERT, "退出"),
    ],
    "模型导入-微信导入": [
        (TAP, "导入打印"), (TAP, "微信导入"), ("runAdbShell", "Open only the scoped app-to-WeChat confirmation dialogs"),
        (WAIT, "微信小程序", "选择文件"), (TAP, "选择文件"),
        (TAP, "文件传输助手"), (WAIT, CUBE, "仅有一个"), (TAP, CUBE, "勾选框"),
        (ASSERT, CUBE, "勾选"), (TAP, "确定"), (TAP, "确认导入"), (ASSERT, "立方体", "10"),
    ],
    DRAGON: [
        (ASSERT, DEVICE, "空闲"), (INPUT, "关节龙"), (TAP, "关节龙"), (ASSERT, "关节龙"), (TAP, "去打印"),
        (WAIT, "关节龙", "分节龙形"), (TAP, "下一步"), (WAIT, "三维画布中央", "龙头", "连续的分节"),
        (ASSERT, "分节龙形", "设备就绪", "网络信号差"), (TAP, "打印记录"),
        (WAIT, DEVICE, "模型处理完成", "打印编号"), ("runAdbShell", "open the fresh record"),
        (ASSERT, "分节龙形", "设备状态完美"), (TAP, "确认打印"),
        (ASSERT, "确认已安装底板", "确认挤出喷头", "安全检查"), ("runAdbShell", "consume the bound record"),
        (TAP, "返回首页"), (ASSERT, DEVICE, "打印任务", "关节龙"),
    ],
}
PREVIEW_CASES = {"文字建模", "标牌打印", "模型导入-微信导入", "OBJ保龄球打印", "姓名牌打印", "普通印章打印", "十二生肖印章打印"}


PREVIEW_READ_WINDOW = r"""file=$(mktemp /data/local/tmp/qa-kfb-preview.XXXXXX) || exit 1; trap 'rm -f "$file"' EXIT; uiautomator dump "$file" >/dev/null && ui=$(cat "$file") && [ -n "$ui" ] || exit 1;"""
PREVIEW_COLOR_CONFIRM = " ".join(r"""
file=$(mktemp /data/local/tmp/qa-kfb-preview.XXXXXX) || exit 1;
trap 'rm -f "$file"' EXIT;
for attempt in 1 2 3; do
: > "$file"; uiautomator dump "$file" >/dev/null && [ -s "$file" ] || exit 1;
if grep -Fq 'text="请确认当前使用的耗材颜色为：' "$file" && grep -Fq 'text="修改设置"' "$file"; then
bounds=$(tr '>' '\n' < "$file" | grep -F 'text="确认"' | grep -F 'class="android.widget.Button"' | grep -F 'package="com.kfb.model"' | grep -F ' clickable="true"' | grep -F ' enabled="true"' | sed -n 's/.*bounds="\[\([0-9][0-9]*\),\([0-9][0-9]*\)\]\[\([0-9][0-9]*\),\([0-9][0-9]*\)\]".*/\1 \2 \3 \4/p');
set -- $bounds; [ "$#" -eq 4 ] || exit 1;
input tap "$((($1+$3)/2))" "$((($2+$4)/2))"; exit $?; fi;
if tr '>' '\n' < "$file" | grep -F 'text="模型打印预览"' | grep -F 'resource-id="com.kfb.model:id/web_title_tv_middle"' > /dev/null; then exit 0; fi;
done; exit 1
""".strip().splitlines())
PREVIEW_WEAK_SIGNAL_BACK = PREVIEW_READ_WINDOW + " " + " ".join(r"""
case "$ui" in *'package="com.kfb.model"'*'text="网络信号差"'*)
case "$ui" in *'text="继续打印"'*'text="去调整网络"'*) input keyevent 4 ;; *) exit 1 ;;
esac ;;
esac
""".strip().splitlines())
PREVIEW_CANCEL_DIALOG_TAP = '弹窗「确认要取消任务吗？」内右侧的「确定」按钮；若中央已显示「已取消模型处理」，仅轻触中央静态提示「已取消模型处理」文字。'


# On a direct transition to the native safety dialog this action only observes it.
DRAGON_WEAK_SIGNAL_CONTINUE = " ".join(r"""
file=$(mktemp /data/local/tmp/qa-kfb-dragon.XXXXXX) || exit 1;
trap 'rm -f "$file" "$file.nodes"' EXIT;
uiautomator dump "$file" >/dev/null && [ -s "$file" ] || exit 1;
tr '>' '\n' < "$file" | grep '<node ' | grep ' package="com.kfb.model"' > "$file.nodes" || exit 1;
if grep -Fq 'resource-id="com.kfb.model:id/tv_print_confirm"' "$file.nodes"; then
if grep -Fq 'text="网络信号差"' "$file.nodes"; then exit 1; fi; exit 0; fi;
for label in 网络信号差 继续打印 去调整网络; do
[ "$(grep -Fc " text=\"$label\"" "$file.nodes")" -eq 1 ] || exit 1; done;
bounds=$(grep -F ' text="继续打印"' "$file.nodes" | grep ' clickable="true"' | grep ' enabled="true"'
| sed -n 's/.*bounds="\[\([0-9][0-9]*\),\([0-9][0-9]*\)\]\[\([0-9][0-9]*\),\([0-9][0-9]*\)\]".*/\1 \2 \3 \4/p');
set -- $bounds; [ "$#" -eq 4 ] && [ "$3" -gt "$1" ] && [ "$4" -gt "$2" ] || exit 1;
input tap "$((($1+$3)/2))" "$((($2+$4)/2))"
""".strip().splitlines())



def test_dragon_bound_submission():
    flow = yaml.safe_load((ROOT / "server-tasks" / "3D打印基线" / (DRAGON + ".yaml")).read_bytes())["tasks"][0]["flow"]
    arm, select, submit = [s["runAdbShell"] for s in flow if s.get("runAdbShell", "").startswith("# dragon:")]
    harness = """
        uiautomator() { cat "$XML_FILE" > "$2"; }
        date() {
            if [ "$1" = "+%s" ]; then echo "$NOW"
            elif [ "$1" = "+%s %Y-%m-%d %H:%M:%S" ]; then echo "$NOW 2026-11-23 12:50:00"
            elif [ "$1" = "+%Y-%m-%d %H:%M:%S" ]; then echo '2026-11-23 12:52:20'
            else return 1; fi
        }
        pidof() { echo "$PID"; }
        input() { echo "INPUT $*"; }
    """
    def node(text, attrs=""):
        return f'<node text="{text}" class="android.widget.TextView" package="com.kfb.model" {attrs}/>'

    action_attrs = 'enabled="true" bounds="[777,894][1002,981]"'
    metric_icon = '<node text="打印" class="android.widget.Image" package="com.kfb.model" enabled="true"/>'

    def row(stamp, model, sn, status="模型处理完成", action="打印"):
        return (f'<node class="android.view.View" package="com.kfb.model">{node(stamp)}{node(status)}'
                f'<node class="android.view.View" package="com.kfb.model">'
                f'{node(model, bounds)}{metric_icon}{metric_icon}{node("打印编号：" + sn)}</node>{node(action, action_attrs)}</node>')

    bounds = 'bounds="[345,642][1002,702]"'
    first = row("2026-11-23 12:51:20", "关节龙", "1574407263092404224")
    old = row("2026-11-23 11:00:00", "关节龙", "1574381498682556416", "取消模型处理", "重新编辑")
    listing = (node("打印记录") + node("EP3-" + DEVICE + " ") +
               '<node class="android.widget.ListView" package="com.kfb.model">' + first + old + '</node>')
    safety = (node("1.确认已安装底板并清理杂物", 'resource-id="com.kfb.model:id/tv_step_1"') +
              node("2.确认挤出喷头干净无异物", 'resource-id="com.kfb.model:id/tv_step_2"') +
              node("检查无误，确认打印", 'resource-id="com.kfb.model:id/tv_print_confirm" clickable="true" enabled="true" ' + bounds))
    with tempfile.TemporaryDirectory() as work:
        def run(command, xml="", now="240", pid="2410", ok=True):
            fixture = Path(work) / "window.xml"
            fixture.write_text(xml)
            result = subprocess.run(["sh", "-c", harness + command.replace("/data/local/tmp", work)],
                                    env={"XML_FILE": str(fixture), "NOW": now, "PID": pid}, capture_output=True, text=True, timeout=5)
            assert (result.returncode == 0) == ok, (result.stderr, result.stdout)
            taps = [line for line in result.stdout.splitlines() if "INPUT" in line]
            assert ok or not taps
            return taps

        for bad in [
            listing.replace(DEVICE, "123456789012"),
            listing.replace("关节龙", "姓名牌", 1),
            listing.replace("模型处理完成", "取消模型处理", 1),
            listing.replace('text="打印"', 'text="重新编辑"'),
            listing.replace(node("打印", action_attrs), ""),
            listing.replace(node("打印", action_attrs), node("打印", action_attrs) * 2),
            listing.replace(action_attrs, action_attrs.replace("true", "false")),
            listing.replace(action_attrs, 'enabled="true" bounds="[0,0][0,0]"'),
            listing.replace(node("打印", action_attrs), '<node class="android.view.View" package="com.kfb.model">' +
                            node("打印", action_attrs) + '</node>'),
            listing.replace("12:51:20", "11:59:00"),
            listing.replace("12:51:20", "12:50:00"),
            listing.replace("12:51:20", "12:53:00"),
            listing.replace("11:00:00", "12:50:30"),
            listing.replace("1574407263092404224", "1574381498682556416"),
            listing.replace("1574407263092404224", "1234"),
            listing.replace(first, first.replace("打印编号：", "其他信息：")),
            listing.replace(bounds, 'bounds="[0,0][0,0]"'),
        ]:
            run(arm, now="100")
            run(select, bad, ok=False)
            run(submit, safety, ok=False)

        for xml, pid, now, ok in [
            (safety.replace("喷头干净无异物", "喷头"), "2410", "255", False),
            (safety.replace('enabled="true"', 'enabled="false"'), "2410", "255", False),
            (safety.replace("com.kfb.model", "other.app"), "2410", "255", False),
            (safety, "111", "255", False), (safety, "2410", "750", False),
            (safety, "2410", "255", True),
            (node("x" * 370_000) + safety, "2410", "255", True),
        ]:
            run(arm, now="100")
            assert run(select, node("x" * 370_000) + listing) == ["INPUT tap 673 672"]
            actions = run(submit, xml, pid=pid, now=now, ok=ok)
            if ok:
                assert actions == ["INPUT tap 673 672"]
            run(submit, safety, ok=False)  # consume the proof even after an invalid safety dialog


def test_dragon_weak_signal_action():
    harness = """
        mktemp() { command mktemp; }
        uiautomator() { printf '%s' "$UI_XML" > "$2"; }
        input() { echo "$*"; }
    """
    def node(text, attributes=""):
        return f'<node text="{text}" package="com.kfb.model" {attributes}/>'

    weak = node("网络信号差")
    button = node("继续打印", 'clickable="true" enabled="true" bounds="[124,1650][526,1764]"')
    alternate = node("去调整网络")
    safety = node("检查无误，确认打印", 'resource-id="com.kfb.model:id/tv_print_confirm"')
    for xml, code, tap in [
        (weak + button + alternate, 0, "tap 325 1707"),
        (safety, 0, ""),
        (weak + button, 1, ""),
        (weak + button * 2 + alternate, 1, ""),
        (weak + button.replace('enabled="true"', 'enabled="false"') + alternate, 1, ""),
        ((weak + button + alternate).replace("com.kfb.model", "other.app"), 1, ""),
        (weak + button + alternate + safety, 1, ""),
    ]:
        result = subprocess.run(["sh", "-c", harness + DRAGON_WEAK_SIGNAL_CONTINUE],
                                env={"UI_XML": xml}, capture_output=True, text=True, timeout=5)
        assert (result.returncode, result.stdout.strip()) == (code, tap), (xml, result.stderr)


def test_preview_native_actions():
    # The shell runs unmodified with fake Android input and a synthetic UI hierarchy.
    tools = r"""
        mktemp() { command mktemp; }
        uiautomator() { cat "$UI_FIXTURE" > "$2"; }
        printf() { /usr/bin/printf "$@"; }
        input() { echo "$*"; }
    """
    material = '<node text="请确认当前使用的耗材颜色为：官方耗材-橘子橙 "/><node text="修改设置"/>'
    confirm = '<node text="确认" class="android.widget.Button" package="com.kfb.model" clickable="true" enabled="true" bounds="[555,1637][924,1760]"/>'
    preview = '<node text="模型打印预览" package="com.kfb.model" resource-id="com.kfb.model:id/web_title_tv_middle"/><node text="确认打印"/>'
    safety = '<node text="检查无误，确认打印" resource-id="com.kfb.model:id/tv_print_confirm"/>'
    weak = '<node package="com.kfb.model"/><node text="网络信号差"/><node text="继续打印"/><node text="去调整网络"/>'
    for command, xml, code, action in (
        (PREVIEW_COLOR_CONFIRM, material + confirm, 0, "tap 739 1698"),
        (PREVIEW_COLOR_CONFIRM, preview, 0, ""),
        (PREVIEW_COLOR_CONFIRM, safety, 1, ""),
        (PREVIEW_COLOR_CONFIRM, '<node text="还在加载"/>', 1, ""),
        (PREVIEW_COLOR_CONFIRM, ('<node text="padding"/>' * 18000) + material + confirm, 0, "tap 739 1698"),
        (PREVIEW_COLOR_CONFIRM, material + confirm * 2, 1, ""),
        (PREVIEW_COLOR_CONFIRM, material + confirm.replace('text="确认"', 'text="确认打印"'), 1, ""),
        (PREVIEW_COLOR_CONFIRM, material + confirm.replace("com.kfb.model", "another.app"), 1, ""),
        (PREVIEW_COLOR_CONFIRM, material + confirm.replace('clickable="true"', 'clickable="false"'), 1, ""),
        (PREVIEW_WEAK_SIGNAL_BACK, weak, 0, "keyevent 4"),
        (PREVIEW_WEAK_SIGNAL_BACK, preview, 0, ""),
        (PREVIEW_WEAK_SIGNAL_BACK, safety, 0, ""),
        (PREVIEW_WEAK_SIGNAL_BACK, weak.replace("去调整网络", "取消"), 1, ""),
        (PREVIEW_WEAK_SIGNAL_BACK, weak.replace("com.kfb.model", "another.app"), 0, ""),
    ):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as fixture:
            fixture.write(xml)
            fixture.flush()
            result = subprocess.run(["sh", "-c", tools + command], env={"UI_FIXTURE": fixture.name},
                                    capture_output=True, text=True, timeout=5)
        assert (result.returncode, result.stdout.strip()) == (code, action), (xml, result.stderr)


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
    if name in EXTENDED_RESULTS and any(step.get("runAdbShell") == reset_activity for step in flow):
        allowed_start_commands.add(reset_activity)
        check_order(name, flow, [("runAdbShell", reset_activity), ("launch", "com.kfb.model")])
    if name == "文字建模":
        allowed_start_commands.discard("am force-stop com.kfb.model")
        variant_actions = [step for step in flow if "立即生成" in str(step)]
        assert [next(iter(step)) for step in variant_actions] == [WAIT, "ai", WAIT, TAP]
        assert all("就这样，生成模型" not in str(step) for step in variant_actions)
        check_order(name, flow, [(TAP, "我的"), (WAIT, "打印记录"), (TAP, "房子"), (WAIT, DEVICE, "空闲")])
    if name in PREVIEW_CASES:
        allowed_start_commands.add(PREVIEW_COLOR_CONFIRM)
    if name == "模型导入-微信导入":
        wechat, = [s["runAdbShell"] for s in flow if s.get("runAdbShell", "").startswith("# Open only the scoped app-to-WeChat")]
        # The device-side command can only accept the two observed dialogs once.
        assert hashlib.sha256(wechat.encode()).hexdigest() == "afa583a88f51a342c9c221f8c8e91913e84421202a768e1cb13a7bdbc6628d25"
        allowed_start_commands.add(wechat)
    if name == "姓名牌打印":
        allowed_start_commands.add(PREVIEW_WEAK_SIGNAL_BACK)
    submissions = []
    cancellations = []
    stamp_editor = '本次印章「打印准备」底部的「开始打印」按钮一次'
    is_stamp = name in {'普通印章打印', '十二生肖印章打印'}
    if is_stamp:
        check_order(name, flow, [
            (WAIT, '打印准备', '2个打印任务'), (ASSERT, '把手', '印面'), (TAP, stamp_editor),
            (WAIT, '第1盘', '居中', '下一步'), (TAP, '第1盘', '下一步'),
        ])
        assert sum(step.get(TAP) == stamp_editor for step in flow) == 1
    if name == DRAGON:
        native = [step["runAdbShell"] for step in flow if step.get("runAdbShell", "").startswith("# dragon:")]
        assert tuple(hashlib.sha256(command.encode()).hexdigest() for command in native) == DRAGON_NATIVE_SHA256
        arm, select, submit = native
        # The ready controls, weight and estimate are already visible at 70% render.
        # Both visits must wait solely for the dragon's actual geometry before assertions.
        first = next(i for i, s in enumerate(flow) if s.get("runAdbShell") == PREVIEW_COLOR_CONFIRM)
        second = next(i for i, s in enumerate(flow) if s.get("runAdbShell") == select)
        geometry = "三维画布中央完整显示盘曲成环的立体关节龙，能看清龙头轮廓和沿弯曲龙身连续的分节。"
        for index in (first, second):
            assert flow[index + 1].get(WAIT) == geometry
            assert ASSERT in flow[index + 2] and "分节龙形" in flow[index + 2][ASSERT]
        assert all("${" not in command for command in native), "Midscene may interpolate shell expressions"
        allowed_start_commands.update({PREVIEW_COLOR_CONFIRM, DRAGON_WEAK_SIGNAL_CONTINUE, *native})
        check_order(name, flow, [(ASSERT, DEVICE, "空闲"), ("runAdbShell", arm), (TAP, "下一步"),
                    (WAIT, "修改设置", "模型打印预览"), ("runAdbShell", PREVIEW_COLOR_CONFIRM),
                    (ASSERT, "分节龙形"), ("runAdbShell", reset_activity), (WAIT, DEVICE, "空闲"),
                    (TAP, "打印记录"), ("runAdbShell", select), (ASSERT, "设备状态完美"), (TAP, "确认打印"),
                    ("runAdbShell", DRAGON_WEAK_SIGNAL_CONTINUE), (ASSERT, "安全检查"), ("runAdbShell", submit)])
        for command in (PREVIEW_COLOR_CONFIRM, DRAGON_WEAK_SIGNAL_CONTINUE, arm, select, submit):
            assert sum(step.get("runAdbShell") == command for step in flow) == 1, name
        selected = next(i for i, step in enumerate(flow) if step.get("runAdbShell") == select)
        submitted = next(i for i, step in enumerate(flow) if step.get("runAdbShell") == submit)
        action_steps = [s for s in flow[selected + 1:submitted] if set(s) - {WAIT, ASSERT, "timeout"}]
        assert action_steps == [{TAP: "本次模型打印预览底部右侧的「确认打印」"}, {"runAdbShell": DRAGON_WEAK_SIGNAL_CONTINUE}]
        assert not any({"ai", "aiAct", "aiAction"} & step.keys() for step in flow), name
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
            if is_stamp and action == TAP and label == stamp_editor:
                continue  # This specific two-plate screen opens the first plate editor.
            if re.search(r"确认打印|直接打印|开始打印|提交打印|发送打印|继续打印", label):
                submissions.append(index)
    if name in PREVIEW_CASES or name == DRAGON:
        check_order(name, flow, [(WAIT, DEVICE, "空闲"), (ASSERT, "空闲")])
    if name == DRAGON:
        assert len(submissions) == 1 and not cancellations, f"{name}: only the preview uses an AI confirmation tap"
    else:
        assert not submissions, f"{name}: physical printing action at {submissions}"
    if name in PREVIEW_CASES:
        assert len(cancellations) == 1, f"{name}: expected one cancellation of the current preview"
        cancel = cancellations[0]
        guard = " ".join(str(step.get(key, "")) for step in flow[:cancel] for key in (ASSERT, WAIT))
        assert DEVICE in guard, f"{name}: preview must use the designated device"
        check_order(name, flow[:cancel], [
            (ASSERT, "空闲"), (TAP, "下一步"), (WAIT, "修改设置", "模型打印预览"), ("runAdbShell", PREVIEW_COLOR_CONFIRM),
            (WAIT, "模型打印预览", "确认打印"), (ASSERT, "本次", "预览", "确认打印"),
        ])
        material = next(i for i, step in enumerate(flow) if step.get("runAdbShell") == PREVIEW_COLOR_CONFIRM)
        editor = max(i for i, step in enumerate(flow[:material]) if TAP in step and "下一步" in step[TAP])
        for step in flow[editor + 1:]:
            assert not ({"ai", "aiAct", "aiAction"} & step.keys()), f"{name}: planning may tap again in the preview"
        taps = [step[TAP] for step in flow[editor + 1:] if TAP in step]
        assert len(taps) == 3 and taps[1] == PREVIEW_CANCEL_DIALOG_TAP, name
        assert taps[0] == flow[cancel][TAP] and "返回" in taps[2], name
        assert sum(step.get("runAdbShell") == PREVIEW_COLOR_CONFIRM for step in flow) == 1
        if name == "姓名牌打印":
            check_order(name, flow[editor:cancel], [(WAIT, "网络信号差"), ("runAdbShell", PREVIEW_WEAK_SIGNAL_BACK),
                                                   (WAIT, "UI自动化", "模型打印预览"), (ASSERT, "本次", "UI自动化")])
            assert sum(step.get("runAdbShell") == PREVIEW_WEAK_SIGNAL_BACK for step in flow) == 1
        if name == "文字建模":
            wait, = [step[WAIT] for step in flow[editor:cancel] if WAIT in step and "确认打印" in step[WAIT]]
            assert all(text in wait for text in ("预计时间", "模型耗材", "取消打印"))
            assert all(text not in wait for text in ("继续等待", "如果", "或百分比"))
        check_order(name, flow[cancel + 1:], [(WAIT, "确认要取消任务", "已取消模型处理"), (TAP, PREVIEW_CANCEL_DIALOG_TAP),
                                              (WAIT, "已取消模型处理", "返回"), (TAP, "返回"), (ASSERT, "离开")])
    else:
        assert not cancellations, f"{name}: unexpected task cancellation"


def test_published_baselines():
    assert (ROOT / "deploy" / "3d-daily" / "fixtures" / CUBE).is_file(), f"Missing import fixture {CUBE}"
    manifest = json.loads((ROOT / "deploy" / "3d-daily" / "baselines.json").read_text())
    pins = {(row["module"], row["filename"]): row["sha256"] for row in manifest}
    assert len(pins) == len(manifest) == len(CASE_IDS) + len(EXTENDED_RESULTS)
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
            assert (name in CASE_IDS or name in EXTENDED_RESULTS) and name not in seen, f"{module}: unexpected or duplicate {name}"
            seen.add(name)
            twin = ROOT / "server-tasks-all" / directory / path.name
            assert twin.read_bytes() == path.read_bytes(), f"{name}: task mirrors differ"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == pins[module, path.name], f"{name}: stale scheduled revision"
            case_id = ("COM_KFB_MODEL_" + CASE_IDS[name] if name in CASE_IDS
                       else stable_case_id("com.kfb.model", module, path.name, name))
            assert re.findall(r"(?m)^\s*# baseline\.case_id:\s*(\S+)$", source) == [case_id], name
            check = validate_yaml_static_executable(source, strict=True)
            assert check["executionLevel"] == "executable", (name, check["errors"], check["warnings"])
            flow = task["flow"]
            if name == "设备详情与部件保养":
                check_order(name, flow, [(ASSERT, "喷头组件", "打印平台", "百分比"),
                                         ("ai", "规格"), (ASSERT, DEVICE, "最大打印尺寸")])
            if name == "掐丝珐琅手工教程播放":
                check_order(name, flow, [(WAIT, "隐藏"), (TAP, "画面"), (ASSERT, "总时长", "倍速")])
                assert TAP in flow[-2], "读取视频进度时需使用刚刚唤出的控件"
            if name == "魔法涂鸦创作模式":
                check_order(name, flow, [(TAP, "自由涂鸦"), ("ai", "我知道了"), (ASSERT, "笔画粗细")])
            if name.startswith("成长乐园"):
                assert not any("课程内容或带" in step.get(WAIT, "") for step in flow), name
                check_order(name, flow, [(TAP, "进入课程"), ("ai", "体验提示"), (ASSERT,)])
            if name in EXTENDED_RESULTS:
                check_order(name, flow, [(TAP,), (ASSERT, *EXTENDED_RESULTS[name])])
            else:
                check_order(name, flow, ROUTES[name])
            check_print_actions(name, flow)
    assert CASE_IDS.keys() == ROUTES.keys()
    assert seen == CASE_IDS.keys() | EXTENDED_RESULTS.keys()


if __name__ == "__main__":
    test_dragon_weak_signal_action()
    test_dragon_bound_submission()
    test_preview_native_actions()
    test_published_baselines()
    print(f"{len(CASE_IDS) + len(EXTENDED_RESULTS)} 智小白3D case flows passed regression checks")
