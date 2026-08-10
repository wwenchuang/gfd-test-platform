param(
  [ValidateSet("install", "remove", "restart", "status", "logs", "test")]
  [string]$Action = "install"
)

$ErrorActionPreference = "Stop"

# Local fixed config. Keep this file on the Windows runner machine only.
$ServiceName = "MidsceneWindowsRunner"
$Workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskServer = "http://101.34.197.12:8088"
$RunnerId = "win-runner-01"
$RunnerToken = "d1Z9CKVE9mzac2WYISJr3JIfZ2AB_agAjD9dHiNo25I"
$AdbBin = "C:\Program Files\platform-tools\adb.exe"
$MidsceneBin = "C:\Users\gfd\AppData\Roaming\npm\midscene.cmd"

$Nssm = Join-Path $Workspace "nssm.exe"
$RunnerScript = Join-Path $Workspace "windows-midscene-runner.py"
$LogsDir = Join-Path $Workspace "logs"
$StdoutLog = Join-Path $LogsDir "windows-runner.out.log"
$StderrLog = Join-Path $LogsDir "windows-runner.err.log"

function Resolve-Python {
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cmd = Get-Command python3.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "python.exe not found. Install Python or add Python to PATH."
}

function Invoke-Nssm {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
  & $Nssm @Args
  if ($LASTEXITCODE -ne 0) {
    throw "nssm failed: $($Args -join ' ')"
  }
}

function Test-ServiceInstalled {
  $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  return $null -ne $service
}

function Wait-ServiceStatus {
  param([string]$Expected, [int]$TimeoutSeconds = 45)
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status.ToString() -eq $Expected) {
      return $true
    }
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)
  return $false
}

function Start-RunnerService {
  if (Test-ServiceInstalled) {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status.ToString() -ne "Stopped") {
      & $Nssm stop $ServiceName 2>$null | Out-Null
      Wait-ServiceStatus "Stopped" 20 | Out-Null
    }
  }

  & $Nssm start $ServiceName
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0 -and -not (Wait-ServiceStatus "Running" 45)) {
    Write-Host "Service failed to reach Running state."
    & $Nssm status $ServiceName 2>$null
    if (Test-Path $StderrLog) {
      Write-Host "stderr tail:"
      Get-Content $StderrLog -Tail 80
    }
    throw "nssm failed: start $ServiceName"
  }

  if (-not (Wait-ServiceStatus "Running" 45)) {
    Write-Host "Service did not reach Running state."
    & $Nssm status $ServiceName 2>$null
    if (Test-Path $StderrLog) {
      Write-Host "stderr tail:"
      Get-Content $StderrLog -Tail 80
    }
    throw "service start timeout: $ServiceName"
  }
}

function Invoke-HealthCheck {
  Write-Host "Checking server health: $TaskServer/api/health"
  $health = Invoke-WebRequest -Uri "$TaskServer/api/health" -TimeoutSec 8 -UseBasicParsing
  Write-Host "Health HTTP $($health.StatusCode)"

  Write-Host "Checking runner token: $RunnerId"
  $headers = @{ "x-token" = $RunnerToken }
  $next = Invoke-WebRequest -Uri "$TaskServer/api/runner/jobs/next?runner_id=$RunnerId" -Headers $headers -TimeoutSec 8 -UseBasicParsing
  Write-Host "Runner endpoint HTTP $($next.StatusCode)"
}

if (-not (Test-Path $Nssm)) {
  throw "nssm.exe not found: $Nssm"
}
if (-not (Test-Path $RunnerScript)) {
  throw "windows-midscene-runner.py not found: $RunnerScript"
}

if ($Action -eq "test") {
  Invoke-HealthCheck
  exit 0
}

if ($Action -eq "logs") {
  Write-Host "stdout: $StdoutLog"
  Write-Host "stderr: $StderrLog"
  if (Test-Path $StdoutLog) { Get-Content $StdoutLog -Tail 120 }
  if (Test-Path $StderrLog) { Get-Content $StderrLog -Tail 120 }
  exit 0
}

if ($Action -eq "status") {
  if (Test-ServiceInstalled) {
    & $Nssm status $ServiceName
  } else {
    Write-Host "Service is not installed: $ServiceName"
  }
  exit 0
}

if ($Action -eq "remove") {
  if (Test-ServiceInstalled) {
    & $Nssm stop $ServiceName 2>$null | Out-Null
    & $Nssm remove $ServiceName confirm
    Write-Host "Removed service: $ServiceName"
  } else {
    Write-Host "Service does not exist: $ServiceName"
  }
  exit 0
}

if ($Action -eq "restart") {
  Start-RunnerService
  Write-Host "Restarted service: $ServiceName"
  exit 0
}

$Python = Resolve-Python
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

Invoke-HealthCheck

if (-not (Test-ServiceInstalled)) {
  Invoke-Nssm install $ServiceName $Python
}

Invoke-Nssm set $ServiceName AppDirectory $Workspace
Invoke-Nssm set $ServiceName AppParameters "-u `"$RunnerScript`""
Invoke-Nssm set $ServiceName AppStdout $StdoutLog
Invoke-Nssm set $ServiceName AppStderr $StderrLog
Invoke-Nssm set $ServiceName AppRotateFiles 1
Invoke-Nssm set $ServiceName AppRotateOnline 1
Invoke-Nssm set $ServiceName AppRotateBytes 10485760
Invoke-Nssm set $ServiceName AppThrottle 15000
Invoke-Nssm set $ServiceName AppExit Default Restart
Invoke-Nssm set $ServiceName Start SERVICE_AUTO_START
Invoke-Nssm set $ServiceName DisplayName "Midscene Windows Runner"
Invoke-Nssm set $ServiceName Description "Midscene Task Platform Windows Runner"

Invoke-Nssm set $ServiceName AppEnvironmentExtra `
  "TASK_SERVER=$TaskServer" `
  "RUNNER_ID=$RunnerId" `
  "MIDSCENE_RUNNER_TOKEN=$RunnerToken" `
  "MIDSCENE_RUNNER_WORKSPACE=$Workspace" `
  "ADB_BIN=$AdbBin" `
  "MIDSCENE_BIN=$MidsceneBin" `
  "PYTHONUNBUFFERED=1"

& sc.exe failure $ServiceName reset= 60 actions= restart/5000/restart/10000/restart/30000 | Out-Null
& sc.exe failureflag $ServiceName 1 | Out-Null

Start-RunnerService

Write-Host "Installed and started service: $ServiceName"
Write-Host "Workspace: $Workspace"
Write-Host "Logs: $LogsDir"
