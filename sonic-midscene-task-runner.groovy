import org.cloud.sonic.agent.bridge.android.AndroidDeviceBridgeTool
import groovy.json.JsonSlurper

// ================== Sonic 接入配置 ==================
def bridgeVersion = "2026.07.15-bounded-ai-recovery-v1"
def bridgeTimeZone = java.util.TimeZone.getTimeZone("Asia/Shanghai")
def formatBridgeTime = { String pattern ->
    def formatter = new java.text.SimpleDateFormat(pattern)
    formatter.setTimeZone(bridgeTimeZone)
    return formatter.format(new Date())
}
// 正常情况下不用手工改模块/文件名：请从 Task 平台「同步到 Sonic」生成桥接脚本，
// 桥接脚本会传入稳定的 MIDSCENE_CASE_ID，避免 YAML 改名后 Sonic 测试套仍跑旧文件。
def readBindingVar = { String name ->
    try {
        return binding.hasVariable(name) ? String.valueOf(binding.getVariable(name) ?: "").trim() : ""
    } catch (Exception ignored) {
        return ""
    }
}

def readGlobalParam = { String name ->
    try {
        def gp = androidStepHandler?.globalParams
        if (gp == null) return ""
        try {
            return String.valueOf(gp.getString(name) ?: "").trim()
        } catch (Exception ignored) {}
        try {
            return String.valueOf(gp.get(name) ?: "").trim()
        } catch (Exception ignored) {}
    } catch (Exception ignored) {}
    return ""
}

def firstValue = { List values ->
    for (def item : values) {
        def value = String.valueOf(item ?: "").trim()
        if (value) return value
    }
    return ""
}

def midsceneCaseId = firstValue([
    readBindingVar("midsceneCaseId"),
    readBindingVar("MIDSCENE_CASE_ID"),
    System.getenv("MIDSCENE_CASE_ID"),
    readGlobalParam("MIDSCENE_CASE_ID"),
    readGlobalParam("midsceneCaseId")
])

// 兼容手工调试模式：只有没传 case_id 时才使用模块/文件名。
def taskModule = firstValue([
    readBindingVar("taskModule"),
    readBindingVar("MIDSCENE_MODULE"),
    System.getenv("MIDSCENE_MODULE"),
    readGlobalParam("MIDSCENE_MODULE"),
    readGlobalParam("taskModule")
])

def taskName = firstValue([
    readBindingVar("taskName"),
    readBindingVar("MIDSCENE_FILE"),
    readBindingVar("MIDSCENE_TASK_FILE"),
    System.getenv("MIDSCENE_FILE"),
    System.getenv("MIDSCENE_TASK_FILE"),
    readGlobalParam("MIDSCENE_FILE"),
    readGlobalParam("MIDSCENE_TASK_FILE"),
    readGlobalParam("taskName")
])

// 可选：只执行 YAML 中某一条用例；留空表示执行整个 YAML 文件。
def targetTaskName = firstValue([
    readBindingVar("targetTaskName"),
    readBindingVar("MIDSCENE_TASK_NAME"),
    System.getenv("MIDSCENE_TASK_NAME"),
    readGlobalParam("MIDSCENE_TASK_NAME"),
    readGlobalParam("targetTaskName")
])

// 可选：外部传入同一次 Sonic 测试套的稳定 ID；不传时 Task 平台会按应用/设备/运行模式自动聚合。
def sonicSuiteRunId = firstValue([
    readBindingVar("sonicSuiteRunId"),
    readBindingVar("MIDSCENE_SUITE_RUN_ID"),
    System.getenv("MIDSCENE_SUITE_RUN_ID"),
    readGlobalParam("MIDSCENE_SUITE_RUN_ID"),
    readGlobalParam("sonicSuiteRunId")
])

// 可选：如果 Sonic 后续能把测试套报告地址作为全局参数传入，这里会原样回传给 Task 平台聚合卡片。
def sonicReportUrl = firstValue([
    readBindingVar("sonicReportUrl"),
    readBindingVar("SONIC_REPORT_URL"),
    System.getenv("SONIC_REPORT_URL"),
    readGlobalParam("SONIC_REPORT_URL"),
    readGlobalParam("sonicReportUrl")
])

// 可选：Sonic 测试套级别信息。配置后 Task 平台会按预期总数等待，避免少用例就提前发飞书汇总。
def sonicSuiteId = firstValue([
    readBindingVar("sonicSuiteId"),
    readBindingVar("MIDSCENE_SUITE_ID"),
    readBindingVar("SONIC_SUITE_ID"),
    System.getenv("MIDSCENE_SUITE_ID"),
    System.getenv("SONIC_SUITE_ID"),
    readGlobalParam("MIDSCENE_SUITE_ID"),
    readGlobalParam("SONIC_SUITE_ID"),
    readGlobalParam("sonicSuiteId")
])

def sonicSuiteName = firstValue([
    readBindingVar("sonicSuiteName"),
    readBindingVar("MIDSCENE_SUITE_NAME"),
    readBindingVar("SONIC_SUITE_NAME"),
    System.getenv("MIDSCENE_SUITE_NAME"),
    System.getenv("SONIC_SUITE_NAME"),
    readGlobalParam("MIDSCENE_SUITE_NAME"),
    readGlobalParam("SONIC_SUITE_NAME"),
    readGlobalParam("sonicSuiteName")
])

def sonicSuiteStartedAt = firstValue([
    readBindingVar("sonicSuiteStartedAt"),
    readBindingVar("MIDSCENE_SUITE_STARTED_AT"),
    readBindingVar("SONIC_SUITE_STARTED_AT"),
    System.getenv("MIDSCENE_SUITE_STARTED_AT"),
    System.getenv("SONIC_SUITE_STARTED_AT"),
    readGlobalParam("MIDSCENE_SUITE_STARTED_AT"),
    readGlobalParam("SONIC_SUITE_STARTED_AT"),
    readGlobalParam("sonicSuiteStartedAt")
])

def parsePositiveInt = { String value ->
    try {
        def n = Integer.parseInt(String.valueOf(value ?: "").trim())
        return n > 0 ? n : 0
    } catch (Exception ignored) {
        return 0
    }
}

def sonicSuiteExpectedTotal = parsePositiveInt(firstValue([
    readBindingVar("sonicSuiteExpectedTotal"),
    readBindingVar("MIDSCENE_SUITE_TOTAL"),
    readBindingVar("MIDSCENE_SUITE_EXPECTED_TOTAL"),
    readBindingVar("SONIC_SUITE_TOTAL"),
    System.getenv("MIDSCENE_SUITE_TOTAL"),
    System.getenv("MIDSCENE_SUITE_EXPECTED_TOTAL"),
    System.getenv("SONIC_SUITE_TOTAL"),
    readGlobalParam("MIDSCENE_SUITE_TOTAL"),
    readGlobalParam("MIDSCENE_SUITE_EXPECTED_TOTAL"),
    readGlobalParam("SONIC_SUITE_TOTAL"),
    readGlobalParam("sonicSuiteExpectedTotal")
]))

// Sonic 基线执行不设置外层 Midscene 进程超时，避免误伤文件导入、模型处理、打印等长链路。
// 超时判断交给 Midscene/YAML/Sonic 自身机制处理。
// =========================================================

// 默认按“基线回归”记录结果，但不自动改已经验证过的脚本
def runMode = "baseline"   // test=测试执行；baseline=基线回归
def autoOptimize = false   // 稳定基线默认 false；需要脚本维护时再临时改 true

