import json
import os
import time
import threading as _threading
import pyaudio
import keyboard
import psutil
import win32gui
import win32process
import win32con
import pygetwindow as gw
import win32api
import subprocess
import ctypes
import sys
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from vosk import Model, KaldiRecognizer
import user_config

sys.stdout.reconfigure(encoding='utf-8')
# Make the process DPI-aware so all Win32 coordinate calls use physical pixels
# consistently. Without this, SystemParametersInfoW, GetWindowRect and
# SetWindowPos use virtualized logical pixels while DwmGetWindowAttribute
# always returns physical pixels — mixing the two causes incorrect positioning.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()        # fallback

# ── CONFIG ───────────────────────────────────────────────────────────────
# APPS and PROC_NAMES are loaded from the user's local config file so that
# each machine keeps its own entries and updates never wipe them.
_cfg        = user_config.load()
MODEL_PATH  = _cfg.get("MODEL_PATH", r"C:\VoiceCommands\vosk-model-small-en-us-0.15")
APPS        = _cfg.get("APPS", {})
PROC_NAMES  = _cfg.get("PROC_NAMES", {})

SAMPLE_RATE = 16000
FRAMES_PER_BUFFER = 1024
COOLDOWN = 1.5
CONFIDENCE_THRESHOLD = 0.65

# Populated from config in run() — use _cw(key) to get current trigger word
_COMMAND_WORDS: dict[str, str] = user_config.DEFAULT_COMMAND_WORDS.copy()
_VOLUME_STEPS:  dict[str, int]  = user_config.DEFAULT_VOLUME_STEPS.copy()

def _cw(key: str) -> str:
    """Return the configured trigger word for an action key."""
    return _COMMAND_WORDS.get(key, user_config.DEFAULT_COMMAND_WORDS.get(key, key))

# Window classes to exclude per app.
# IME / Default IME are Windows system Input Method Editor windows —
# every thread creates one; they are never the app's main UI.
# vguiPopupWindow IS Steam's UI class so must not be excluded.
EXCLUDE_CLASSES: dict[str, set[str]] = {
    "files": {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"},
    "steam": {"IME"},
}

# System window classes that should never be treated as an app's main window.
# Applied globally across all apps in addition to EXCLUDE_CLASSES.
GLOBAL_EXCLUDE_CLASSES: set[str] = {"IME", "Default IME"}

# Apps that may hide to the system tray.
INCLUDE_HIDDEN: set[str] = {"steam", "discord"}

# When an app has many windows, prefer the one whose title contains this.
PREFERRED_TITLE: dict[str, str] = {
    "discord": "discord",
}

# "open X" runs this instead of window detection.
OPEN_OVERRIDE = {
    "steam": lambda: os.startfile("steam://open/main"),
}

# Special launch for new instances.
LAUNCH_OVERRIDE = {
    "files":   lambda: subprocess.Popen(["explorer.exe"]),
    "command": lambda: subprocess.Popen(
        ["cmd.exe"], creationflags=subprocess.CREATE_NEW_CONSOLE
    ),
}

# Custom close commands.
CLOSE_OVERRIDE = {
    "steam": lambda: subprocess.Popen([APPS["steam"], "-shutdown"]),
}

# Apps where minimise works by focusing the window via OPEN_OVERRIDE and then
# sending Win+Down. Used when the visible window can't be found by process/class
# (e.g. Steam's CEF-based UI lives in a window our search can't reliably locate).
MINIMISE_VIA_FOCUS: set[str] = {"steam"}


# Snap positions — used to build the grammar and validate commands.
# Positions are calculated at runtime from actual work area dimensions
# using SetWindowPos directly, bypassing Snap Assist entirely.
SNAP_POSITIONS: set[str] = {
    "left", "right", "fullscreen",
    "top left", "top right",
    "bottom left", "bottom right",
}
# ─────────────────────────────────────────────────────────────────────────

# ── VOLUME CONTROL ───────────────────────────────────────────────────────
def _get_volume_interface() -> POINTER(IAudioEndpointVolume):
    device = AudioUtilities.GetSpeakers()
    com_device = getattr(device, "_dev", device)
    interface = com_device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def change_volume(direction: str, step_word: str) -> None:
    pct = _VOLUME_STEPS.get(step_word)
    if pct is None:
        print(f"  Unknown step '{step_word}'")
        return
    delta = pct / 100.0
    if direction == "down":
        delta = -delta
    vol = _get_volume_interface()
    current = vol.GetMasterVolumeLevelScalar()
    new_level = max(0.0, min(1.0, current + delta))
    vol.SetMasterVolumeLevelScalar(new_level, None)
    arrow = "🔊▲" if delta > 0 else "🔉▼"
    print(f"{arrow}  Volume {'up' if delta > 0 else 'down'} {abs(pct)}%"
          f" → {new_level*100:.0f}%")


# REMOVE this:
def set_mute(muted: bool) -> None:
    vol = _get_volume_interface()
    vol.SetMute(int(muted), None)
    print("🔇  Muted!" if muted else "🔊  Unmuted!")

# REPLACE with:
def toggle_mute() -> None:
    vol = _get_volume_interface()
    new_state = not vol.GetMute()
    vol.SetMute(int(new_state), None)
    print("🔇  Muted!" if new_state else "🔊  Unmuted!")


def _get_work_area() -> tuple[int, int, int, int]:
    """Return (x, y, w, h) of the primary monitor's work area (excludes taskbar)."""
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _get_frame_offsets(hwnd: int) -> tuple[int, int, int, int]:
    """Return the invisible shadow/frame offsets (left, top, right, bottom).
    Windows includes these in GetWindowRect but they're not visible — we must
    compensate so snap targets have no gaps between them."""
    full    = ctypes.wintypes.RECT()
    visible = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(full))
    # DWMWA_EXTENDED_FRAME_BOUNDS (9) gives the visible rendered bounds
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(visible), ctypes.sizeof(visible)
    )
    return (
        visible.left  - full.left,
        visible.top   - full.top,
        full.right    - visible.right,
        full.bottom   - visible.bottom,
    )


