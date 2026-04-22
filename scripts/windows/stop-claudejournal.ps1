$ErrorActionPreference = "Continue"
# Repo root is two levels up from scripts/windows/<this file>.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
Set-Location $root

# Primary path: the app's own pid-file-based stop command.
python -m claudejournal stop

# Fallback: kill any stray serve processes that didn't clean up their pid file.
$stray = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "claudejournal\s+serve" }
foreach ($p in $stray) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host "stopped stray serve pid $($p.ProcessId)"
    } catch {}
}