def taskServer = firstValue([
    readBindingVar("taskServer"),
    readBindingVar("TASK_SERVER"),
    System.getenv("TASK_SERVER"),
    readGlobalParam("TASK_SERVER"),
    "http://101.34.197.12:8088"
])
def runnerToken = firstValue([
    readBindingVar("runnerToken"),
    readBindingVar("MIDSCENE_RUNNER_TOKEN"),
    System.getenv("MIDSCENE_RUNNER_TOKEN"),
    readGlobalParam("MIDSCENE_RUNNER_TOKEN")
])
def weakRunnerTokens = ["", "midscene2026", "change-me", "changeme", "test", "token"] as Set
if (weakRunnerTokens.contains(String.valueOf(runnerToken ?: "").trim())) {
    throw new RuntimeException("MIDSCENE_RUNNER_TOKEN 未配置或仍使用弱默认值，请从 Task 平台重新同步 Sonic 桥接脚本")
}

def runtimeEnvFetch = [ok: false, source: "Task 服务端", detail: "尚未读取"]
def fetchRuntimeEnvFromTaskServer = {
    try {
        // Groovy String 没有 Python 风格的 rstrip；该异常会导致服务端 Key 从未被读取。
        def serverBase = String.valueOf(taskServer ?: "").replaceAll('/+$', '')
        def url = serverBase + "/api/sonic/runtime-env"
        def conn = new URL(url).openConnection()
        conn.setRequestProperty("x-token", runnerToken)
        conn.setConnectTimeout(10000)
        conn.setReadTimeout(15000)
        def status = conn.responseCode
        def stream = status >= 200 && status < 300 ? conn.inputStream : conn.errorStream
        def text = stream ? stream.getText("UTF-8") : ""
        if (status < 200 || status >= 300) {
            runtimeEnvFetch = [ok: false, source: "Task 服务端", detail: "HTTP ${status}"]
            return [:]
        }
        def parsed = new JsonSlurper().parseText(text)
        if (parsed?.ok && parsed?.env instanceof Map) {
            def envMap = [:]
            parsed.env.each { key, value ->
                def k = String.valueOf(key ?: "").trim()
                def v = String.valueOf(value ?: "").trim()
                if (k && v) envMap[k] = v
            }
            runtimeEnvFetch = [
                ok: !!(envMap["DASHSCOPE_API_KEY"] || envMap["OPENAI_API_KEY"]),
                source: "Task 服务端",
                detail: (envMap["DASHSCOPE_API_KEY"] || envMap["OPENAI_API_KEY"]) ? "模型配置已下发" : "接口可访问，但服务端未配置模型 Key"
            ]
            return envMap
        }
        runtimeEnvFetch = [ok: false, source: "Task 服务端", detail: "接口返回格式无效"]
    } catch (Exception e) {
        runtimeEnvFetch = [ok: false, source: "Task 服务端", detail: "${e.class.simpleName}: ${e.message ?: '读取失败'}"]
    }
    return [:]
}

def runtimeEnv = fetchRuntimeEnvFromTaskServer()

// 模型环境只从 Task 服务端或 Agent/Sonic 参数继承，避免脚本中存放明文凭据。
def fallbackDashscopeApiKey = ""
def fallbackDashscopeBaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"
def fallbackDashscopeModel = "qwen3.6-plus"
def fallbackDashscopeVlModel = "qwen3.6-plus"

def appNameByPackage = [
    "com.kfb.model": "3D 打印",
    "com.xbxxhz.box": "小白学习打印"
]
def dashscopeApiKey = firstValue([
    runtimeEnv["DASHSCOPE_API_KEY"],
    runtimeEnv["OPENAI_API_KEY"],
    readBindingVar("DASHSCOPE_API_KEY"),
    readBindingVar("OPENAI_API_KEY"),
    readGlobalParam("DASHSCOPE_API_KEY"),
    readGlobalParam("OPENAI_API_KEY"),
    System.getenv("DASHSCOPE_API_KEY"),
    System.getenv("OPENAI_API_KEY"),
    fallbackDashscopeApiKey
])
def dashscopeBaseUrl = firstValue([
    runtimeEnv["DASHSCOPE_BASE_URL"],
    runtimeEnv["OPENAI_BASE_URL"],
    readBindingVar("DASHSCOPE_BASE_URL"),
    readBindingVar("OPENAI_BASE_URL"),
    readGlobalParam("DASHSCOPE_BASE_URL"),
    readGlobalParam("OPENAI_BASE_URL"),
    System.getenv("DASHSCOPE_BASE_URL"),
    System.getenv("OPENAI_BASE_URL"),
    fallbackDashscopeBaseUrl
])
def midsceneModelName = firstValue([
    runtimeEnv["MIDSCENE_MODEL_NAME"],
    runtimeEnv["DASHSCOPE_VL_MODEL"],
    runtimeEnv["DASHSCOPE_MODEL"],
    readBindingVar("MIDSCENE_MODEL_NAME"),
    readBindingVar("DASHSCOPE_VL_MODEL"),
    readBindingVar("DASHSCOPE_MODEL"),
    readGlobalParam("MIDSCENE_MODEL_NAME"),
    readGlobalParam("DASHSCOPE_VL_MODEL"),
    readGlobalParam("DASHSCOPE_MODEL"),
    System.getenv("MIDSCENE_MODEL_NAME"),
    System.getenv("DASHSCOPE_VL_MODEL"),
    System.getenv("DASHSCOPE_MODEL"),
    fallbackDashscopeVlModel,
    fallbackDashscopeModel
])
def configuredMidsceneReplanningCycleLimit = firstValue([
    runtimeEnv["MIDSCENE_REPLANNING_CYCLE_LIMIT"],
    readBindingVar("MIDSCENE_REPLANNING_CYCLE_LIMIT"),
    readGlobalParam("MIDSCENE_REPLANNING_CYCLE_LIMIT"),
    System.getenv("MIDSCENE_REPLANNING_CYCLE_LIMIT"),
    "8"
])
int parsedMidsceneReplanningCycleLimit = 8
try {
    parsedMidsceneReplanningCycleLimit = Integer.parseInt(configuredMidsceneReplanningCycleLimit)
} catch (Exception ignored) {}
// This is a capacity ceiling, not a fixed loop count. Normal actions still stop
// immediately, while multi-dialog cleanup gets enough room to reach a stable page.
def midsceneReplanningCycleLimit = String.valueOf(Math.max(8, parsedMidsceneReplanningCycleLimit))
def deviceSerial = androidStepHandler.iDevice.getSerialNumber()
def adbPath = "\"C:\\Program Files\\platform-tools\\adb.exe\""

def localTaskDir = ""
def localTaskPath = ""
def jobId = "sonic_${System.currentTimeMillis()}"
def taskNames = []
def totalTaskCount = 0
def completedTaskCount = 0
def currentTaskIndex = 0
def currentTaskName = ""
def currentAppPackage = ""
def caseExecutionContext = [:]

def encodeUrlPart = { String s ->
    java.net.URLEncoder.encode(s ?: "", "UTF-8").replace("+", "%20")
}

def escapeJson = { String s ->
    (s ?: "")
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\b", "\\b")
        .replace("\f", "\\f")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
}

def compactLog = { String s, int limit ->
    s = s ?: ""
    return s.length() > limit ? s.substring(s.length() - limit) : s
}

def repairUtf8ReadAsGbk = { String s ->
    s = s ?: ""
    if (!(s.contains("锛") || s.contains("涓") || s.contains("褰") || s.contains("妗") || s.contains("鐣") || s.contains("杈") || s.contains("閬"))) {
        return s
    }
    try {
        return new String(s.getBytes("GBK"), "UTF-8")
    } catch (Exception ignored) {
        return s
    }
}