def _set_corner_pref(hwnd: int, square: bool) -> None:
    """Tell DWM to use square (True) or default rounded (False) corners.
    Windows only does this automatically for its own snap — we must ask."""
    # DWMWA_WINDOW_CORNER_PREFERENCE = 33
    # DWMWCP_DEFAULT = 0  (rounded on Win11), DWMWCP_DONOTROUND = 1
    val = ctypes.c_int(1 if square else 0)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, 33, ctypes.byref(val), ctypes.sizeof(val)
    )


def _apply_snap(hwnd: int, position: str) -> None:
    """Move and resize hwnd to a snap position.
    Compensates for invisible frame shadow so windows tile flush with no gaps,
    and squares the corners to match native snap behaviour."""
    wx, wy, ww, wh = _get_work_area()
    hw, hh = ww // 2, wh // 2

    coords: dict[str, tuple[int, int, int, int]] = {
        "left":         (wx,       wy,       hw,  wh),
        "right":        (wx + hw,  wy,       hw,  wh),
        "top left":     (wx,       wy,       hw,  hh),
        "top right":    (wx + hw,  wy,       hw,  hh),
        "bottom left":  (wx,       wy + hh,  hw,  hh),
        "bottom right": (wx + hw,  wy + hh,  hw,  hh),
    }

    # Restore from maximised so SetWindowPos takes effect
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.05)

    if position == "fullscreen":
        _set_corner_pref(hwnd, square=False)    # restore rounded corners
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    elif position in coords:
        x, y, w, h = coords[position]
        fl, ft, fr, fb = _get_frame_offsets(hwnd)
        _set_corner_pref(hwnd, square=True)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP,
            x - fl, y - ft, w + fl + fr, h + ft + fb,
            win32con.SWP_SHOWWINDOW | win32con.SWP_NOZORDER,
        )


def snap_app(app_name: str | None, position: str) -> None:
    """Snap an app (or the current foreground window) to a screen position."""
    if position not in SNAP_POSITIONS:
        print(f"  Unknown position '{position}'")
        return

    if app_name is None:
        # Snap whatever is currently in focus
        hwnd = win32gui.GetForegroundWindow()
    elif app_name not in APPS:
        print(f"  Don't know '{app_name}'")
        return
    elif app_name in OPEN_OVERRIDE:
        # Bring the app forward via its URI/override, then grab the foreground hwnd
        OPEN_OVERRIDE[app_name]()
        time.sleep(0.6)
        hwnd = win32gui.GetForegroundWindow()
    else:
        hwnds = _windows_for_app(app_name)
        if not hwnds:
            print(f"  Couldn't find a window for '{app_name}'")
            return
        hwnd = _pick_window(hwnds, app_name)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        _set_foreground(hwnd)
        time.sleep(0.15)

    _apply_snap(hwnd, position)
    label = app_name or "current window"
    print(f"🗗  Snapped {label} to {position}!")
