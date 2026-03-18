"""
Claude Usage Monitor - Overlay that displays inside the Claude desktop app.
Shows usage percentage bars as a widget embedded in the Claude window.
"""

import ctypes
import ctypes.wintypes
import json
import os
import threading
import time
import tkinter as tk
from datetime import datetime, timezone

import requests
import win32gui
import win32con
import win32api


# ─── Config ───────────────────────────────────────────────────────────────────

CREDENTIALS_PATH = os.path.expanduser("~/.claude/.credentials.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
POLL_INTERVAL = 60  # seconds between API calls
POSITION_INTERVAL = 200  # ms between position updates

# Theme - matches Claude dark UI
BG = "#1a1a1a"
BG_BAR = "#333333"
FG = "#e0e0e0"
FG_DIM = "#777777"
BAR_BLUE = "#4a9eff"
BAR_AMBER = "#ffb400"
BAR_RED = "#ff4444"
BORDER_COLOR = "#333333"

QUOTA_LABELS = {
    "five_hour": "Session",
    "seven_day": "Weekly",
    "seven_day_sonnet": "Sonnet",
    "seven_day_opus": "Opus",
    "seven_day_cowork": "Cowork",
}


# ─── API ──────────────────────────────────────────────────────────────────────

def read_token():
    try:
        with open(CREDENTIALS_PATH, "r") as f:
            creds = json.load(f)
        return creds["claudeAiOauth"]["accessToken"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None


def api_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
    }


def fetch_usage(token):
    try:
        r = requests.get(USAGE_URL, headers=api_headers(token), timeout=10)
        if r.status_code == 401:
            return "Auth expired"
        if r.status_code == 429:
            return "Rate limited"
        if r.status_code >= 500:
            return "Server error"
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return str(e)


def fetch_profile(token):
    try:
        r = requests.get(PROFILE_URL, headers=api_headers(token), timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def time_until(iso_str):
    if not iso_str:
        return ""
    try:
        target = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        total_secs = int((target - now).total_seconds())
        if total_secs <= 0:
            return "now"
        hours, remainder = divmod(total_secs, 3600)
        minutes = remainder // 60
        if hours > 0:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return ""


def bar_color(pct):
    if pct >= 90:
        return BAR_RED
    if pct >= 70:
        return BAR_AMBER
    return BAR_BLUE


def get_active_quotas(usage_data):
    if not isinstance(usage_data, dict):
        return []
    entries = []
    for key, label in QUOTA_LABELS.items():
        val = usage_data.get(key)
        if val and isinstance(val, dict) and val.get("utilization") is not None:
            entries.append((key, label, val["utilization"], val.get("resets_at")))
    return entries


# ─── Window finder ────────────────────────────────────────────────────────────

def find_claude_window():
    """Find the main Claude desktop app window."""
    result = []

    def enum_callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        # Claude desktop app window title
        if title == "Claude":
            result.append(hwnd)

    win32gui.EnumWindows(enum_callback, None)
    return result[0] if result else None


def get_window_rect(hwnd):
    """Get window position and size."""
    try:
        rect = win32gui.GetWindowRect(hwnd)
        return rect  # (left, top, right, bottom)
    except Exception:
        return None


def is_window_maximized(hwnd):
    try:
        placement = win32gui.GetWindowPlacement(hwnd)
        return placement[1] == win32con.SW_SHOWMAXIMIZED
    except Exception:
        return False


# ─── Overlay Widget ───────────────────────────────────────────────────────────

class UsageOverlay:
    def __init__(self):
        self.root = None
        self.usage_data = None
        self.profile_data = None
        self.running = True
        self.bar_canvases = {}
        self.labels = {}
        self.claude_hwnd = None
        self.expanded = False

    def start(self):
        self.root = tk.Tk()
        self.root.title("Claude Usage")
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)  # borderless
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)

        # Make click-through for the transparent parts
        # But keep it clickable for the widget itself
        self.root.wm_attributes("-transparentcolor", "")

        # Build the compact widget
        self._build_ui()

        # Start background threads
        threading.Thread(target=self._poll_loop, daemon=True).start()

        # Start position tracking
        self.root.after(100, self._track_position)

        # Bind click to toggle expanded view
        self._bind_click_all(self.root)

        self.root.mainloop()

    def _bind_click_all(self, widget):
        widget.bind("<Button-1>", self._toggle_expand)
        for child in widget.winfo_children():
            self._bind_click_all(child)

    def _build_ui(self):
        # Main container with subtle border
        self.container = tk.Frame(self.root, bg=BG, highlightbackground=BORDER_COLOR,
                                  highlightthickness=1, padx=10, pady=7)
        self.container.pack(fill="both", expand=True)

        # Compact view: stacked rows
        self.compact_frame = tk.Frame(self.container, bg=BG)
        self.compact_frame.pack(fill="x")

        # Title label
        self.title_label = tk.Label(self.compact_frame, text="Usage", font=("Segoe UI", 10, "bold"),
                                     fg=FG_DIM, bg=BG)
        self.title_label.pack(anchor="w", pady=(0, 2))

        # Compact bar placeholders
        self.compact_bars_frame = tk.Frame(self.compact_frame, bg=BG)
        self.compact_bars_frame.pack(fill="x")

        # Expanded view (hidden by default)
        self.expanded_frame = tk.Frame(self.container, bg=BG)

    def _update_compact(self):
        """Update the compact single-line view."""
        for w in self.compact_bars_frame.winfo_children():
            w.destroy()

        entries = get_active_quotas(self.usage_data)

        # Add extra usage if available
        if isinstance(self.usage_data, dict):
            extra = self.usage_data.get("extra_usage")
            if extra and isinstance(extra, dict) and extra.get("is_enabled"):
                used = extra.get("used_credits", 0)
                limit = extra.get("monthly_limit", 0)
                if limit > 0:
                    pct = used / limit * 100
                    entries.append(("extra_usage", "EX", pct, None))

        if not entries:
            tk.Label(self.compact_bars_frame, text="--", font=("Segoe UI", 8),
                     fg=FG_DIM, bg=BG).pack(side="left")
            return

        short_labels = {
            "five_hour": "5h", "seven_day": "7d", "seven_day_sonnet": "SN",
            "seven_day_opus": "OP", "seven_day_cowork": "CW", "extra_usage": "m",
        }

        for key, label, pct, _ in entries:
            f = tk.Frame(self.compact_bars_frame, bg=BG)
            f.pack(fill="x", pady=1)

            short = short_labels.get(key, key)
            tk.Label(f, text=short, font=("Segoe UI", 9), fg=FG_DIM, bg=BG, width=3,
                     anchor="w").pack(side="left")

            # Mini bar
            bar_w, bar_h = 50, 10
            c = tk.Canvas(f, width=bar_w, height=bar_h, bg=BG_BAR,
                          highlightthickness=0, bd=0)
            c.pack(side="left", padx=2)
            fill = int(bar_w * min(pct, 100) / 100)
            if fill > 0:
                c.create_rectangle(0, 0, fill, bar_h, fill=bar_color(pct), outline="")

            color = bar_color(pct)
            tk.Label(f, text=f"{pct:.0f}%", font=("Segoe UI", 9, "bold"),
                     fg=color, bg=BG).pack(side="left")

    def _update_expanded(self):
        """Update the expanded detailed view."""
        for w in self.expanded_frame.winfo_children():
            w.destroy()

        # Account info
        if self.profile_data:
            acct = self.profile_data.get("account", {})
            org = self.profile_data.get("organization", {})
            name = acct.get("display_name", "")
            plan = org.get("organization_type", "").replace("_", " ").title()
            if name:
                tk.Label(self.expanded_frame, text=f"{name} - {plan}",
                         font=("Segoe UI", 11), fg=BAR_BLUE, bg=BG).pack(anchor="w", pady=(5, 3))

        entries = get_active_quotas(self.usage_data)
        if isinstance(self.usage_data, str):
            tk.Label(self.expanded_frame, text=self.usage_data,
                     font=("Segoe UI", 9), fg=BAR_RED, bg=BG).pack(anchor="w")
            return

        for key, label, pct, resets_at in entries:
            row = tk.Frame(self.expanded_frame, bg=BG)
            row.pack(fill="x", pady=3)

            # Label row
            top = tk.Frame(row, bg=BG)
            top.pack(fill="x")
            tk.Label(top, text=label, font=("Segoe UI", 11), fg=FG, bg=BG).pack(side="left")

            reset_str = time_until(resets_at)
            right_text = f"{pct:.0f}%"
            if reset_str:
                right_text += f"  ({reset_str})"
            tk.Label(top, text=right_text, font=("Segoe UI", 11, "bold"),
                     fg=bar_color(pct), bg=BG).pack(side="right")

            # Bar
            bar_w, bar_h = 250, 13
            c = tk.Canvas(row, width=bar_w, height=bar_h, bg=BG_BAR,
                          highlightthickness=0, bd=0)
            c.pack(fill="x", pady=1)
            fill = int(bar_w * min(pct, 100) / 100)
            if fill > 0:
                c.create_rectangle(0, 0, fill, bar_h, fill=bar_color(pct), outline="")

        # Extra usage (over-limit spending)
        if isinstance(self.usage_data, dict):
            extra = self.usage_data.get("extra_usage")
            if extra and isinstance(extra, dict) and extra.get("is_enabled"):
                used = extra.get("used_credits", 0)
                limit = extra.get("monthly_limit", 0)
                tk.Frame(self.expanded_frame, bg="#444", height=1).pack(fill="x", pady=5)

                # Label row
                row = tk.Frame(self.expanded_frame, bg=BG)
                row.pack(fill="x", pady=3)

                top = tk.Frame(row, bg=BG)
                top.pack(fill="x")
                tk.Label(top, text="Extra (Over-limit)", font=("Segoe UI", 11), fg=FG, bg=BG).pack(side="left")

                pct_used = (used / limit * 100) if limit > 0 else 0
                tk.Label(top, text=f"${used:.2f}/${limit:.0f}  ({pct_used:.0f}%)",
                         font=("Segoe UI", 11, "bold"),
                         fg=bar_color(pct_used), bg=BG).pack(side="right")

                # Bar
                bar_w, bar_h = 250, 13
                c = tk.Canvas(row, width=bar_w, height=bar_h, bg=BG_BAR,
                              highlightthickness=0, bd=0)
                c.pack(fill="x", pady=1)
                fill = int(bar_w * min(pct_used, 100) / 100)
                if fill > 0:
                    c.create_rectangle(0, 0, fill, bar_h, fill=bar_color(pct_used), outline="")

    def _toggle_expand(self, event=None):
        self.expanded = not self.expanded
        if self.expanded:
            self._update_expanded()
            self.expanded_frame.pack(fill="x", after=self.compact_frame)
        else:
            self.expanded_frame.pack_forget()
        # Re-bind clicks on new widgets
        self._bind_click_all(self.root)
        self.root.update_idletasks()

    def _track_position(self):
        """Keep the overlay positioned inside the Claude window.
        Hide when Claude is not the foreground window."""
        if not self.running:
            return

        hwnd = find_claude_window()
        fg = win32gui.GetForegroundWindow()

        if hwnd and not win32gui.IsIconic(hwnd):
            self.claude_hwnd = hwnd

            # Show only when the foreground window is exactly the Claude
            # window or our own overlay window
            fg_title = win32gui.GetWindowText(fg)
            should_show = (fg == hwnd) or (fg_title == "Claude Usage")

            if should_show:
                rect = get_window_rect(hwnd)
                if rect:
                    left, top, right, bottom = rect
                    self.root.update_idletasks()
                    w = self.root.winfo_reqwidth()
                    h = self.root.winfo_reqheight()

                    sidebar_width = 270
                    x = left + (sidebar_width - w) // 2
                    y = bottom - h - 98

                    self.root.geometry(f"+{x}+{y}")
                    self.root.attributes("-alpha", 0.95)
                    self.root.lift()
            else:
                self.root.geometry("+9999+9999")
                self.root.attributes("-alpha", 0)
        else:
            self.root.geometry("+9999+9999")
            self.root.attributes("-alpha", 0)

        self.root.after(POSITION_INTERVAL, self._track_position)

    def _poll_loop(self):
        """Background API polling."""
        while self.running:
            token = read_token()
            if token:
                self.usage_data = fetch_usage(token)
                if self.profile_data is None:
                    self.profile_data = fetch_profile(token)
                # Update UI from main thread
                if self.root:
                    self.root.after(0, self._refresh_ui)
            else:
                self.usage_data = "No credentials"

            for _ in range(POLL_INTERVAL):
                if not self.running:
                    break
                time.sleep(1)

    def _refresh_ui(self):
        self._update_compact()
        if self.expanded:
            self._update_expanded()
        self._bind_click_all(self.root)


if __name__ == "__main__":
    overlay = UsageOverlay()
    overlay.start()