def readStreamUtf8 = { InputStream stream, StringBuffer target ->
    Thread.start {
        try {
            stream.withReader("UTF-8") { reader ->
                String line
                while ((line = reader.readLine()) != null) {
                    target.append(line).append("\n")
                }
            }
        } catch (Exception ignored) {}
    }
}

def parseTaskNames = { String yaml ->
    def names = []
    (yaml ?: "").eachLine { line ->
        def m = line =~ /^\s*-\s+name:\s*(.+?)\s*$/
        if (m) {
            def name = m[0][1].trim()
            if ((name.startsWith("\"") && name.endsWith("\"")) || (name.startsWith("'") && name.endsWith("'"))) {
                name = name.substring(1, name.length() - 1)
            }
            if (name) names << name
        }
    }
    return names
}

def parseAppPackage = { String yaml ->
    def pkg = ""
    (yaml ?: "").eachLine { line ->
        if (pkg) return
        def m = line =~ /^\s*-\s+(?:launch|terminate)\s*:\s*["']?([^"'\s#]+)/
        if (m && m[0][1].contains(".")) {
            pkg = m[0][1].trim()
        }
    }
    return pkg
}

def injectExternalPageEscape = { String yaml ->
    def result = []
    (yaml ?: "").readLines().each { line ->
        if (line ==~ /^\s*-\s+terminate\s*:.*/) {
            def indent = (line =~ /^(\s*)/)[0][1]
            def from = Math.max(0, result.size() - 6)
            def recent = result.subList(from, result.size()).join("\n")
            if (!recent.contains("runAdbShell:")) {
                result << "${indent}- runAdbShell: \"input keyevent 3\""
                result << "${indent}- sleep: 500"
            }
        }
        result << line
    }
    return result.join("\n") + "\n"
}

def runCmd = { String cmd, int timeoutSeconds = 0 ->
    def proc = ["cmd", "/c", cmd].execute()
    def out = new StringBuffer()
    def err = new StringBuffer()
    def outThread = readStreamUtf8(proc.inputStream, out)
    def errThread = readStreamUtf8(proc.errorStream, err)
    def finished = timeoutSeconds > 0 ? proc.waitFor(timeoutSeconds, java.util.concurrent.TimeUnit.SECONDS) : proc.waitFor()
    if (!finished) {
        try {
            proc.destroyForcibly()
        } catch (Exception ignored) {}
    }
    outThread.join(3000)
    errThread.join(3000)
    def code = finished ? proc.exitValue() : 124
    def stderrText = err.toString()
    if (!finished) stderrText = (stderrText ? stderrText + "\n" : "") + "command timeout after ${timeoutSeconds}s"
    return [code: code, stdout: out.toString(), stderr: stderrText]
}

def configureMidsceneProcess = { ProcessBuilder builder, String replanningLimit ->
    builder.directory(new File("D:\\sonic"))
    builder.environment().put("ANDROID_HOME", "C:\\Program Files")
    builder.environment().put("OPENAI_API_KEY", dashscopeApiKey)
    builder.environment().put("OPENAI_BASE_URL", dashscopeBaseUrl)
    builder.environment().put("DASHSCOPE_API_KEY", dashscopeApiKey)
    builder.environment().put("DASHSCOPE_BASE_URL", dashscopeBaseUrl)
    builder.environment().put("DASHSCOPE_VL_MODEL", runtimeEnv["DASHSCOPE_VL_MODEL"] ?: midsceneModelName)
    builder.environment().put("DASHSCOPE_MODEL", runtimeEnv["DASHSCOPE_MODEL"] ?: midsceneModelName)
    builder.environment().put("MIDSCENE_MODEL_NAME", midsceneModelName)
    builder.environment().put("MIDSCENE_USE_QWEN_VL", runtimeEnv["MIDSCENE_USE_QWEN_VL"] ?: "1")
    builder.environment().put("MIDSCENE_SKIP_CONFIG_CHECK", runtimeEnv["MIDSCENE_SKIP_CONFIG_CHECK"] ?: "1")
    builder.environment().put("MIDSCENE_REPLANNING_CYCLE_LIMIT", replanningLimit)
    builder.environment().put("NODE_TLS_REJECT_UNAUTHORIZED", runtimeEnv["NODE_TLS_REJECT_UNAUTHORIZED"] ?: "0")
    if (runtimeEnv["APP_PACKAGE"]) {
        builder.environment().put("APP_PACKAGE", runtimeEnv["APP_PACKAGE"])
    }
    builder.redirectErrorStream(true)
    return builder
}

def runFailureRecovery = { String appPackage ->
    def recoveryFile = new File("D:\\sonic\\midscene-failure-recovery-${jobId}.yaml")
    def result = [attempted: true, ok: false, detail: ""]
    try {
        recoveryFile.setText("""android:
  deviceId: "${deviceSerial}"

tasks:
  - name: Sonic失败后状态恢复
    flow:
      - launch: ${appPackage}
      - aiAction: >-
          失败后仅做状态恢复：观察当前应用页面，关闭阻塞弹窗；若存在未完成的编辑、处理、预览、确认或取消流程，按页面真实可见文字安全取消或退出，并处理必要确认，直到回到应用首页或主导航稳定页面。不要开始新业务，不要提交、支付、打印或删除数据；如果已经在首页则不操作。
      - runAdbShell: "am force-stop ${appPackage}"
""", "UTF-8")

        int configuredLimit = 8
        try {
            configuredLimit = Integer.parseInt(String.valueOf(midsceneReplanningCycleLimit ?: "8"))
        } catch (Exception ignored) {}
        def recoveryLimit = String.valueOf(Math.max(8, configuredLimit))
        def recoveryBuilder = configureMidsceneProcess(
            new ProcessBuilder("cmd", "/c", "midscene \"${recoveryFile.absolutePath}\""),
            recoveryLimit
        )
        def recoveryProcess = recoveryBuilder.start()
        def recoveryOutput = new StringBuffer()
        def recoveryReader = readStreamUtf8(recoveryProcess.inputStream, recoveryOutput)
        def finished = recoveryProcess.waitFor(180, java.util.concurrent.TimeUnit.SECONDS)
        if (!finished) {
            try {
                recoveryProcess.destroyForcibly()
            } catch (Exception ignored) {}
        }
        recoveryReader.join(5000)
        def outputText = recoveryOutput.toString()
        def exitCode = finished ? recoveryProcess.exitValue() : 124
        result.ok = finished && exitCode == 0
        result.detail = result.ok
            ? "AI 已将应用恢复到稳定起点（重规划上限 ${recoveryLimit}）"
            : "AI 状态恢复${finished ? '失败' : '超时'}（退出码 ${exitCode}）：${compactLog(outputText, 1200)}"

        def recoveryReportLine = outputText.readLines().reverse().find {
            it.contains("report finalized:") || it.contains("report generated:")
        }
        if (recoveryReportLine) {
            def recoveryReportPath = recoveryReportLine.replaceAll(/.*report (?:finalized|generated):\s*/, "").trim()
            if (recoveryReportPath) new File(recoveryReportPath).delete()
        }
    } catch (Exception e) {
        result.detail = "AI 状态恢复异常：${e.message ?: String.valueOf(e)}"
    } finally {
        recoveryFile.delete()
        def stopResult = runCmd("${adbPath} -s ${deviceSerial} shell am force-stop ${appPackage}", 8)
        if (stopResult.code != 0) {
            result.ok = false
            def stopDetail = compactLog(stopResult.stderr ?: stopResult.stdout ?: "无返回内容", 300)
            result.detail = "${result.detail}；恢复后强停应用失败：${stopDetail}"
        }
    }
    return result
}

def preflightCheck = {
    def issues = []
    def details = []
    details << "桥接版本：${bridgeVersion}"
    details << "模型配置来源：${runtimeEnvFetch.source} - ${runtimeEnvFetch.detail}"
    def midsceneVersion = runCmd("midscene --version", 15)
    if (midsceneVersion.code != 0) {
        issues << "midscene 命令不可用"
        details << "midscene --version：${midsceneVersion.stderr ?: midsceneVersion.stdout}"
    } else {
        details << "midscene：${(midsceneVersion.stdout ?: midsceneVersion.stderr).trim()}"
    }
    def adbDevices = runCmd("${adbPath} devices", 15)
    if (adbDevices.code != 0 || !adbDevices.stdout.contains("${deviceSerial}\tdevice")) {
        issues << "adb 未识别当前 Sonic 设备"
        details << "adb devices：${adbDevices.stdout ?: adbDevices.stderr}"
    } else {
        details << "adb：当前设备 ${deviceSerial} 在线"
    }
    if (!dashscopeApiKey) {
        issues << "未配置 DASHSCOPE_API_KEY/OPENAI_API_KEY（${runtimeEnvFetch.detail}）"
    }
    if (!midsceneModelName) {
        issues << "未配置 MIDSCENE_MODEL_NAME"
    } else {
        details << "模型：${midsceneModelName}"
    }
    details << "重规划上限：${midsceneReplanningCycleLimit}"
    return [ok: issues.isEmpty(), issues: issues, details: details]
}

def parseCurlResponse = { result ->
    def lines = (result.stdout ?: "").readLines()
    def httpCode = lines ? lines[-1].trim() : ""
    def body = lines.size() > 1 ? lines[0..-2].join("\n").trim() : ""
    return [httpCode: httpCode, body: body]
}

def shouldFallbackToChunkUpload = { String httpCode, String stdout ->
    def text = stdout ?: ""
    return ["413", "502", "504"].contains(httpCode) ||
        text.contains("413 Request Entity Too Large") ||
        text.contains("502 Bad Gateway") ||
        text.contains("504 Gateway Time-out")
}

def uploadReportFile = { File reportFile, String reportFileName ->
    if (!reportFile || !reportFile.exists()) {
        return [url: "", error: "报告文件不存在", localPath: reportFile?.absolutePath ?: ""]
    }
    // Large Midscene HTML reports often contain embedded screenshots. Avoid
    // spending time sending a known-large request only to retry it as chunks.
    int directUploadLimit = 4 * 1024 * 1024
    if (reportFile.length() <= directUploadLimit) {
        def encodedFileName = encodeUrlPart(reportFileName)
        def directCmd = "curl --connect-timeout 5 --max-time 45 -s -w \"\\n%{http_code}\" -X POST \"${taskServer}/report\"" +
            " -H \"x-token: ${runnerToken}\"" +
            " -H \"x-filename: ${encodedFileName}\"" +
            " --data-binary \"@${reportFile.absolutePath}\""
        def direct = runCmd(directCmd, 50)
        def directResp = parseCurlResponse(direct)
        def httpCode = directResp.httpCode
        def body = directResp.body
        if (direct.code == 0 && httpCode.startsWith("2") && body) {
            return [url: body, error: "", localPath: reportFile.absolutePath]
        }
        if (!shouldFallbackToChunkUpload(httpCode, direct.stdout ?: "")) {
            return [url: "", error: "报告上传失败 HTTP ${httpCode ?: direct.code}: ${body ?: direct.stderr}", localPath: reportFile.absolutePath]
        }
    }

    def bytes = reportFile.bytes
    int chunkSize = 1024 * 1024
    int total = Math.max(1, (int)Math.ceil(bytes.length / (double)chunkSize))
    def uploadId = "sonic_${jobId}_${System.currentTimeMillis()}".replaceAll(/[^A-Za-z0-9_.-]/, "_")
    for (int i = 0; i < total; i++) {
        int start = i * chunkSize
        int end = Math.min(bytes.length, start + chunkSize)
        byte[] chunk = java.util.Arrays.copyOfRange(bytes, start, end)
        def b64 = java.util.Base64.getEncoder().encodeToString(chunk)
        def chunkPayload = new File("D:\\sonic\\midscene_report_chunk_${jobId}_${i}.json")
        chunkPayload.setText("""{"upload_id":"${escapeJson(uploadId)}","filename":"${escapeJson(reportFileName)}","index":${i},"total":${total},"contentBase64":"${b64}"}""", "UTF-8")
        def chunkCmd = "curl --connect-timeout 5 --max-time 45 -s -w \"\\n%{http_code}\" -X POST \"${taskServer}/api/report/chunk\"" +
            " -H \"x-token: ${runnerToken}\"" +
            " -H \"Content-Type: application/json\"" +
            " --data-binary \"@${chunkPayload.absolutePath}\""
        def chunkResult = runCmd(chunkCmd, 50)
        def chunkResp = parseCurlResponse(chunkResult)
        def chunkCode = chunkResp.httpCode
        chunkPayload.delete()
        if (chunkResult.code != 0 || !chunkCode.startsWith("2")) {
            return [url: "", error: "报告分片上传失败 ${i + 1}/${total} HTTP ${chunkCode ?: chunkResult.code}: ${chunkResp.body ?: chunkResult.stderr}", localPath: reportFile.absolutePath]
        }
    }
    def finishPayload = new File("D:\\sonic\\midscene_report_finish_${jobId}.json")
    finishPayload.setText("""{"upload_id":"${escapeJson(uploadId)}","total":${total}}""", "UTF-8")
    def finishCmd = "curl --connect-timeout 5 --max-time 45 -s -w \"\\n%{http_code}\" -X POST \"${taskServer}/api/report/chunk-finish\"" +
        " -H \"x-token: ${runnerToken}\"" +
        " -H \"Content-Type: application/json\"" +
        " --data-binary \"@${finishPayload.absolutePath}\""
    def finish = runCmd(finishCmd, 50)
    finishPayload.delete()
    def finishResp = parseCurlResponse(finish)
    def finishCode = finishResp.httpCode
    def finishBody = finishResp.body
    if (finish.code == 0 && finishCode.startsWith("2")) {
        def m = finishBody =~ /"url"\s*:\s*"([^"]+)"/
        def url = m ? m[0][1].replace("\\/", "/") : ""
        return [url: url, error: url ? "" : "分片完成但未返回报告 URL", localPath: reportFile.absolutePath]
    }
    return [url: "", error: "报告分片合并失败 HTTP ${finishCode ?: finish.code}: ${finishBody ?: finish.stderr}", localPath: reportFile.absolutePath]
}