# ─────────────────────────────────────────────────────────────────────────

# ── PROCESS CACHE ────────────────────────────────────────────────────────
_pid_name_cache: dict[int, str] = {}
_cache_built_at: float = 0.0
_CACHE_TTL = 5.0


def _refresh_pid_cache() -> None:
    global _pid_name_cache, _cache_built_at
    _pid_name_cache = {
        p.pid: p.info["name"].lower()
        for p in psutil.process_iter(["name"])
        if p.info["name"]
    }
    _cache_built_at = time.monotonic()


def _proc_matches(cached_name: str, pattern: str) -> bool:
    """Match a process name against a pattern.
    Patterns ending in * are prefix matches; otherwise exact match."""
    pattern = pattern.lower()
    if pattern.endswith("*"):
        return cached_name.startswith(pattern[:-1])
    return cached_name == pattern


def _is_taskbar_window(hwnd: int) -> bool:
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if ex_style & win32con.WS_EX_TOOLWINDOW:
        return False
    if ex_style & win32con.WS_EX_APPWINDOW:
        return True
    return win32gui.GetWindow(hwnd, win32con.GW_OWNER) == 0


def _window_score(hwnd: int) -> int:
    visible = win32gui.IsWindowVisible(hwnd)
    iconic  = win32gui.IsIconic(hwnd)
    taskbar = _is_taskbar_window(hwnd)
    if visible and not iconic and taskbar:
        return 3
    if visible and not iconic:
        return 2
    if taskbar:
        return 1
    return 0


def _windows_for_app(app_name: str) -> list[int]:
    pattern = PROC_NAMES.get(app_name, "")
    if not pattern:
        return []
    if time.monotonic() - _cache_built_at > _CACHE_TTL:
        _refresh_pid_cache()

    excluded     = EXCLUDE_CLASSES.get(app_name, set()) | GLOBAL_EXCLUDE_CLASSES
    allow_hidden = app_name in INCLUDE_HIDDEN
    found = []

    def _cb(hwnd, _):
        if not allow_hidden and not win32gui.IsWindowVisible(hwnd):
            return
        if not win32gui.GetWindowText(hwnd):
            return
        if win32gui.GetClassName(hwnd) in excluded:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc_name = _pid_name_cache.get(pid, "")
            if proc_name and _proc_matches(proc_name, pattern):
                found.append(hwnd)
        except OSError:
            pass

    win32gui.EnumWindows(_cb, None)

    # UWP fallback: some apps (Settings, Calculator, etc.) run their UI inside
    # ApplicationFrameHost.exe — if we found nothing by process, try matching by
    # window title containing the app name.
    if not found:
        title_hint = app_name.lower()

        def _cb_title(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).lower()
            if not title:
                return
            cls = win32gui.GetClassName(hwnd)
            if cls in GLOBAL_EXCLUDE_CLASSES:
                return
            if title_hint in title or title in title_hint:
                found.append(hwnd)

        win32gui.EnumWindows(_cb_title, None)

    found.sort(key=_window_score, reverse=True)
    return found


def _pick_window(hwnds: list[int], app_name: str) -> int:
    pref = PREFERRED_TITLE.get(app_name, "").lower()
    if pref:
        for h in hwnds:
            if win32gui.IsWindowVisible(h) and pref in win32gui.GetWindowText(h).lower():
                return h
        for h in hwnds:
            if pref in win32gui.GetWindowText(h).lower():
                return h
    return hwnds[0]
# ─────────────────────────────────────────────────────────────────────────


def _set_foreground(hwnd: int) -> None:
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        print(f"  Warning: couldn't bring window to foreground ({e})")


def _launch(app_name: str) -> None:
    if app_name in LAUNCH_OVERRIDE:
        LAUNCH_OVERRIDE[app_name]()
    elif app_name in APPS:
        os.startfile(APPS[app_name])
    else:
        print(f"  Don't know how to open '{app_name}'")
        return
    print(f"▶  Opened new {app_name}!")


