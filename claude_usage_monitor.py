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

# Theme - matches Claude desktop dark sidebar
BG = "#292929"
BG_TRANSPARENT = "#010101"  # color keyed out for rounded corners
BG_BAR = "#3d3d3d"
FG = "#e3e3e3"
FG_DIM = "#8a8a8a"
BAR_BLUE = "#6b9fff"
BAR_AMBER = "#e5a633"
BAR_RED = "#e55555"
CORNER_RADIUS = 10

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
        self.root.configure(bg=BG_TRANSPARENT)
        self.root.overrideredirect(True)  # borderless
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.wm_attributes("-transparentcolor", BG_TRANSPARENT)

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

    def _round_rect(self, canvas, x1, y1, x2, y2, r, **kwargs):
        """Draw a rounded rectangle on a canvas."""
        points = [
            x1 + r, y1, x2 - r, y1,
            x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1,
        ]
        return canvas.create_polygon(points, smooth=True, **kwargs)

    def _build_ui(self):
        # Use a Canvas to draw the rounded background
        self.bg_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0,
                                    bg=BG_TRANSPARENT)
        self.bg_canvas.pack(fill="both", expand=True)

        # Inner frame placed on the canvas
        self.container = tk.Frame(self.bg_canvas, bg=BG, padx=12, pady=8)
        self.container_window = self.bg_canvas.create_window(
            CORNER_RADIUS, CORNER_RADIUS, anchor="nw", window=self.container)

        # After layout, draw the rounded rect background
        self.root.after(50, self._draw_bg)

        # Compact view: stacked rows
        self.compact_frame = tk.Frame(self.container, bg=BG)
        self.compact_frame.pack(fill="x")

        # Title label - matches Claude sidebar font style
        self.title_label = tk.Label(self.compact_frame, text="Usage",
                                     font=("Segoe UI", 9),
                                     fg=FG_DIM, bg=BG)
        self.title_label.pack(anchor="w", pady=(0, 4))

        # Compact bar placeholders
        self.compact_bars_frame = tk.Frame(self.compact_frame, bg=BG)
        self.compact_bars_frame.pack(fill="x")

        # Expanded view (hidden by default)
        self.expanded_frame = tk.Frame(self.container, bg=BG)

    def _draw_bg(self):
        """Redraw the rounded rectangle background."""
        self.root.update_idletasks()
        w = self.container.winfo_reqwidth() + CORNER_RADIUS * 2
        h = self.container.winfo_reqheight() + CORNER_RADIUS * 2
        self.bg_canvas.config(width=w, height=h)
        self.bg_canvas.delete("bg")
        self._round_rect(self.bg_canvas, 0, 0, w, h, CORNER_RADIUS,
                          fill=BG, outline="", tags="bg")
        self.bg_canvas.tag_lower("bg")

    def _draw_rounded_bar(self, canvas, x, y, w, h, fill_pct, color):
        """Draw a rounded progress bar on canvas."""
        r = h // 2
        # Background track
        self._round_rect(canvas, x, y, x + w, y + h, r, fill=BG_BAR, outline="")
        # Fill
        fill_w = int(w * min(fill_pct, 100) / 100)
        if fill_w > r * 2:
            self._round_rect(canvas, x, y, x + fill_w, y + h, r, fill=color, outline="")
        elif fill_w > 0:
            self._round_rect(canvas, x, y, x + max(fill_w, r * 2), y + h, r, fill=color, outline="")

    def _update_compact(self):
        """Update the compact stacked view."""
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
            f.pack(fill="x", pady=2)

            short = short_labels.get(key, key)
            tk.Label(f, text=short, font=("Segoe UI", 8), fg=FG_DIM, bg=BG, width=3,
                     anchor="w").pack(side="left")

            # Rounded mini bar
            bar_w, bar_h = 62, 8
            c = tk.Canvas(f, width=bar_w, height=bar_h, bg=BG,
                          highlightthickness=0, bd=0)
            c.pack(side="left", padx=(2, 4))
            self._draw_rounded_bar(c, 0, 0, bar_w, bar_h, pct, bar_color(pct))

            color = bar_color(pct)
            tk.Label(f, text=f"{pct:.0f}%", font=("Segoe UI", 8),
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
                tk.Label(self.expanded_frame, text=f"{name} \u00b7 {plan}",
                         font=("Segoe UI", 9), fg=FG_DIM, bg=BG).pack(anchor="w", pady=(5, 3))

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
            tk.Label(top, text=label, font=("Segoe UI", 9), fg=FG, bg=BG).pack(side="left")

            reset_str = time_until(resets_at)
            right_text = f"{pct:.0f}%"
            if reset_str:
                right_text += f"  \u00b7 {reset_str}"
            tk.Label(top, text=right_text, font=("Segoe UI", 9),
                     fg=bar_color(pct), bg=BG).pack(side="right")

            # Rounded bar
            bar_w, bar_h = 220, 8
            c = tk.Canvas(row, width=bar_w, height=bar_h, bg=BG,
                          highlightthickness=0, bd=0)
            c.pack(fill="x", pady=(2, 0))
            self._draw_rounded_bar(c, 0, 0, bar_w, bar_h, pct, bar_color(pct))

        # Extra usage (over-limit spending)
        if isinstance(self.usage_data, dict):
            extra = self.usage_data.get("extra_usage")
            if extra and isinstance(extra, dict) and extra.get("is_enabled"):
                used = extra.get("used_credits", 0)
                limit = extra.get("monthly_limit", 0)

                # Subtle separator
                sep = tk.Canvas(self.expanded_frame, height=1, bg=BG,
                                highlightthickness=0, bd=0)
                sep.pack(fill="x", pady=6)
                sep.create_line(0, 0, 300, 0, fill="#3d3d3d")

                row = tk.Frame(self.expanded_frame, bg=BG)
                row.pack(fill="x", pady=3)

                top = tk.Frame(row, bg=BG)
                top.pack(fill="x")
                tk.Label(top, text="Monthly extra", font=("Segoe UI", 9), fg=FG, bg=BG).pack(side="left")

                pct_used = (used / limit * 100) if limit > 0 else 0
                tk.Label(top, text=f"${used:.2f} / ${limit:.0f}",
                         font=("Segoe UI", 9),
                         fg=bar_color(pct_used), bg=BG).pack(side="right")

                bar_w, bar_h = 220, 8
                c = tk.Canvas(row, width=bar_w, height=bar_h, bg=BG,
                              highlightthickness=0, bd=0)
                c.pack(fill="x", pady=(2, 0))
                self._draw_rounded_bar(c, 0, 0, bar_w, bar_h, pct_used, bar_color(pct_used))

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
        self.root.after(50, self._draw_bg)

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
        self.root.after(50, self._draw_bg)


def kill_existing_overlays():
    """Kill any already-running overlay instances."""
    import win32process
    my_pid = os.getpid()
    pids = set()

    def cb(hwnd, _):
        if win32gui.GetWindowText(hwnd) == "Claude Usage":
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid != my_pid:
                pids.add(pid)

    win32gui.EnumWindows(cb, None)
    for pid in pids:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


if __name__ == "__main__":
    kill_existing_overlays()
    overlay = UsageOverlay()
    overlay.start()