def resetForegroundApp = { String appPackage ->
    if (!appPackage) return 0L
    def startedAt = System.currentTimeMillis()
    def currentPkg = ""
    try {
        def focusResult = runCmd("${adbPath} -s ${deviceSerial} shell dumpsys window", 10)
        def focusText = focusResult.stdout ?: ""
        def m = focusText =~ /mCurrentFocus=.*?\s([A-Za-z0-9_.]+)\/[A-Za-z0-9_.$]+/
        if (!m) m = focusText =~ /mFocusedApp=.*?\s([A-Za-z0-9_.]+)\/[A-Za-z0-9_.$]+/
        if (m && m[0][1]?.contains(".")) currentPkg = m[0][1].trim()
    } catch (Exception e) {
        androidStepHandler.log.sendStepLog(2, "ADB前置告警", "读取前台包名失败: ${e.message}")
    }
    def width = 1080
    def height = 2400
    try {
        def sizeResult = runCmd("${adbPath} -s ${deviceSerial} shell wm size", 10)
        def m = (sizeResult.stdout ?: "") =~ /(\d+)x(\d+)/
        if (m) {
            width = (m[0][1] as int)
            height = (m[0][2] as int)
        }
    } catch (Exception e) {
        androidStepHandler.log.sendStepLog(2, "ADB前置告警", "wm size: ${e.message}")
    }
    def x = Math.max(1, (width / 2) as int)
    def y1 = Math.max(1, (height * 0.82) as int)
    def y2 = Math.max(1, (height * 0.18) as int)
    def commands = [
        "shell input keyevent 3",
        "shell input keyevent 187",
        "shell input swipe ${x} ${y1} ${x} ${y2} 300",
        "shell input swipe ${x} ${y1} ${x} ${y2} 300",
        "shell input swipe ${x} ${y1} ${x} ${y2} 300",
        "shell input keyevent 3",
        "shell am kill-all"
    ]
    if (currentPkg && currentPkg != appPackage && !currentPkg.startsWith("com.android.systemui") && !currentPkg.contains("launcher")) {
        commands.add(0, "shell am force-stop ${currentPkg}")
    }
    commands << "shell am force-stop ${appPackage}"
    commands.each { adbArg ->
        def result = runCmd("${adbPath} -s ${deviceSerial} ${adbArg}", 8)
        if (result.code != 0) {
            def detail = compactLog(result.stderr ?: result.stdout ?: "无返回内容", 300)
            throw new RuntimeException("设备前置清理失败，命令未完成：${adbArg}（退出码 ${result.code}，${detail}）")
        }
        Thread.sleep(adbArg.contains("input swipe") ? 250 : 150)
    }
    Thread.sleep(300)
    return System.currentTimeMillis() - startedAt
}