def open_or_focus(app_name: str) -> None:
    if app_name not in APPS:
        print(f"  Don't know how to open '{app_name}'")
        return
    if app_name in OPEN_OVERRIDE:
        OPEN_OVERRIDE[app_name]()
        print(f"▶  Opened/focused {app_name}!")
        return
    hwnds = _windows_for_app(app_name)
    if hwnds:
        hwnd = _pick_window(hwnds, app_name)
        win32gui.ShowWindow(hwnd, 9)
        _set_foreground(hwnd)
        print(f"▶  Focused {app_name}!")
        return
    _launch(app_name)


def open_and_snap(app_name: str, position: str) -> None:
    """Open/focus an app and immediately snap it to a position.
    If the app isn't running yet, waits for it to launch before snapping."""
    if app_name not in APPS:
        print(f"  Don't know how to open '{app_name}'")
        return
    if position not in SNAP_POSITIONS:
        print(f"  Unknown position '{position}'")
        return

    needs_launch = (
        app_name not in OPEN_OVERRIDE
        and not _windows_for_app(app_name)
    )

    if needs_launch:
        _launch(app_name)
        print(f"  Waiting for {app_name} to start...")
        time.sleep(2.5)

    # snap_app handles focus + snap for both OPEN_OVERRIDE and normal apps
    snap_app(app_name, position)


def minimise_app(app_name: str | None = None) -> None:
    if app_name:
        if app_name not in APPS:
            print(f"  Don't know '{app_name}'")
            return

        # For apps whose visible window can't be found by process/class,
        # bring the window forward via its open handler then send Win+Down.
        if app_name in MINIMISE_VIA_FOCUS:
            if app_name in OPEN_OVERRIDE:
                OPEN_OVERRIDE[app_name]()
                time.sleep(0.6)     # let the window reach the foreground
            keyboard.send("windows+down")
            print(f"🗕  Minimised {app_name}!")
            return

        hwnds = _windows_for_app(app_name)
        if hwnds:
            hwnd = _pick_window(hwnds, app_name)
            win32gui.ShowWindow(hwnd, 6)
            print(f"🗕  Minimised {app_name}!")
        else:
            print(f"  Couldn't find a window for '{app_name}'")
    else:
        win = gw.getActiveWindow()
        if win:
            win.minimize()
            print("🗕  Minimised current window!")


# ── Pending-close state ───────────────────────────────────────────────────
_pending_close: dict | None = None   # {app, hwnds, timer}
_pending_cancel = _threading.Event()


def _commit_close(app_name: str, hwnds: list[int]) -> None:
    """Actually close the windows — called after the undo window expires."""
    global _pending_close
    _pending_close = None
    if app_name in CLOSE_OVERRIDE:
        CLOSE_OVERRIDE[app_name]()
    else:
        for hwnd in hwnds:
            try:
                win32gui.PostMessage(hwnd, 0x0010, 0, 0)
            except Exception:
                pass
    print(f"✕  Closed {app_name}!")


def close_app(app_name: str) -> None:
    global _pending_close
    if app_name not in APPS:
        print(f"  Don't know '{app_name}'")
        return

    # Cancel any existing pending close before starting a new one
    if _pending_close is not None:
        _pending_cancel.set()

    delay = user_config.get_close_delay()

    # For CLOSE_OVERRIDE apps there's no window to minimise first
    if app_name in CLOSE_OVERRIDE:
        hwnds = []
    else:
        hwnds = _windows_for_app(app_name)
        if not hwnds:
            print(f"  Couldn't find a window for '{app_name}'")
            return
        # Minimise so the user can see something happened
        for hwnd in hwnds:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

    _pending_cancel.clear()
    _pending_close = {"app": app_name, "hwnds": hwnds}

    print(f"⏳  Closing {app_name} in {delay}s — say 'undo' to cancel!")

    def _timer():
        global _pending_close
        cancelled = _pending_cancel.wait(timeout=delay)
        if not cancelled and _pending_close and _pending_close["app"] == app_name:
            _commit_close(app_name, hwnds)

    t = _threading.Thread(target=_timer, daemon=True)
    t.start()
    _pending_close["timer"] = t


def undo_close() -> None:
    global _pending_close
    if _pending_close is None:
        print("  Nothing to undo.")
        return
    app_name = _pending_close["app"]
    hwnds    = _pending_close["hwnds"]
    _pending_cancel.set()
    _pending_close = None
    # Restore minimised windows
    for hwnd in hwnds:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            _set_foreground(hwnd)
        except Exception:
            pass
    print(f"↩  Cancelled close — {app_name} restored!")


