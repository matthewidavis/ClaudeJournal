param(
    [int]$Port = 8888,
    [switch]$SkipRun
)

# Headless launcher for ClaudeJournal.
# Starts the web server first (site is pre-rendered, reachable immediately),
# then kicks off the pipeline refresh in the background so it does not block.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$logDir = Join-Path $root "db"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$runLog      = Join-Path $logDir "start-run.log"
$serveLog    = Join-Path $logDir "serve.log"
$serveErrLog = Join-Path $logDir "serve.err.log"
$runErrLog   = Join-Path $logDir "run.err.log"
$launcherLog = Join-Path $logDir "launcher.log"

function Log($msg) {
    "[$(Get-Date -Format s)] $msg" | Out-File -FilePath $launcherLog -Append -Encoding utf8
}

Log "launcher start (port=$Port, SkipRun=$SkipRun)"

# Clean stale pid file from prior crashes so stop + restart work cleanly.
$pidFile = Join-Path $logDir "serve.pid"
if (Test-Path $pidFile) {
    $raw = (Get-Content $pidFile -ErrorAction SilentlyContinue) -join ""
    $pidPart = ($raw -split ":")[0]
    $parsedPid = $pidPart -as [int]
    if ($parsedPid) {
        $alive = Get-Process -Id $parsedPid -ErrorAction SilentlyContinue
        if (-not $alive) {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            Log "removed stale pid file (pid $parsedPid not running)"
        } else {
            Log "serve already running pid $parsedPid - exiting launcher"
            return
        }
    }
}

# Resolve python.
$python = $null
foreach ($cmd in @("python.exe","python3.exe","py.exe")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source; break }
}
if (-not $python) { Log "FATAL: no python on PATH"; return }
Log "using python: $python"

# 1) Start serve (site already rendered from last run; reachable immediately).
"[$(Get-Date -Format s)] claudejournal serve starting on port $Port" | Out-File -FilePath $serveLog -Append -Encoding utf8
$serveProc = Start-Process -FilePath $python `
    -ArgumentList @("-m","claudejournal","serve","--port",$Port) `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $serveLog `
    -RedirectStandardError  $serveErrLog `
    -PassThru
Log "serve launched pid $($serveProc.Id)"

# Give serve a moment to bind the port, then verify.
Start-Sleep -Seconds 3
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Log "serve confirmed listening on port $Port"
} else {
    Log "WARNING: nothing listening on port $Port after 3s - check serve.err.log"
}

# 2) Kick off the pipeline refresh in the background - fully detached.
if (-not $SkipRun) {
    "[$(Get-Date -Format s)] claudejournal run starting (background)" | Out-File -FilePath $runLog -Append -Encoding utf8
    $runProc = Start-Process -FilePath $python `
        -ArgumentList @("-m","claudejournal","run") `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $runLog `
        -RedirectStandardError  $runErrLog `
        -PassThru
    Log "run launched pid $($runProc.Id) (detached)"
}

Log "launcher done"