def restoreSonicDriverWithTimeout = { int timeoutSeconds ->
    def executor = java.util.concurrent.Executors.newSingleThreadExecutor()
    def future = executor.submit({
        int port = AndroidDeviceBridgeTool.startUiaServer(androidStepHandler.iDevice)
        androidStepHandler.startAndroidDriver(androidStepHandler.iDevice, port)
        return port
    } as java.util.concurrent.Callable)
    try {
        def port = future.get(timeoutSeconds, java.util.concurrent.TimeUnit.SECONDS)
        return [ok: true, port: port, error: ""]
    } catch (java.util.concurrent.TimeoutException e) {
        future.cancel(true)
        return [ok: false, port: 0, error: "Sonic Driver 恢复超过 ${timeoutSeconds} 秒未完成，已终止等待，避免测试套长期卡住"]
    } catch (Exception e) {
        return [ok: false, port: 0, error: "Sonic Driver 恢复失败：${e.cause?.message ?: e.message ?: String.valueOf(e)}"]
    } finally {
        executor.shutdownNow()
    }
}

def postResultToTaskManager = { String status, int exitCode, String output, String errorDetail, String reportUrl, String caseName, String reportUploadError, String localReportPath ->
    try {
        def payloadFile = new File("D:\\sonic\\midscene_result_${jobId}.json")
        def now = formatBridgeTime("yyyy-MM-dd HH:mm:ss")
        def computedProgress = totalTaskCount > 0 ? (5 + Math.round((completedTaskCount * 90.0) / totalTaskCount) as int) : 0
        def finalProgress = status == "success" ? 100 : Math.max(0, Math.min(99, computedProgress))
        def finalCompleted = status == "success" ? Math.max(completedTaskCount, totalTaskCount) : Math.max(0, completedTaskCount)
        def payload = """{
  "job_id": "${escapeJson(jobId)}",
  "module": "${escapeJson(taskModule)}",
  "file": "${escapeJson(taskName)}",
  "case_id": "${escapeJson(midsceneCaseId ?: "")}",
  "suite_run_id": "${escapeJson(sonicSuiteRunId ?: "")}",
  "sonic_suite_id": "${escapeJson(sonicSuiteId ?: "")}",
  "suite_name": "${escapeJson(sonicSuiteName ?: "")}",
  "suite_started_at": "${escapeJson(sonicSuiteStartedAt ?: "")}",
  "suite_expected_total": ${Math.max(0, sonicSuiteExpectedTotal)},
  "caseName": "${escapeJson(targetTaskName ?: caseName)}",
  "target_task_name": "${escapeJson(targetTaskName ?: "")}",
  "status": "${escapeJson(status)}",
  "exitCode": ${exitCode},
  "deviceId": "${escapeJson(deviceSerial)}",
  "runnerId": "sonic",
  "appPackage": "${escapeJson(currentAppPackage ?: "")}",
  "appName": "${escapeJson(appNameByPackage[currentAppPackage] ?: currentAppPackage ?: "")}",
  "run_mode": "${escapeJson(runMode)}",
  "autoOptimize": ${autoOptimize ? "true" : "false"},
  "created_at": "${now}",
  "progress": ${finalProgress},
  "current_task_name": "${escapeJson(currentTaskName ?: targetTaskName ?: "")}",
  "current_task_index": ${Math.max(0, currentTaskIndex)},
  "completed_task_count": ${finalCompleted},
  "total_task_count": ${Math.max(0, totalTaskCount)},
  "stdout": "${escapeJson(compactLog(output, 12000))}",
  "stderr": "${escapeJson(compactLog(errorDetail, 4000))}",
  "error": "${escapeJson(errorDetail)}",
  "reportUrl": "${escapeJson(reportUrl)}",
  "sonicReportUrl": "${escapeJson(sonicReportUrl ?: "")}",
  "report_upload_error": "${escapeJson(reportUploadError)}",
  "report_upload_pending": ${localReportPath && !reportUploadError ? "true" : "false"},
  "local_report_path": "${escapeJson(localReportPath)}"
}"""
        payloadFile.setText(payload, "UTF-8")
        def cmd = "curl --connect-timeout 5 --max-time 20 -s -w \"\\n%{http_code}\" -X POST \"${taskServer}/api/sonic/result\"" +
            " -H \"Content-Type: application/json\"" +
            " -H \"x-token: ${runnerToken}\"" +
            " --data-binary \"@${payloadFile.absolutePath}\""
        def result = runCmd(cmd, 25)
        def resp = parseCurlResponse(result)
        if (result.code != 0 || !(resp.httpCode ?: "").startsWith("2")) {
            def responseError = repairUtf8ReadAsGbk(compactLog(resp.body ?: result.stderr, 500))
            androidStepHandler.log.sendStepLog(2, "Task结果归档失败", "Task 平台未确认执行结果，HTTP：${resp.httpCode ?: result.code}\n${responseError}")
            return
        }
        def response = [:]
        try {
            response = resp.body ? new JsonSlurper().parseText(resp.body) : [:]
        } catch (Exception ignored) {}
        def resultText = status == "success" ? "成功" : "失败"
        def archiveMessage = "Task 平台已接收本次${runMode == 'baseline' ? '基线' : '测试'}执行结果：${resultText}"
        if (reportUrl && localReportPath) {
            archiveMessage += "\nMidscene HTML 报告地址已预留，文件正在后台上传：${reportUrl}"
        } else if (reportUrl) {
            archiveMessage += "\nMidscene HTML 报告已关联到执行记录：${reportUrl}"
        }
        androidStepHandler.log.sendStepLog(2, "Task结果归档", archiveMessage)
        def optimize = response instanceof Map ? response.optimize : null
        if (autoOptimize && optimize instanceof Map && optimize.ok == true && optimize.next_job) {
            androidStepHandler.log.sendStepLog(2, "脚本维护任务", "已按人工启用的维护模式创建修复重跑任务")
        }
    } catch (Exception e) {
        androidStepHandler.log.sendStepLog(2, "Task结果归档失败", e.message ?: String.valueOf(e))
    }
}

