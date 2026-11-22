# ONE TIME: lets the laptop log in to the Pi without typing the password (SSH key).
# Run: right-click > Run with PowerShell   (laptop on GarudaBench, VPN off)
$Pi = "garuda@10.43.0.1"
$key = "$HOME\.ssh\id_ed25519"
if (-not (Test-Path "$HOME\.ssh")) { New-Item -ItemType Directory -Path "$HOME\.ssh" | Out-Null }
if (-not (Test-Path $key)) {
    Write-Host "Creating a key. When asked for a passphrase just press Enter twice." -ForegroundColor Yellow
    ssh-keygen -t ed25519 -f $key
}
Write-Host "Copying the key to the Pi. Type the Pi password ONE last time:" -ForegroundColor Yellow
Get-Content "$key.pub" | ssh -o StrictHostKeyChecking=accept-new $Pi "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
Write-Host "Testing (must print KEY OK without a password):"
ssh -o BatchMode=yes $Pi "echo KEY OK"
Read-Host "Press Enter to close"
