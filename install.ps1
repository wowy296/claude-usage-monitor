# Claude Usage Monitor - One-line installer
# Run with: irm https://raw.githubusercontent.com/wowy296/claude-usage-monitor/main/install.ps1 | iex

$ErrorActionPreference = "Stop"
$installDir = "$env:LOCALAPPDATA\ClaudeUsageMonitor"

Write-Host "Installing Claude Usage Monitor..." -ForegroundColor Cyan

# Create install directory
New-Item -ItemType Directory -Force -Path $installDir | Out-Null

# Download the monitor script
$scriptUrl = "https://raw.githubusercontent.com/wowy296/claude-usage-monitor/main/claude_usage_monitor.py"
Invoke-WebRequest -Uri $scriptUrl -OutFile "$installDir\claude_usage_monitor.py"

# Download the batch launcher
$batUrl = "https://raw.githubusercontent.com/wowy296/claude-usage-monitor/main/run-overlay.bat"
Invoke-WebRequest -Uri $batUrl -OutFile "$installDir\run-overlay.bat"

# Update the bat file to use the install dir
$batContent = "@echo off`r`ncd /d `"$installDir`"`r`npythonw.exe claude_usage_monitor.py`r`n"
Set-Content -Path "$installDir\run-overlay.bat" -Value $batContent

# Install Python dependencies
Write-Host "Installing dependencies..." -ForegroundColor Cyan
pip install requests pywin32 --quiet

# Add to Windows startup
$startupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut("$startupDir\Claude Usage Monitor.lnk")
$shortcut.TargetPath = "$installDir\run-overlay.bat"
$shortcut.WorkingDirectory = $installDir
$shortcut.WindowStyle = 0
$shortcut.Save()

# Launch now
Write-Host "Launching Claude Usage Monitor..." -ForegroundColor Green
Start-Process -FilePath "pythonw.exe" -ArgumentList "`"$installDir\claude_usage_monitor.py`"" -WorkingDirectory $installDir

Write-Host ""
Write-Host "Done! Claude Usage Monitor is installed and running." -ForegroundColor Green
Write-Host "It will auto-start with Windows." -ForegroundColor Green
Write-Host "Open Claude desktop app to see your usage overlay." -ForegroundColor Cyan