def postReportAttachmentToTaskManager = { String reportUrl, String reportUploadError, String localReportPath ->
    try {
        def payloadFile = new File("D:\\sonic\\midscene_report_result_${jobId}.json")
        def payload = """{
  "job_id": "${escapeJson(jobId)}",
  "reportUrl": "${escapeJson(reportUrl)}",
  "report_upload_error": "${escapeJson(reportUploadError)}",
  "local_report_path": "${escapeJson(localReportPath)}"
}"""
        payloadFile.setText(payload, "UTF-8")
        def cmd = "curl --connect-timeout 5 --max-time 20 -s -w \"\\n%{http_code}\" -X POST \"${taskServer}/api/sonic/report-ready\"" +
            " -H \"Content-Type: application/json\"" +
            " -H \"x-token: ${runnerToken}\"" +
            " --data-binary \"@${payloadFile.absolutePath}\""
        runCmd(cmd, 25)
        payloadFile.delete()
    } catch (Exception ignored) {}
}

def postProgressToTaskManager = { int progress, String currentTask, int currentIndex, int completedCount, int totalCount, String outputTail, String message ->
    try {
        def payloadFile = new File("D:\\sonic\\midscene_progress_${jobId}.json")
        def now = formatBridgeTime("yyyy-MM-dd HH:mm:ss")
        def payload = """{
  "job_id": "${escapeJson(jobId)}",
  "module": "${escapeJson(taskModule)}",
  "file": "${escapeJson(taskName)}",
  "case_id": "${escapeJson(midsceneCaseId ?: "")}",
  "suite_run_id": "${escapeJson(sonicSuiteRunId ?: "")}",
  "sonic_suite_id": "${escapeJson(sonicSuiteId ?: "")}",
  "suite_name": "${escapeJson(sonicSuiteName ?: "")}",
  "suite_started_at": "${escapeJson(sonicSuiteStartedAt ?: "")}",
  "suite_expected_total": ${Math.max(0, sonicSuiteExpectedTotal)},
  "caseName": "${escapeJson(targetTaskName ?: currentTask ?: taskName.replace('.yaml', ''))}",
  "target_task_name": "${escapeJson(targetTaskName ?: "")}",
  "status": "running",
  "exitCode": 1,
  "deviceId": "${escapeJson(deviceSerial)}",
  "runnerId": "sonic",
  "appPackage": "${escapeJson(currentAppPackage ?: "")}",
  "appName": "${escapeJson(appNameByPackage[currentAppPackage] ?: currentAppPackage ?: "")}",
  "run_mode": "${escapeJson(runMode)}",
  "autoOptimize": ${autoOptimize ? "true" : "false"},
  "created_at": "${now}",
  "progress": ${Math.max(0, Math.min(99, progress))},
  "current_task_name": "${escapeJson(currentTask ?: '')}",
  "current_task_index": ${Math.max(0, currentIndex)},
  "completed_task_count": ${Math.max(0, completedCount)},
  "total_task_count": ${Math.max(0, totalCount)},
  "message": "${escapeJson(message ?: '')}",
  "stdout": "${escapeJson(compactLog(outputTail, 2000))}",
  "stderr": "",
  "error": "",
  "reportUrl": "",
  "sonicReportUrl": "${escapeJson(sonicReportUrl ?: "")}"
}"""
        payloadFile.setText(payload, "UTF-8")
        def cmd = "curl --connect-timeout 3 --max-time 5 -s -w \"\\n%{http_code}\" -X POST \"${taskServer}/api/sonic/result\"" +
            " -H \"Content-Type: application/json\"" +
            " -H \"x-token: ${runnerToken}\"" +
            " --data-binary \"@${payloadFile.absolutePath}\""
        def result = runCmd(cmd, 8)
        def resp = parseCurlResponse(result)
        if (result.code != 0 || !(resp.httpCode ?: "").startsWith("2")) {
            androidStepHandler.log.sendStepLog(2, "进度回传失败", "HTTP：${resp.httpCode ?: result.code}\n${resp.body ?: result.stderr}")
        }
    } catch (Exception e) {}
}

if (!midsceneCaseId && (!taskModule || !taskName || taskName == "需要修改的名称.yaml")) {
    def msg = "Sonic 用例未配置 MIDSCENE_CASE_ID，也没有有效的 MIDSCENE_MODULE/MIDSCENE_FILE。请在 Task 平台重新「同步到 Sonic」，或替换 Sonic 用例里的 Groovy 桥接脚本；已禁止回退到默认脚本。"
    androidStepHandler.log.sendStepLog(2, "Midscene桥接配置错误", msg)
    throw new RuntimeException(msg)
}

def yamlFromCaseApi = ""
if (midsceneCaseId) {
    try {
        def caseUrl = "${taskServer}/api/sonic/case?case_id=${encodeUrlPart(midsceneCaseId)}"
        def conn = new URL(caseUrl).openConnection()
        conn.setRequestProperty("x-token", runnerToken)
        conn.setConnectTimeout(15000)
        conn.setReadTimeout(30000)
        def payload = new JsonSlurper().parseText(conn.inputStream.getText("UTF-8"))
        if (!payload.ok) {
            throw new RuntimeException(payload.error ?: "case_id 查询失败")
        }
        def c = payload.case ?: [:]
        // 重要:即使 API 返回了 module/file,也要保留原始值作为后备
        def apiModule = String.valueOf(c.module ?: "").trim()
        def apiFile = String.valueOf(c.file ?: "").trim()
        def apiTaskName = String.valueOf(c.task_name ?: "").trim()
        
        // 只有 API 返回有效值时才覆盖
        if (apiModule) taskModule = apiModule
        if (apiFile) taskName = apiFile
        if (apiTaskName && !targetTaskName) targetTaskName = apiTaskName
        
        yamlFromCaseApi = String.valueOf(payload.yaml ?: "")
        caseExecutionContext = payload.context instanceof Map ? payload.context : [:]
        if (!sonicSuiteId) sonicSuiteId = String.valueOf(caseExecutionContext.sonic_suite_id ?: "").trim()
        if (!sonicSuiteName) sonicSuiteName = String.valueOf(caseExecutionContext.sonic_suite_name ?: "").trim()
        if (sonicSuiteExpectedTotal <= 0) {
            sonicSuiteExpectedTotal = parsePositiveInt(String.valueOf(caseExecutionContext.suite_expected_total ?: ""))
        }
        androidStepHandler.log.sendStepLog(2, "Case解析", "case_id=${midsceneCaseId}\n${taskModule}/${taskName}\n${targetTaskName ?: '整文件'}")
    } catch (Exception e) {
        // case_id 解析失败时,检查是否有内置的 module/file 可用
        if (!taskModule || !taskName) {
            def msg = "case_id 解析失败:${midsceneCaseId},${e.message}。请检查:\n1. 服务器上是否存在对应的 YAML 文件\n2. TASK_DIR 配置是否正确\n3. YAML 文件是否包含 baseline.case_id 注释"
            androidStepHandler.log.sendStepLog(2, "Case解析失败", msg)
            postResultToTaskManager("failed", 1, "", msg, "", midsceneCaseId, "", "")
            throw new RuntimeException(msg)
        } else {
            // 有内置 module/file,继续执行
            androidStepHandler.log.sendStepLog(2, "Case解析警告", "case_id=${midsceneCaseId}查询失败,使用内置配置:${taskModule}/${taskName}")
        }
    }
}

localTaskDir = "D:\\sonic\\midscene-tasks\\${taskModule}"
localTaskPath = "${localTaskDir}\\${taskName}"
new File(localTaskDir).mkdirs()

