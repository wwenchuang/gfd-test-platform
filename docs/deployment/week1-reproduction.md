# 第 1 周：环境核对和从头复现

本文只覆盖本周演练环境，不修改线上平台的虚拟环境、系统包、设备或历史结果。执行前先阅读 `tests/week1_smoke/README.md`。下面的命令假定已打开新的服务器终端，位于已确认版本的项目根目录；如果某一步失败，保留实际输出，标记阻塞，不把后续步骤记为通过。

## 1. 固定代码与隔离目录

记录项目提交和系统 Python 版本：

```sh
git rev-parse HEAD
python3 --version
node --version
npm --version
```

在练习工作目录中新建**尚不存在**的目录，避免覆盖已有环境。以 `w01-repro-YYYYMMDD-HHMMSS` 为例，替换成这次实际时间；执行人需要确保路径不存在。Python 项目冒烟只使用标准库，浏览器冒烟的版本由 `tests/week1_smoke/package-lock.json` 固定。npm 锁文件约束直接及传递的包版本与完整性；浏览器版本由所锁定的 Playwright 包选择，首次下载仍依赖可用的官方分发源和 Linux 运行库。

```sh
python3 -m venv /root/ai-test-practice-20261105/w01-repro-YYYYMMDD-HHMMSS/venv
/root/ai-test-practice-20261105/w01-repro-YYYYMMDD-HHMMSS/venv/bin/python -m pip --version
/root/ai-test-practice-20261105/w01-repro-YYYYMMDD-HHMMSS/venv/bin/python -c 'import sys, pip; print("python:", sys.executable); print("venv:", sys.prefix); print("pip:", pip.__file__)'
```

核对输出中的 `python`、`venv` 和 `pip` 都位于这次新建环境；如果系统没有 `venv` 或 `ensurepip`，记录失败并由管理员确认安装来源，不能自动改动线上系统。这个标准库项目没有额外的 Python 包需要安装；不要伪造 `requirements.txt` 或把系统包清单当成依赖锁。

## 2. 三类网络分别检查

仅对不含凭据的地址发 GET，不跟随跳转，不传认证信息。以下千问主机是北京地域示例；应先确认该账户实际地域，不能把某个地域连通推断成 Key 类型、额度或模型权限。`curl` 的 HTTP 401、403、404 或 405 表示目标有 HTTP 响应，仍需记录具体状态，不代表模型鉴权成功。

```sh
curl --silent --show-error --max-time 15 --output /dev/null --write-out 'business HTTP %{http_code}\n' http://127.0.0.1:8088/api/health
curl --silent --show-error --max-time 15 --output /dev/null --write-out 'business-domain HTTP %{http_code}\n' https://print.wisebeginner3d.com/
curl --silent --show-error --max-time 15 --output /dev/null --write-out 'pypi HTTP %{http_code}\n' https://pypi.org/simple/pip/
curl --silent --show-error --max-time 15 --output /dev/null --write-out 'npm HTTP %{http_code}\n' https://registry.npmjs.org/playwright
curl --silent --show-error --max-time 15 --output /dev/null --write-out 'dashscope HTTP %{http_code}\n' https://dashscope.aliyuncs.com/compatible-mode/v1
```

应另外记录每条命令的进程退出码：0 代表至少完成连接并获得 HTTP 响应，非 0 表示 DNS、TLS、连接或超时等传输失败；HTTP 状态仍需分别判断。`business` 检查本机平台，`business-domain` 单独检查真实业务域，不能互相代替；站点根路径的 HTTP 响应不证明业务 API 通过。业务健康检查除 HTTP 200 外还要检查响应中的健康状态。不得用此检查替代有鉴权的真实模型调用或业务断言。依赖源访问成功也不证明浏览器二进制下载成功。

## 3. 干净安装并执行冒烟

在新的练习副本或隔离目录内，按 `tests/week1_smoke/README.md` 使用其锁文件运行 `npm ci`，设置指向新目录的 `PLAYWRIGHT_BROWSERS_PATH`，再安装 Playwright 对应的 Chromium；记录安装输出和依赖版本。在同一个新终端按该 README 运行 API 和 UI 正向、故意失败的独立命令，分别保留原始日志、退出码、截图与 trace。原始故意失败必须非零，验收脚本可将“按预期失败”作为外层成功；不能把外层成功当作原始命令的退出码。

如果复用服务器上已有的 Chromium、npm 缓存或 Linux 动态库，要如实记录，不能称为全新下载；如果依赖或网络不可用，保留阻塞证据而不是改用其他机器的结果。不要把真实密钥、鉴权头或业务响应中的敏感字段写入日志、截图或版本库。

## 4. 计费类型和费用

用户初步判断“应该是按量计费”；此项尚待该 Key 所属账户的控制台或账单确认，不得当作已经核实的结论。Key 类型及地域也需确认，记录“一般 API／Coding Plan／其他／待核实”、账单周期及证据位置，不记录 Key、账单账号或付款信息。usage 中的 token 不是费用，不能根据 token 数直接断言实际账单；记录调用时间、模型和 usage，费用以账户的实际计费数据为准。控制台暂不可访问时明确写“待核实”。

## 5. 留档

保存代码提交、各运行结果和时间、系统及依赖版本、网络结果和退出码、截图与 trace 的位置、清理情况及待确认项。仅清理本次新建的隔离测试数据；无业务写入时记录“未创建业务数据”，不要清理其他人的资料。人工计时、视觉人工确认和本周最终验收保持独立状态。
