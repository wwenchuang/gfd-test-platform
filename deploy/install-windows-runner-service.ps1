param(
  [ValidateSet("install", "remove", "restart", "status", "logs", "test")]
  [string]$Action = "install",
  [string]$ServiceName = "MidsceneWindowsRunner",
  [string]$Workspace = "",
  [string]$TaskServer = "http://101.34.197.12:8088",
  [string]$RunnerId = "win-runner-01",
  [string]$RunnerToken = "",
  [string]$PythonExe = "",
  [string]$AdbBin = "C:\Program Files\platform-tools\adb.exe",
  [string]$MidsceneBin = "C:\Users\gfd\AppData\Roaming\npm\midscene.cmd",
  [string]$NssmPath = ""
)

$ErrorActionPreference = "Stop"

function Resolve-Workspace {
  param([string]$Value)
  if ($Value) {
    return (Resolve-Path $Value).Path
  }
  if ($PSScriptRoot) {
    return (Resolve-Path $PSScriptRoot).Path
  }
  return (Resolve-Path ".").Path
}

function Resolve-CommandPath {
  param([string]$Name)
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return ""
}

function Resolve-Python {
  param([string]$PathFromArg)
  $candidates = @()
  if ($PathFromArg) { $candidates += $PathFromArg }
  $python = Resolve-CommandPath "python.exe"
  if ($python) { $candidates += $python }
  $python3 = Resolve-CommandPath "python3.exe"
  if ($python3) { $candidates += $python3 }
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return (Resolve-Path $candidate).Path
    }
  }
  throw "python.exe not found. Install Python or pass -PythonExe."
}

function Resolve-Nssm {
  param([string]$PathFromArg, [string]$WorkspaceDir)
  $candidates = @()
  if ($PathFromArg) { $candidates += $PathFromArg }
  $candidates += [System.IO.Path]::Combine($WorkspaceDir, "nssm.exe")
  if ($PSScriptRoot) { $candidates += [System.IO.Path]::Combine($PSScriptRoot, "nssm.exe") }
  $nssm = Resolve-CommandPath "nssm.exe"
  if ($nssm) { $candidates += $nssm }
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return (Resolve-Path $candidate).Path
    }
  }
  throw "nssm.exe not found. Put nssm.exe in the runner workspace or pass -NssmPath."
}

function Invoke-Nssm {
  param([string]$Nssm, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
  & $Nssm @Args
  if ($LASTEXITCODE -ne 0) {
    throw "nssm failed: $($Args -join ' ')"
  }
}

function Invoke-HealthCheck {
  param([string]$Server, [string]$Token, [string]$Runner)
  Write-Host "Checking server health: $Server/api/health"
  $health = Invoke-WebRequest -Uri "$Server/api/health" -TimeoutSec 8 -UseBasicParsing
  Write-Host "Health HTTP $($health.StatusCode)"
  if ($Token) {
    Write-Host "Checking runner job endpoint..."
    $headers = @{ "x-token" = $Token }
    $next = Invoke-WebRequest -Uri "$Server/api/runner/jobs/next?runner_id=$Runner" -Headers $headers -TimeoutSec 8 -UseBasicParsing
    Write-Host "Runner endpoint HTTP $($next.StatusCode)"
  }
}

function Test-ServiceInstalled {
  param([string]$Name)
  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  return $null -ne $service
}

function Wait-ServiceStatus {
  param([string]$Name, [string]$Expected, [int]$TimeoutSeconds = 45)
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($service -and $service.Status.ToString() -eq $Expected) {
      return $true
    }
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)
  return $false
}

function Start-RunnerService {
  param([string]$Nssm, [string]$Name, [string]$ErrLog)
  if (Test-ServiceInstalled $Name) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($service -and $service.Status.ToString() -ne "Stopped") {
      & $Nssm stop $Name 2>$null | Out-Null
      Wait-ServiceStatus $Name "Stopped" 20 | Out-Null
    }
  }

  & $Nssm start $Name
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0 -and -not (Wait-ServiceStatus $Name "Running" 45)) {
    Write-Host "Service failed to reach Running state."
    & $Nssm status $Name 2>$null
    if (Test-Path $ErrLog) {
      Write-Host "stderr tail:"
      Get-Content $ErrLog -Tail 80
    }
    throw "nssm failed: start $Name"
  }

  if (-not (Wait-ServiceStatus $Name "Running" 45)) {
    Write-Host "Service did not reach Running state."
    & $Nssm status $Name 2>$null
    if (Test-Path $ErrLog) {
      Write-Host "stderr tail:"
      Get-Content $ErrLog -Tail 80
    }
    throw "service start timeout: $Name"
  }
}

$Workspace = Resolve-Workspace $Workspace
$RunnerToken = if ($RunnerToken) { $RunnerToken } else { $env:MIDSCENE_RUNNER_TOKEN }
$nssm = Resolve-Nssm $NssmPath $Workspace
$python = Resolve-Python $PythonExe
$runnerScript = Join-Path $Workspace "windows-midscene-runner.py"
$logsDir = Join-Path $Workspace "logs"
$stdoutLog = Join-Path $logsDir "windows-runner.out.log"
$stderrLog = Join-Path $logsDir "windows-runner.err.log"

if ($Action -eq "test") {
  Invoke-HealthCheck $TaskServer $RunnerToken $RunnerId
  exit 0
}

if ($Action -eq "logs") {
  Write-Host "stdout: $stdoutLog"
  Write-Host "stderr: $stderrLog"
  if (Test-Path $stdoutLog) {
    Write-Host ""
    Write-Host "----- stdout tail -----"
    Get-Content $stdoutLog -Tail 120 -Encoding UTF8
  }
  if (Test-Path $stderrLog) {
    Write-Host ""
    Write-Host "----- stderr tail -----"
    Get-Content $stderrLog -Tail 160 -Encoding UTF8
  }
  exit 0
}

if ($Action -eq "remove") {
  & $nssm stop $ServiceName 2>$null | Out-Null
  & $nssm remove $ServiceName confirm
  Write-Host "Removed service $ServiceName"
  exit 0
}

if ($Action -eq "restart") {
  Start-RunnerService $nssm $ServiceName $stderrLog
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
  throw "RunnerToken is required. Pass -RunnerToken or set MIDSCENE_RUNNER_TOKEN."
}

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

Invoke-HealthCheck $TaskServer $RunnerToken $RunnerId

if (-not (Test-ServiceInstalled $ServiceName)) {
  Invoke-Nssm $nssm install $ServiceName $python
}

Invoke-Nssm $nssm set $ServiceName AppDirectory $Workspace
Invoke-Nssm $nssm set $ServiceName AppParameters "-u `"$runnerScript`""
Invoke-Nssm $nssm set $ServiceName AppStdout $stdoutLog
Invoke-Nssm $nssm set $ServiceName AppStderr $stderrLog
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
  "MIDSCENE_BIN=$MidsceneBin" `
  "PYTHONUNBUFFERED=1" `
  "PYTHONUTF8=1" `
  "PYTHONIOENCODING=utf-8"

& sc.exe failure $ServiceName reset= 60 actions= restart/5000/restart/10000/restart/30000 | Out-Null
& sc.exe failureflag $ServiceName 1 | Out-Null

Start-RunnerService $nssm $ServiceName $stderrLog
Write-Host "Installed and started service $ServiceName"
Write-Host "Workspace: $Workspace"
Write-Host "Logs: $logsDir"