def downloadResult = [code: 0, stdout: "", stderr: ""]
def yamlFile = new File(localTaskPath)
def yamlContent = ""

if (yamlFromCaseApi) {
    yamlFile.setText(yamlFromCaseApi, "UTF-8")
    yamlContent = yamlFromCaseApi
} else {
    // 下载 YAML，注意中文模块名和文件名需要 URL 编码
    def encodedModule = encodeUrlPart(taskModule)
    def encodedTaskName = encodeUrlPart(taskName)
    def downloadUrl = "${taskServer}/tasks/${encodedModule}/${encodedTaskName}"
    def downloadCmd = "curl --connect-timeout 5 --max-time 30 -L -f -o \"${localTaskPath}\" \"${downloadUrl}\""
    downloadResult = runCmd(downloadCmd, 35)
    yamlContent = yamlFile.exists() ? yamlFile.getText("UTF-8") : ""
}

if (downloadResult.code != 0 || !yamlContent || !yamlContent.contains("tasks:")) {
    def caseName = taskName.replace(".yaml", "")
    def msg = "YAML文件下载失败，请检查服务器文件是否存在：${taskModule}/${taskName}"
    androidStepHandler.log.sendStepLog(2, "YAML下载失败", "${msg}\n${downloadResult.stderr}")
    postResultToTaskManager("failed", 1, downloadResult.stdout, msg, "", caseName, "", "")
    throw new RuntimeException("YAML下载失败，终止执行")
}

androidStepHandler.log.sendStepLog(2, "Task下载", "已下载：${taskModule}/${taskName}")

if (yamlContent.contains("deviceId:")) {
    yamlContent = yamlContent.replaceAll(/deviceId:\s*["']?[^"'\n\r]+["']?/, "deviceId: \"" + deviceSerial + "\"")
} else {
    yamlContent = yamlContent.replace("android:", "android:\n  deviceId: \"" + deviceSerial + "\"")
}
yamlFile.setText(yamlContent, "UTF-8")
androidStepHandler.log.sendStepLog(2, "设备注入", "当前设备：${deviceSerial}")
androidStepHandler.log.sendStepLog(2, "YAML脚本（本次实际执行内容）", yamlContent)
def appPackage = parseAppPackage(yamlContent)
currentAppPackage = appPackage ?: String.valueOf(caseExecutionContext.app_package ?: "").trim()
if (appPackage) {
    try {
        def resetElapsedMs = resetForegroundApp(appPackage)
        androidStepHandler.log.sendStepLog(2, "APP前置重置", "已清理后台并强停应用，启动动作交由 YAML 执行：${appPackage}\n耗时：${resetElapsedMs}ms")
    } catch (Exception e) {
        def msg = "执行前设备清理失败：${e.message ?: String.valueOf(e)}"
        androidStepHandler.log.sendStepLog(2, "APP前置重置失败", msg)
        postResultToTaskManager("failed", 1, "", msg, "", targetTaskName ?: taskName.replace(".yaml", ""), "", "")
        throw new RuntimeException(msg)
    }
} else {
    androidStepHandler.log.sendStepLog(2, "APP前置重置", "未从 YAML 中识别到 launch/terminate 包名，飞书通知将按未识别应用处理")
}
taskNames = targetTaskName ? [targetTaskName] : parseTaskNames(yamlContent)
totalTaskCount = taskNames.size()
completedTaskCount = 0
currentTaskIndex = 0
currentTaskName = totalTaskCount > 0 ? taskNames[0] : ""
def preflight = preflightCheck()
androidStepHandler.log.sendStepLog(2, "Midscene环境自检", (preflight.details + preflight.issues.collect { "问题：" + it }).join("\n"))
if (!preflight.ok) {
    def caseName = targetTaskName ?: taskName.replace(".yaml", "")
    def msg = "执行前环境自检失败：" + preflight.issues.join("；")
    def preflightLog = (preflight.details + preflight.issues.collect { "问题：" + it }).join("\n")
    postResultToTaskManager("failed", 1, preflightLog, msg, "", caseName, "", "")
    throw new RuntimeException(msg)
}
postProgressToTaskManager(3, currentTaskName, currentTaskIndex, completedTaskCount, totalTaskCount, "", "准备执行")

def pb = configureMidsceneProcess(
    new ProcessBuilder("cmd", "/c", "midscene \"${localTaskPath}\""),
    midsceneReplanningCycleLimit
)
def proc = pb.start()

def outputBuffer = new StringBuilder()
def lastProgressAt = 0L
def progressOutputTail = ""
def readerThread = Thread.start {
    try {
        proc.inputStream.withReader("UTF-8") { reader ->
            reader.eachLine { line ->
                outputBuffer.append(line).append("\n")
                progressOutputTail = compactLog(progressOutputTail + line + "\n", 2000)
                def trimmed = line.trim()
                taskNames.eachWithIndex { name, idx ->
                    if (name && trimmed.contains(name)) {
                        currentTaskIndex = idx
                        currentTaskName = name
                        if ((trimmed.contains("✔") || trimmed.contains("✓")) && idx + 1 > completedTaskCount) {
                            completedTaskCount = idx + 1
                            currentTaskName = completedTaskCount < totalTaskCount ? taskNames[completedTaskCount] : name
                        }
                    }
                }
                def nowMs = System.currentTimeMillis()
                if (nowMs - lastProgressAt > 15000 || trimmed.contains("✔") || trimmed.contains("✓") || trimmed.contains("✘")) {
                    lastProgressAt = nowMs
                    def progress = 5
                    if (totalTaskCount > 0) {
                        progress = Math.min(95, 5 + Math.round((completedTaskCount * 90.0) / totalTaskCount)).intValue()
                    }
                    postProgressToTaskManager(progress, currentTaskName, currentTaskIndex, completedTaskCount, totalTaskCount, progressOutputTail, trimmed.take(160))
                }
            }
        }
    } catch (Exception e) {}
}

proc.waitFor()
readerThread.join(5000)
def output = outputBuffer.toString()

def exitCode = proc.exitValue()
def statusText = exitCode == 0 ? "✅ 成功" : "❌ 失败"
def statusValue = exitCode == 0 ? "success" : "failed"
def outputSummary = compactLog(output, exitCode == 0 ? 1000 : 4000)
androidStepHandler.log.sendStepLog(2, "Midscene执行结果", "退出码：${exitCode} 状态：${statusText}\n${outputSummary}")