# ── DIAGNOSTIC ───────────────────────────────────────────────────────────
def print_diagnostic() -> None:
    print("\n── Window diagnostic ───────────────────────────────────────")
    _refresh_pid_cache()
    all_proc_names = set(_pid_name_cache.values())

    for app_name, pattern in PROC_NAMES.items():
        running = any(_proc_matches(n, pattern) for n in all_proc_names)
        if not running:
            print(f"  {app_name:12s}  ✗  '{pattern}' not running")
            similar = sorted({n for n in all_proc_names if app_name in n})
            if similar:
                print(f"  {'':12s}     → similar process running: {', '.join(similar)}")
                print(f"  {'':12s}     → update PROC_NAMES[\"{app_name}\"] to match")
            continue

        hwnds = _windows_for_app(app_name)
        if not hwnds:
            print(f"  {app_name:12s}  ✗  process running but no windows found")
        else:
            best  = _pick_window(hwnds, app_name)
            title = win32gui.GetWindowText(best)
            cls   = win32gui.GetClassName(best)
            score = _window_score(best)
            print(f"  {app_name:12s}  ✓  {len(hwnds)} win — "
                  f"best: '{title}' [{cls}] score={score}")

    print("────────────────────────────────────────────────────────────\n")
# ─────────────────────────────────────────────────────────────────────────


def build_grammar() -> str:
    words = [
        _cw("skip"), _cw("previous"), _cw("rewind"),
        _cw("play_pause"), "play",          # always keep "play" as alias
        "open", "minimise", "close",
        _cw("minimise_all"), _cw("open_all"),
        "maximise",
        _cw("mute"),
        _cw("diagnose"),
        _cw("copy"), _cw("paste"),
        _cw("save"),
        _cw("enter"),
        _cw("undo"),
        _cw("stop_engine"),
        _cw("restart_engine"),
        "[unk]",
    ]
    for app_name in APPS:
        words.append(f"open {app_name}")
        words.append(f"open new {app_name}")
        words.append(f"minimise {app_name}")
        words.append(f"maximise {app_name}")
        words.append(f"close {app_name}")
        for pos in SNAP_POSITIONS:
            words.append(f"move {app_name} {pos}")
            words.append(f"open {app_name} {pos}")
    for pos in SNAP_POSITIONS:
        words.append(f"move {pos}")
    for step in _VOLUME_STEPS:
        words.append(f"volume up {step}")
        words.append(f"volume down {step}")
    # Deduplicate while preserving order
    seen = set(); out = []
    for w in words:
        if w not in seen:
            seen.add(w); out.append(w)
    return json.dumps(out)


def average_confidence(result: dict) -> float:
    words = result.get("result", [])
    if not words:
        return 0.0
    return sum(w.get("conf", 0.0) for w in words) / len(words)


def _parse_app(words: list[str], start: int) -> tuple[str | None, list[str]]:
    """Try to match the longest app name beginning at words[start].
    Tries 3-word, 2-word, then 1-word candidates so 'no mans sky' is
    matched before 'no' alone would be.
    Returns (app_name, remaining_words) or (None, words[start:])."""
    for length in range(min(3, len(words) - start), 0, -1):
        candidate = " ".join(words[start : start + length])
        if candidate in APPS:
            return candidate, words[start + length :]
    return None, words[start:]


last_command = None
last_command_time = 0


