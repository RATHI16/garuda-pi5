# GARUDA BOOTH DEMO - one click on the Windows laptop (started by GARUDA_DEMO.bat)
# Connects WiFi GarudaBench, waits for the Pi, opens the camera page, runs the checks on the Pi
# and starts the bridge (dry run or live), then shows the bridge window.
# The Pi itself already starts hotspot + Ethernet + camera at boot (booth_setup_once.sh).
param([string]$Mode = "")

$Pi = "garuda@10.43.0.1"
$IP = "10.43.0.1"
$Page = "http://10.43.0.1:8098/demo"
$SshOpts = @("-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=5")

function Stop-WithMessage($text) {
    Write-Host ""
    Write-Host "  FAIL  $text" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  GARUDA BOOTH DEMO" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
if (-not $Mode) {
    Write-Host "  1 = DRY RUN   (checks everything, sends NOTHING to the FC)"
    Write-Host "  2 = LIVE      (bridge sends setpoints; pilot switches OFFBOARD on the RC)"
    Write-Host "  3 = STOP      (stop the bridge)"
    Write-Host "  4 = WATCH     (show the running bridge window again)"
    $c = Read-Host "Choose 1-4"
    $Mode = @{ "1" = "dry"; "2" = "live"; "3" = "stop"; "4" = "watch" }[$c]
    if (-not $Mode) { Stop-WithMessage "no valid choice" }
}
if ($Mode -eq "live") {
    Write-Host ""
    Write-Host "  LIVE: props-off checks done? tether/net? pilot on RC with mode switch + kill switch?" -ForegroundColor Yellow
    $ok = Read-Host "  Type YES to start LIVE"
    if ($ok -ne "YES") { Stop-WithMessage "LIVE not confirmed" }
}

Write-Host ""
Write-Host "== 1. WiFi GarudaBench"
$wifi = (netsh wlan show interfaces) -join "`n"
if ($wifi -match "SSID\s+:\s+GarudaBench") {
    Write-Host "  OK   already connected"
} else {
    netsh wlan connect name="GarudaBench" | Out-Null
    Write-Host "  connecting..."
    Start-Sleep -Seconds 8
}

Write-Host "== 2. Pi reachable"
$up = $false
for ($i = 1; $i -le 30; $i++) {
    if (Test-Connection -ComputerName $IP -Count 1 -Quiet) { $up = $true; break }
    Write-Host "  waiting for the Pi ($i/30)..."
    Start-Sleep -Seconds 2
}
if (-not $up) {
    Stop-WithMessage "Pi $IP not reachable. Pi powered for 2 min? VPN (AnyConnect) OFF? WiFi = GarudaBench?"
}
Write-Host "  OK   $IP answers"

if ($Mode -eq "dry" -or $Mode -eq "live") {
    Write-Host "== 3. Camera page"
    Start-Process $Page
    Write-Host "  opened $Page  (Placement = On drone, click the person)"
}

Write-Host "== 4. Pi: checks + bridge"
if ($Mode -eq "watch") {
    & ssh -t @SshOpts $Pi "tmux attach -t bridge || echo 'no bridge running'"
} else {
    & ssh -t @SshOpts $Pi "bash ~/booth/booth_fly.sh $Mode"
}
Write-Host ""
Write-Host "Bridge window closed on the laptop. If it was LIVE it keeps running on the Pi." -ForegroundColor Yellow
Write-Host "To stop it: run GARUDA_DEMO.bat and choose 3."
Read-Host "Press Enter to close"