// 提取失败原因
def errorDetail = ""
if (exitCode != 0) {
    def lines = output.readLines()

    def replanLine = lines.find { it.contains("Replanned") && it.contains("exceeding the limit") }
    if (replanLine) errorDetail = "重规划次数超限，请检查页面状态"

    if (!errorDetail) {
        def yamlErrLine = lines.find { it.contains('property "tasks" is required') || it.contains("failed to load") }
        if (yamlErrLine) errorDetail = "YAML格式错误或加载失败，请检查文件内容"
    }

    if (!errorDetail) {
        def unknownFlowLine = lines.find { it.contains("unknown flowItem in yaml") }
        if (unknownFlowLine) {
            def match = unknownFlowLine =~ /unknown flowItem in yaml:\s*(.*)/
            if (match) errorDetail = "YAML语法错误: " + match[0][1].replaceAll(/[{}"\\]/, "").trim()
        }
    }

    if (!errorDetail) {
        def failedLocateIdx = lines.findIndexOf { it.contains("failed to locate element:") }
        if (failedLocateIdx >= 0 && failedLocateIdx + 1 < lines.size()) {
            errorDetail = "找不到元素: " + lines[failedLocateIdx + 1].trim()
        }
    }

    if (!errorDetail) {
        def iCanSeeLine = lines.find { it.trim().startsWith("I can see") }
        if (iCanSeeLine) errorDetail = iCanSeeLine.trim()
    }

    if (!errorDetail) {
        def assertLine = lines.find { it.contains("Assertion failed") }
        def taskFailedLine = lines.find { it.contains("Task failed:") }
        def failedLine = lines.find { it.contains("Failed to continue:") }
        def reasonLine = lines.find { it.trim().startsWith("Reason:") }
        if (assertLine) errorDetail += assertLine.trim()
        if (taskFailedLine) errorDetail += (errorDetail ? " | " : "") + taskFailedLine.trim()
        if (failedLine) errorDetail += (errorDetail ? " | " : "") + failedLine.trim()
        if (reasonLine) errorDetail += (errorDetail ? " | " : "") + reasonLine.trim()
    }

    if (!errorDetail) {
        def errorIdx = lines.findIndexOf { it.trim().startsWith("error:") }
        if (errorIdx >= 0 && errorIdx + 1 < lines.size()) {
            errorDetail = lines[errorIdx + 1].trim()
        }
    }

    if (!errorDetail) errorDetail = "请查看详细报告"
    errorDetail = repairUtf8ReadAsGbk(errorDetail)
}

// 从日志提取真实报告路径
def realReportPath = null
def logLines = output.readLines()
def reportFinalizedLine = logLines.find { it.contains("report finalized:") }
if (reportFinalizedLine) {
    realReportPath = reportFinalizedLine.replaceAll(/.*report finalized:\s*/, "").trim()
} else {
    def reportGeneratedLine = logLines.find { it.contains("report generated:") }
    if (reportGeneratedLine) {
        realReportPath = reportGeneratedLine.replaceAll(/.*report generated:\s*/, "").trim()
    }
}
if (!realReportPath) {
    def reportDir = new File("D:\\sonic\\midscene_run\\report")
    def latestReport = reportDir.listFiles()?.sort { it.lastModified() }?.reverse()?.find { it.name.endsWith(".html") }
    if (latestReport) realReportPath = latestReport.absolutePath
}

def dateStr = formatBridgeTime("yyyy-MM-dd_HH-mm-ss")
def caseName = targetTaskName ?: taskName.replace(".yaml", "")
def reportUrl = ""
def reportUploadError = ""
def localReportPath = ""
def reportFileForBackgroundUpload = null
def reportFileNameForBackgroundUpload = ""

if (realReportPath) {
    def originalFile = new File(realReportPath)
    def safeFileName = "${taskName.replace('.yaml', '')}-${dateStr}.html"
    def safeFile = new File("D:\\sonic\\midscene_run\\report\\${safeFileName}")
    if (originalFile.absolutePath != safeFile.absolutePath) {
        if (!originalFile.renameTo(safeFile)) {
            safeFile = originalFile
        }
    }
    localReportPath = safeFile.absolutePath
    reportFileForBackgroundUpload = safeFile
    reportFileNameForBackgroundUpload = safeFileName
    reportUrl = "${String.valueOf(taskServer).replaceAll('/+$', '')}/reports/${encodeUrlPart(safeFileName)}"
}

def archiveStartedAt = System.currentTimeMillis()
postResultToTaskManager(statusValue, exitCode, output, errorDetail, reportUrl, caseName, reportUploadError, localReportPath)
def archiveElapsedMs = System.currentTimeMillis() - archiveStartedAt

if (reportFileForBackgroundUpload) {
    androidStepHandler.log.sendStepLog(2, "Midscene报告", "报告已生成，正在后台上传；不阻塞下一条用例\n预留地址：${reportUrl}")
    def asyncReportFile = reportFileForBackgroundUpload
    def asyncReportName = reportFileNameForBackgroundUpload
    def uploadThread = new Thread({
        def uploadResult = uploadReportFile(asyncReportFile, asyncReportName)
        postReportAttachmentToTaskManager(
            uploadResult.url ?: "",
            uploadResult.error ?: "",
            uploadResult.localPath ?: asyncReportFile.absolutePath
        )
    } as Runnable, "midscene-report-upload-${jobId}")
    uploadThread.setDaemon(true)
    uploadThread.start()
}

if (exitCode != 0 && currentAppPackage) {
    def lowerOutput = output.toLowerCase()
    def uiStateFailure = [
        "replanned", "waitfor timeout", "failed to locate element",
        "failed to continue", "assertion failed", "task failed"
    ].any { lowerOutput.contains(it) }
    def recoveryUnavailable = [
        "ai call error", "failed to call ai model service", "request was aborted",
        "request aborted", "request was cancelled", "request was canceled",
        "request cancelled", "request canceled", "service unavailable",
        "too many requests", "rate limit", "modelservingerror",
        "unknown flowitem", "unknown flowitem in yaml", "failed to load",
        "property \"tasks\" is required"
    ].any { lowerOutput.contains(it) }
    if (uiStateFailure && !recoveryUnavailable) {
        androidStepHandler.log.sendStepLog(2, "失败后状态恢复中", "检测到 UI 状态型失败，调用有界 AI 恢复；仅清理当前状态，不修改基线 YAML，最长 180 秒")
        def recoveryResult = runFailureRecovery(currentAppPackage)
        androidStepHandler.log.sendStepLog(
            recoveryResult.ok ? 2 : 1,
            recoveryResult.ok ? "失败后状态已隔离" : "失败后状态恢复未完成",
            recoveryResult.detail
        )
    } else {
        def reason = recoveryUnavailable ? "模型/脚本环境当前不可用于 AI 恢复" : "失败不属于可确认的 UI 状态型故障"
        androidStepHandler.log.sendStepLog(2, "跳过失败后 AI 恢复", "${reason}；仍会强停应用，避免继续占用前台")
        runCmd("${adbPath} -s ${deviceSerial} shell am force-stop ${currentAppPackage}", 8)
    }
}

try {
    def restoreStartedAt = System.currentTimeMillis()
    def adbCmd = "${adbPath} -s ${deviceSerial} shell am force-stop io.appium.uiautomator2.server"
    def uiaStopResult = runCmd(adbCmd, 10)
    if (uiaStopResult.code != 0) {
        throw new RuntimeException("停止旧 UIAutomator2 服务失败：${compactLog(uiaStopResult.stderr ?: uiaStopResult.stdout, 300)}")
    }
    Thread.sleep(300)
    androidStepHandler.log.sendStepLog(2, "Sonic Driver恢复中", "Midscene 已结束，正在恢复 Sonic 设备控制；最长等待 60 秒")
    def restoreResult = restoreSonicDriverWithTimeout(60)
    if (!restoreResult.ok) {
        throw new RuntimeException(restoreResult.error)
    }
    def restoreElapsedMs = System.currentTimeMillis() - restoreStartedAt
    androidStepHandler.log.sendStepLog(2, "Sonic Driver已恢复", "设备连接正常\n衔接耗时：结果归档 ${archiveElapsedMs}ms，Driver 恢复 ${restoreElapsedMs}ms；HTML 报告后台上传不阻塞下一条用例")
} catch (Exception restoreError) {
    if (exitCode == 0) {
        def msg = "Midscene 用例已执行完成，但 Sonic Driver 恢复失败：${restoreError.message ?: String.valueOf(restoreError)}"
        androidStepHandler.log.sendStepLog(2, "Sonic Driver恢复失败", msg)
        postResultToTaskManager("failed", 1, output, msg, reportUrl, caseName, reportUploadError, localReportPath)
    }
    throw restoreError
} finally {
    if (exitCode != 0) {
        throw new RuntimeException("Midscene 执行失败，退出码：${exitCode}")
    }
}
