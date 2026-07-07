param(
  [ValidateSet("install", "remove", "restart", "status")]
  [string]$Action = "install",
  [string]$ServiceName = "MidsceneWindowsRunner",
  [string]$Workspace = "D:\sonic\midscene_run",
  [string]$TaskServer = "http://101.34.197.12:8088",
  [string]$RunnerId = "win-runner-01",
  [string]$RunnerToken = "",
  [string]$PythonExe = "python",
  [string]$AdbBin = "C:\Program Files\platform-tools\adb.exe",
  [string]$MidsceneBin = "C:\Users\gfd\AppData\Roaming\npm\midscene.cmd",
  [string]$NssmPath = ""
)

$ErrorActionPreference = "Stop"

function Resolve-Nssm {
  param([string]$PathFromArg)
  $candidates = @()
  if ($PathFromArg) { $candidates += $PathFromArg }
  $candidates += [System.IO.Path]::Combine($PSScriptRoot, "nssm.exe")
  $candidates += [System.IO.Path]::Combine($Workspace, "nssm.exe")
  $cmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
  if ($cmd) { $candidates += $cmd.Source }
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return (Resolve-Path $candidate).Path
    }
  }
  throw "nssm.exe not found. Put nssm.exe in $Workspace, next to this script, or pass -NssmPath."
}

function Invoke-Nssm {
  param([string]$Nssm, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
  & $Nssm @Args
  if ($LASTEXITCODE -ne 0) {
    throw "nssm failed: $($Args -join ' ')"
  }
}

$nssm = Resolve-Nssm $NssmPath
$runnerScript = Join-Path $Workspace "windows-midscene-runner.py"
$logsDir = Join-Path $Workspace "logs"

if ($Action -eq "remove") {
  & $nssm stop $ServiceName 2>$null | Out-Null
  & $nssm remove $ServiceName confirm
  Write-Host "Removed service $ServiceName"
  exit 0
}

if ($Action -eq "restart") {
  Invoke-Nssm $nssm restart $ServiceName
  Write-Host "Restarted service $ServiceName"
  exit 0
}

if ($Action -eq "status") {
  & $nssm status $ServiceName
  exit $LASTEXITCODE
}

if (-not (Test-Path $runnerScript)) {
  throw "Runner script not found: $runnerScript"
}
if (-not $RunnerToken) {
  throw "RunnerToken is required. Check server /opt/midscene.env MIDSCENE_RUNNER_TOKEN."
}

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

& $nssm status $ServiceName *> $null
if ($LASTEXITCODE -ne 0) {
  Invoke-Nssm $nssm install $ServiceName $PythonExe $runnerScript
}

Invoke-Nssm $nssm set $ServiceName AppDirectory $Workspace
Invoke-Nssm $nssm set $ServiceName AppStdout (Join-Path $logsDir "windows-runner.out.log")
Invoke-Nssm $nssm set $ServiceName AppStderr (Join-Path $logsDir "windows-runner.err.log")
Invoke-Nssm $nssm set $ServiceName AppRotateFiles 1
Invoke-Nssm $nssm set $ServiceName AppRotateOnline 1
Invoke-Nssm $nssm set $ServiceName AppRotateBytes 10485760
Invoke-Nssm $nssm set $ServiceName AppThrottle 15000
Invoke-Nssm $nssm set $ServiceName AppExit Default Restart
Invoke-Nssm $nssm set $ServiceName Start SERVICE_AUTO_START
Invoke-Nssm $nssm set $ServiceName DisplayName "Midscene Windows Runner"
Invoke-Nssm $nssm set $ServiceName Description "Midscene Task Platform Windows Runner"
Invoke-Nssm $nssm set $ServiceName AppEnvironmentExtra `
  "TASK_SERVER=$TaskServer" `
  "RUNNER_ID=$RunnerId" `
  "MIDSCENE_RUNNER_TOKEN=$RunnerToken" `
  "MIDSCENE_RUNNER_WORKSPACE=$Workspace" `
  "ADB_BIN=$AdbBin" `
  "MIDSCENE_BIN=$MidsceneBin"

Invoke-Nssm $nssm restart $ServiceName
Write-Host "Installed and started service $ServiceName"
Write-Host "Logs: $logsDir"