def handle_command(text: str) -> None:
    global last_command, last_command_time
    if not text:
        return
    words = text.split()
    now = time.time()
    if text == last_command and (now - last_command_time) < COOLDOWN:
        return
    last_command = text
    last_command_time = now

    print(f"Command: '{text}'")

    if text == _cw("skip"):
        print("⏭  Skipping track!")
        keyboard.send("next track")
    elif text == _cw("previous"):
        print("⏮  Previous track!")
        keyboard.send("previous track")
    elif text == _cw("rewind"):
        print("🔁  Restarting track!")
        keyboard.send("previous track")
        time.sleep(0.05)
        keyboard.send("previous track")
    elif text in (_cw("play_pause"), "play"):
        print("⏸  Toggling playback!")
        keyboard.send("play/pause media")
    elif text == _cw("copy"):
        print("📋  Copy!")
        keyboard.send("ctrl+c")
    elif text == _cw("paste"):
        print("📋  Paste!")
        keyboard.send("ctrl+v")
    elif text == _cw("save"):
        print("💾  Save!")
        keyboard.send("ctrl+s")
    elif text == _cw("enter"):
        print("↵  Enter!")
        keyboard.send("enter")
    elif text == _cw("undo"):
        undo_close()
    elif text == _cw("stop_engine"):
        print("🛑  Closing voice commands!")
        _stop_event.set()
    elif text == _cw("restart_engine"):
        print("🔄  Restarting voice commands!")
        _restart_requested = True
        _stop_event.set()
    elif words[0] == "volume" and len(words) == 3 and words[1] in ("up", "down"):
        change_volume(words[1], words[2])
    elif text == _cw("mute"):
        toggle_mute()
    elif text == _cw("diagnose"):
        print_diagnostic()
    elif words[0] == "move":
        if len(words) < 2:
            print("  Say 'move' followed by an app name and/or position")
        else:
            app, rest = _parse_app(words, 1)
            if app:
                snap_app(app, " ".join(rest))
            else:
                snap_app(None, " ".join(words[1:]))
    elif words[0] == "open":
        if len(words) == 1:
            print("  Say 'open' followed by an app name")
        elif words[1] == "all":
            keyboard.send("windows+d")
            print("🗖  Showing all windows!")
        elif words[1] == "new":
            if len(words) > 2:
                app, _ = _parse_app(words, 2)
                _launch(app) if app else print("  Say 'open new' followed by an app name")
            else:
                print("  Say 'open new' followed by an app name")
        else:
            app, rest = _parse_app(words, 1)
            if app:
                position = " ".join(rest)
                if position in SNAP_POSITIONS:
                    open_and_snap(app, position)
                else:
                    open_or_focus(app)
            else:
                print(f"  Don't know '{' '.join(words[1:])}'")
    elif words[0] == "minimise":
        if len(words) > 1:
            if words[1] == "all":
                keyboard.send("windows+d")
                print("🗕  Minimised all windows!")
            else:
                app, _ = _parse_app(words, 1)
                minimise_app(app) if app else minimise_app()
        else:
            minimise_app()
    elif words[0] == "maximise":
        app, _ = _parse_app(words, 1) if len(words) > 1 else (None, [])
        snap_app(app, "fullscreen")
    elif words[0] == "close":
        if len(words) > 1:
            app, _ = _parse_app(words, 1)
            close_app(app) if app else print("  Say 'close' followed by an app name")
        else:
            print("  Say 'close' followed by an app name")


# ── ENGINE ───────────────────────────────────────────────────────────────
_stop_event     = _threading.Event()
_restart_requested = False


def run(stop_event: _threading.Event | None = None) -> bool:
    """Start the voice engine.  Returns True if a restart was requested."""
    global APPS, PROC_NAMES, MODEL_PATH, _stop_event, _restart_requested
    global CONFIDENCE_THRESHOLD, COOLDOWN, _COMMAND_WORDS, _VOLUME_STEPS
    _cfg                 = user_config.load()
    MODEL_PATH           = user_config.get_model_path()
    APPS                 = _cfg.get("APPS", APPS)
    PROC_NAMES           = _cfg.get("PROC_NAMES", PROC_NAMES)
    CONFIDENCE_THRESHOLD = user_config.get_confidence_threshold()
    COOLDOWN             = user_config.get_cooldown()
    _COMMAND_WORDS       = user_config.get_command_words()
    _VOLUME_STEPS        = user_config.get_volume_steps()

    if stop_event is None:
        stop_event = _threading.Event()
    _stop_event        = stop_event
    _restart_requested = False

    print("Loading model...")
    model = Model(MODEL_PATH)
    rec   = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetGrammar(build_grammar())
    rec.SetWords(True)

    print_diagnostic()

    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAMES_PER_BUFFER,
    )
    stream.start_stream()

    print("Listening...")
    print(f"Confidence threshold: {CONFIDENCE_THRESHOLD:.0%}")
    print("Say 'diagnose' at any time to recheck running apps.\n")

    try:
        while not stop_event.is_set():
            data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                text   = result.get("text", "").strip().lower()
                if not text:
                    continue
                conf = average_confidence(result)
                if conf >= CONFIDENCE_THRESHOLD:
                    handle_command(text)
                else:
                    print(f"💤  Low confidence ({conf:.0%}): '{text}' — ignored")
            else:
                partial = json.loads(rec.PartialResult())
                text    = partial.get("partial", "").strip().lower()
                if text:
                    print(f"👂  Hearing: '{text}'")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    return _restart_requested


if __name__ == "__main__":
    while run():          # loop handles "restart voice commands"
        print("Restarting...\n")