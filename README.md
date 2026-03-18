# Claude Usage Monitor

A lightweight Windows overlay that shows your **Claude AI usage** directly inside the Claude desktop app — no browser extensions needed.

![Claude Usage Monitor overlay showing usage bars inside Claude desktop app](.github/preview.png)

## Features

- Live usage bars for Session (5h), Weekly (7d), Sonnet, Opus, Cowork, and Extra (over-limit) quotas
- Compact view with color-coded bars (blue → amber → red as you approach limits)- Click to expand for detailed view with reset timers
- Auto-hides when Claude is not in focus
- Runs on startup automatically
- Zero performance impact — polls the API every 60 seconds

## Install

### Option 1 — One-liner (PowerShell)

Open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/wowy296/claude-usage-monitor/main/install.ps1 | iex
```

This installs the monitor, adds it to startup, and launches it immediately.

### Option 2 — Standalone EXE

Download `ClaudeUsageMonitor.exe` from the [latest release](https://github.com/wowy296/claude-usage-monitor/releases/latest) and run it. No Python required.

To add it to startup, press `Win+R`, type `shell:startup`, and drop a shortcut to the EXE there.

### Option 3 — Run from source

```bash
pip install requests pywin32
python claude_usage_monitor.py
```

## Requirements

- Windows 10/11
- Claude desktop app installed and logged in
- Python 3.9+ (only needed for Option 3 / one-liner install)

## How it works

Reads your OAuth token from `~/.claude/.credentials.json` (written by the Claude desktop app) and calls the Anthropic usage API every 60 seconds. Nothing is sent anywhere — it's read-only.

## Usage

- **Compact view** — always visible in the bottom-left of the Claude sidebar
- **Click** the overlay to expand detailed stats with reset timers
- **Click again** to collapse

The overlay hides automatically when you switch to another app.

## License

MIT
