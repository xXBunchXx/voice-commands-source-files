import json
import os
import pathlib
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
import audio_devices

# In the --noconsole exe sys.stdout/stderr are None until the engine redirects
# them, so guard this — otherwise importing the module (e.g. from the Listen
# button before the engine has started) crashes with an AttributeError.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass
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
MODEL_PATH  = _cfg.get("MODEL_PATH", r"C:\Echo\vosk-model-small-en-us-0.15")
APPS        = _cfg.get("APPS", {})
PROC_NAMES  = _cfg.get("PROC_NAMES", {})

SAMPLE_RATE = 16000
FRAMES_PER_BUFFER = 512    # ~32ms chunks — finer granularity for low-latency partials
COOLDOWN = 1.5

# How long a partial result must stay unchanged before we treat it as a finished
# command and act on it (instead of waiting for Vosk's end-of-speech silence,
# which adds ~0.5s).  Lower = snappier but slightly more sensitive to mid-word pauses.
PARTIAL_STABLE_SECS = 0.12
CONFIDENCE_THRESHOLD = 0.65

# Populated from config in run() — use _cw(key) to get current trigger word
_COMMAND_WORDS:    dict[str, str]             = user_config.DEFAULT_COMMAND_WORDS.copy()
_VOLUME_STEPS:     dict[str, int]             = user_config.DEFAULT_VOLUME_STEPS.copy()
_CONTEXT_COMMANDS:   dict[str, dict[str, str]]  = user_config.DEFAULT_CONTEXT_COMMANDS.copy()
_SPOKEN_NAMES:       dict[str, str]             = {}   # display_name → spoken_name
_SPOKEN_TO_DISPLAY:  dict[str, str]             = {}   # spoken_name  → display_name
_WORD_DELAYS:        dict[str, int]             = {}   # command_key  → grace ms for bare verb
_CONTEXT_DELAYS:     dict[str, int]             = {}   # custom-command phrase → speed ms
_AUDIO_DEVICES:      dict[str, dict]            = {}   # spoken_name  → {"id":..., "name":...}
_MODES:              dict[str, dict]            = {}   # user modes (default is implicit)
_ACTIVE_MODE:        str                        = "default"   # current mode (runtime)


def _mode_names() -> list:
    return ["default"] + sorted(_MODES.keys())

def _active_groups() -> set:
    """Built-in command groups enabled in the current mode (default = all)."""
    if _ACTIVE_MODE == "default":
        return set(user_config.MODE_GROUPS)
    g = _MODES.get(_ACTIVE_MODE, {}).get("groups", {})
    return {k for k in user_config.MODE_GROUPS if g.get(k)}

def _active_context_commands() -> dict:
    """Custom commands active in the current mode."""
    if _ACTIVE_MODE == "default":
        return _CONTEXT_COMMANDS
    return _MODES.get(_ACTIVE_MODE, {}).get("commands", {})

def set_active_mode(name: str) -> None:
    """Switch the active mode.  The grammar watcher rebuilds the vocabulary to
    match within ~1s (build_grammar depends on the active mode)."""
    global _ACTIVE_MODE
    name = (name or "").strip().lower()
    if name not in _mode_names():
        print(f"  No mode called '{name}'")
        return
    _ACTIVE_MODE = name
    print(f"🎚  Mode → {name}")
    _status(f"Mode: {name.title()}")


def _spoken_all(app: str) -> list[str]:
    """Return every spoken alias for an app (comma-separated), or its display
    name if none are set."""
    raw = _SPOKEN_NAMES.get(app, "") or ""
    aliases = [w.strip() for w in raw.split(",") if w.strip()]
    return aliases or [app]

def _spoken(app: str) -> str:
    """Return the primary spoken alias for an app, falling back to its name."""
    return _spoken_all(app)[0]

def _cw_all(key: str) -> list[str]:
    """Return all trigger words for an action key (comma-separated aliases)."""
    raw = _COMMAND_WORDS.get(key, user_config.DEFAULT_COMMAND_WORDS.get(key, key))
    return [w.strip() for w in raw.split(",") if w.strip()]

def _cw(key: str) -> str:
    """Return the primary (first) trigger word for an action key."""
    parts = _cw_all(key)
    return parts[0] if parts else key


# ── Status overlay callback ───────────────────────────────────────────────
# Set by main.py after engine reload so the overlay can show what's happening.
_status_cb = None

def _status(msg: str) -> None:
    """Push a status message to the overlay. No-op if overlay not connected."""
    if _status_cb:
        try:
            _status_cb(msg)
        except Exception:
            pass


# ── Self-window control ────────────────────────────────────────────────────
# "echo" is Echo's own window.  Managing it with raw win32 calls from the engine
# thread leaves Tk's event loop out of sync, so the GUI freezes until clicked.
# main.py registers a callback that does iconify/deiconify on the Tk thread.
_self_window_cb = None     # callable(action) where action in {"minimise", "restore"}
_SELF_EXE = os.path.basename(sys.executable).lower()   # "echo.exe" when frozen

def _is_self_app(app_name: str | None) -> bool:
    """True if *app_name* refers to Echo's own window (this process)."""
    if not app_name:
        return False
    proc = (PROC_NAMES.get(app_name, "") or "").lower()
    return bool(proc) and proc == _SELF_EXE


def _get_active_proc() -> str:
    """Return the exe name (lowercase) of the current foreground window's process."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return ""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if time.monotonic() - _cache_built_at > _CACHE_TTL:
            _refresh_pid_cache()
        return _pid_name_cache.get(pid, "")
    except Exception:
        return ""


def _proc_matches_context(proc: str, context: str) -> bool:
    if context == "any":
        return True
    if context == "browser":
        return proc in user_config.BROWSER_PROCS
    if context == "explorer":
        return proc in user_config.EXPLORER_PROCS
    if context == "editor":
        return proc in user_config.EDITOR_PROCS
    # Check user-defined custom groups  {"music": ["spotify.exe", "chrome.exe"]}
    groups = user_config.get_custom_groups()
    if context in groups:
        return proc.lower() in [p.lower() for p in groups[context]]
    # Direct proc name match  e.g. context == "spotify.exe"
    return bool(proc) and proc.lower() == context.lower()


_WM_APPCOMMAND              = 0x0319
_APPCOMMAND_MEDIA_PLAY_PAUSE = 14


def _play_in_app(app_name: str) -> bool:
    """Send APPCOMMAND_MEDIA_PLAY_PAUSE directly to the app's window via WM_APPCOMMAND.
    Returns True if the message was delivered to a window."""
    try:
        hwnds = _windows_for_app(app_name)
        if not hwnds:
            return False
        hwnd = _pick_window(hwnds, app_name)
        win32gui.PostMessage(
            hwnd, _WM_APPCOMMAND, hwnd,
            _APPCOMMAND_MEDIA_PLAY_PAUSE << 16)
        print(f"  ↳ APPCOMMAND_MEDIA_PLAY_PAUSE → {app_name} (hwnd {hwnd:#x})")
        return True
    except Exception as _pe:
        print(f"  WM_APPCOMMAND failed: {_pe}")
        return False


def _execute_action(action) -> None:
    """Execute a shortcut string or a macro dict."""
    import time as _t
    if isinstance(action, str):
        keyboard.send(action)
    elif isinstance(action, dict) and action.get("type") == "macro":
        repeat = max(1, int(action.get("repeat", 1)))
        for _ in range(repeat):
            for step in action.get("steps", []):
                stype = step.get("type", "press")
                if stype == "press":
                    keys = step.get("keys", "")
                    if keys:
                        keyboard.send(keys)
                elif stype == "wait":
                    _t.sleep(max(0, step.get("ms", 100)) / 1000.0)


def _try_context_command(text: str) -> bool:
    """Try to run a context-sensitive command.
    Returns True if the phrase was recognised (whether or not context matched)."""
    cmds = _active_context_commands()
    if text not in cmds:
        return False
    proc    = _get_active_proc()
    targets = cmds[text]
    for context, action in targets.items():
        if _proc_matches_context(proc, context):
            try:
                _execute_action(action)
            except Exception as _ae:
                print(f"⚠️  Action error ({text!r}): {_ae}")
            win_title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            preview   = action if isinstance(action, str) else "macro"
            print(f"🖱  {text}  [{context}]  → {preview}  ({win_title})")
            _status(f"{text.title()}  [{context}]")
            return True
    # Phrase recognised but context didn't match
    contexts = " / ".join(targets.keys())
    print(f"  '{text}' only works in: {contexts}  (active: {proc or 'unknown'})")
    return True


def _try_specific_context(text: str) -> bool:
    """Fire an app/group-SPECIFIC context command (context is not 'any') when the
    foreground app matches.  This runs BEFORE the built-in commands so it can
    override them — e.g. 'enter' does ctrl+enter inside Claude but the normal
    Enter key everywhere else.  'any' commands never override (handled later)."""
    cmds = _active_context_commands()
    if text not in cmds:
        return False
    proc    = _get_active_proc()
    targets = cmds[text]
    for context, action in targets.items():
        if context == "any":
            continue
        if _proc_matches_context(proc, context):
            try:
                _execute_action(action)
            except Exception as _ae:
                print(f"⚠️  Action error ({text!r}): {_ae}")
            preview = action if isinstance(action, str) else "macro"
            print(f"🖱  {text}  [{context}]  → {preview}  (overrides default)")
            _status(f"{text.title()}  [{context}]")
            return True
    return False

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

# Apps that may hide to the system tray — matched by config name OR process name
# so they're handled however the user named them.
INCLUDE_HIDDEN: set[str] = {"steam", "discord"}
INCLUDE_HIDDEN_PROCS: set[str] = {"steam.exe", "discord.exe"}

# When an app has many windows, prefer the one whose title contains this.
PREFERRED_TITLE: dict[str, str] = {
    "discord": "discord",
}

def _terminate_proc(proc_name: str) -> None:
    """Terminate every running process whose exe name matches *proc_name*
    (case-insensitive).  Used for apps that ignore WM_CLOSE (e.g. Discord)."""
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"].lower() == proc_name.lower():
                p.terminate()
        except Exception:
            pass


# "open X" runs this instead of window detection.
# Discord: discord:// URI uses Discord's own restore/launch path — works whether
# Discord is closed, hidden in tray, or already visible.
OPEN_OVERRIDE = {
    "steam":   lambda: os.startfile("steam://open/main"),
    "discord": lambda: os.startfile("discord://"),
}

# The same overrides keyed by PROCESS name, so they apply no matter what the user
# named the app in their config.  Win32 SetForegroundWindow can't reliably restore
# these apps from a minimised/tray state, but their own URI handler always can.
PROC_OPEN_OVERRIDE = {
    "steam.exe":   lambda: os.startfile("steam://open/main"),
    "discord.exe": lambda: os.startfile("discord://"),
}

def _open_override_for(app_name: str):
    """Return the special open/focus handler for an app, matched by its config
    key OR its process name (so e.g. Discord restores correctly however it was
    added).  Returns None if the app has no override."""
    if app_name in OPEN_OVERRIDE:
        return OPEN_OVERRIDE[app_name]
    proc = (PROC_NAMES.get(app_name, "") or "").lower()
    return PROC_OPEN_OVERRIDE.get(proc)

# Special launch for new instances.
LAUNCH_OVERRIDE = {
    "files":   lambda: subprocess.Popen(["explorer.exe"]),
    "command": lambda: subprocess.Popen(
        ["cmd.exe"], creationflags=subprocess.CREATE_NEW_CONSOLE
    ),
}

# Custom close commands.
# Discord: WM_CLOSE only hides to tray, so we terminate the process instead.
CLOSE_OVERRIDE = {
    "steam":   lambda: subprocess.Popen([APPS["steam"], "-shutdown"]),
    "discord": lambda: _terminate_proc("discord.exe"),
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

# Spoken number words → digit, used by the layouts feature (layouts 1-9).
_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
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
    _status(f"Volume {'up' if delta > 0 else 'down'} {abs(pct)}%  →  {new_level*100:.0f}%")


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
    _status("Muted" if new_state else "Unmuted")


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
    _status(f"Moved {label} → {position}")


def send_to_background(app_name: str | None = None) -> None:
    """Push a window to the very bottom of the z-order (behind every other
    window) without minimising it — it stays open, just out of the way."""
    if app_name:
        if app_name not in APPS:
            print(f"  Don't know '{app_name}'")
            return
        hwnds = _windows_for_app(app_name)
        hwnd  = _pick_window(hwnds, app_name) if hwnds else None
        label = app_name
    else:
        hwnd  = win32gui.GetForegroundWindow()
        label = "current window"
    if not hwnd:
        print(f"  Couldn't find a window for '{app_name or 'current'}'")
        return
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
    win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    print(f"🗗  Sent {label} to background!")
    _status(f"{label.title()} → background")
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
    allow_hidden = (app_name in INCLUDE_HIDDEN
                    or (PROC_NAMES.get(app_name, "") or "").lower() in INCLUDE_HIDDEN_PROCS)
    found = []

    def _cb(hwnd, _):
        if not allow_hidden and not win32gui.IsWindowVisible(hwnd):
            return
        # Tray apps (discord, steam) can have their main window hidden with
        # no title string — still include them so we can restore/focus them.
        # For normal apps require a non-empty title to skip background threads.
        if not allow_hidden and not win32gui.GetWindowText(hwnd):
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
    """Bring a window to the foreground, restoring it first if minimised.

    Windows blocks SetForegroundWindow when the call comes from a process that
    doesn't own the current foreground window (its "focus-stealing prevention").
    A plain call therefore silently fails for minimised windows.  We temporarily
    AttachThreadInput to the foreground (and target) threads, which makes Windows
    treat us as part of that input queue and lets the focus call go through.
    """
    try:
        # Restore from minimised, otherwise just ensure it's shown
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        our_tid    = win32api.GetCurrentThreadId()
        target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
        fg_hwnd    = win32gui.GetForegroundWindow()
        fg_tid     = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0

        # The alt-key tap satisfies Windows' "user recently provided input" rule
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

        attached = set()
        try:
            for tid in (fg_tid, target_tid):
                if tid and tid != our_tid and tid not in attached:
                    if ctypes.windll.user32.AttachThreadInput(our_tid, tid, True):
                        attached.add(tid)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            try:
                win32gui.SetActiveWindow(hwnd)
            except Exception:
                pass
        finally:
            for tid in attached:
                ctypes.windll.user32.AttachThreadInput(our_tid, tid, False)
    except Exception as e:
        print(f"  Warning: couldn't bring window to foreground ({e})")


def _is_url(path: str) -> bool:
    """True for any URI-scheme path (http://, steam://, ms-settings: etc).
    Windows file paths never contain '://' so this is safe."""
    return "://" in path or path.startswith("ms-")

def _is_folder(path: str) -> bool:
    return pathlib.Path(path).is_dir()


def _launch(app_name: str) -> None:
    if app_name in LAUNCH_OVERRIDE:
        LAUNCH_OVERRIDE[app_name]()
    elif app_name in APPS:
        path = APPS[app_name]
        if path.lower().startswith("shell:"):
            # Start-menu / AppsFolder item (UWP, launcher-registered apps, etc.)
            subprocess.Popen(["explorer.exe", path])
        elif _is_url(path):
            import webbrowser
            webbrowser.open(path)
        elif _is_folder(path):
            os.startfile(path)
        else:
            os.startfile(path)
    else:
        print(f"  Don't know how to open '{app_name}'")
        return
    print(f"▶  Opened {app_name}!")
    _status(f"Opening {app_name}")


def open_or_focus(app_name: str) -> None:
    if app_name not in APPS:
        print(f"  Don't know how to open '{app_name}'")
        return
    # Echo's own window — restore via Tk so the GUI repaints correctly
    if _is_self_app(app_name) and _self_window_cb:
        _self_window_cb("restore")
        print(f"  Focused {app_name}!")
        _status(f"Focusing {app_name}")
        return

    path = APPS[app_name]
    # URLs and folders just open — no window-focus logic needed
    if _is_url(path) or _is_folder(path):
        _launch(app_name)
        return

    # ── Try to focus an existing window FIRST ──────────────────────────────
    # _set_foreground restores from minimised/tray.  For apps with their own
    # restore URI (Discord/Steam) this avoids using the URI when a window
    # already exists, because e.g. discord:// also navigates Discord to its
    # Friends view — focusing the window keeps whatever the user was looking at.
    hwnds = _windows_for_app(app_name)
    if hwnds:
        hwnd = _pick_window(hwnds, app_name)
        _set_foreground(hwnd)
        print(f"▶  Focused {app_name}!")
        _status(f"Focusing {app_name}")
        return

    # ── No window found — use the app's own restore/launch handler ─────────
    _ov = _open_override_for(app_name)
    if _ov:
        _ov()
        print(f"▶  Opened {app_name}!")
        _status(f"Opening {app_name}")
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


def merge_explorer_windows() -> None:
    """Merge all open File Explorer windows into one window with tabs."""
    try:
        import win32com.client
        shell = win32com.client.Dispatch("Shell.Application")
    except Exception as e:
        print(f"  Could not access Shell COM: {e}")
        return

    # Collect all Explorer windows + their current folder paths
    wins = []
    for w in shell.Windows():
        try:
            path = w.Document.Folder.Self.Path
            hwnd = int(w.HWND)
            if path and win32gui.IsWindow(hwnd):
                wins.append({"hwnd": hwnd, "path": path})
        except Exception:
            continue

    if not wins:
        print("  No File Explorer windows found.")
        return
    if len(wins) == 1:
        print("  Only one Explorer window open — nothing to merge.")
        return

    print(f"🗂  Merging {len(wins)} Explorer windows into tabs…")
    _status(f"Merging {len(wins)} Explorer windows into tabs")

    # Bring the first window to front as the target
    main = wins[0]
    win32gui.ShowWindow(main["hwnd"], win32con.SW_RESTORE)
    _set_foreground(main["hwnd"])
    time.sleep(0.5)

    for w in wins[1:]:
        # Open a new tab in the main window
        keyboard.send("ctrl+t")
        time.sleep(0.4)
        # Focus the address bar and navigate to the path
        keyboard.send("ctrl+l")
        time.sleep(0.25)
        keyboard.write(w["path"])
        keyboard.send("enter")
        time.sleep(0.5)
        # Close the now-redundant original window
        try:
            win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
            time.sleep(0.2)
        except Exception:
            pass

    print(f"🗂  Merged into {len(wins)} tabs!")


# ── AUDIO OUTPUT SWITCHING ─────────────────────────────────────────────────
def switch_audio(name: str) -> None:
    """Switch the default playback device to the one mapped to *name*."""
    dev = _AUDIO_DEVICES.get(name)
    if not dev or not dev.get("id"):
        print(f"  No audio device set up for '{name}'")
        return
    if audio_devices.set_default_output(dev["id"]):
        label = dev.get("name", name)
        print(f"🔊  Switched audio to {name}  ({label})")
        _status(f"Audio → {name.title()}")
    else:
        print(f"  Couldn't switch audio to '{name}'")


# ── LAYOUTS ────────────────────────────────────────────────────────────────
# A layout is a snapshot of which known apps are open, each window's position
# and size, and whether it's minimised/maximised/normal.  Saved per number 1-9.

def _app_for_proc(proc: str) -> "str | None":
    """Reverse-lookup: the app display name whose PROC_NAMES pattern matches
    *proc*, or None if the process isn't one of the user's configured apps."""
    proc = (proc or "").lower()
    if not proc:
        return None
    for app, pattern in PROC_NAMES.items():
        if pattern and _proc_matches(proc, pattern):
            return app
    return None


def save_layout(n: int) -> None:
    """Snapshot every open window that belongs to a configured app into layout *n*."""
    if time.monotonic() - _cache_built_at > _CACHE_TTL:
        _refresh_pid_cache()
    entries: list = []
    seen_apps: dict = {}

    def _cb(hwnd, _):
        # Minimised windows are still "visible"; tray-hidden ones are not — skip those.
        if not win32gui.IsWindowVisible(hwnd):
            return
        if not win32gui.GetWindowText(hwnd):
            return
        if win32gui.GetClassName(hwnd) in GLOBAL_EXCLUDE_CLASSES:
            return
        if not _is_taskbar_window(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        app = _app_for_proc(_pid_name_cache.get(pid, ""))
        if not app:
            return
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
        except Exception:
            return
        entries.append({
            "app":  app,
            "show": int(placement[1]),          # SW_SHOWNORMAL / MINIMIZED / MAXIMIZED
            "rect": list(placement[4]),         # (left, top, right, bottom) when normal
        })
        seen_apps[app] = seen_apps.get(app, 0) + 1

    win32gui.EnumWindows(_cb, None)
    user_config.set_layout(n, entries)
    apps = ", ".join(sorted(seen_apps)) or "nothing"
    print(f"💾  Saved layout {n}  ({len(entries)} window(s): {apps})")
    _status(f"Saved layout {n}")


def restore_layout(n: int) -> None:
    """Restore layout *n*: open any missing apps, then position/size every window
    and apply its minimised/maximised/normal state.  Runs in a background thread
    so launching apps doesn't stall the voice engine."""
    entries = user_config.get_layout(n)
    if not entries:
        print(f"  Layout {n} is empty — say 'save layout {n}' first.")
        _status(f"Layout {n} is empty")
        return

    print(f"📂  Restoring layout {n}  ({len(entries)} window(s))…")
    _status(f"Restoring layout {n}")

    def _worker():
        # 1. Launch any app in the layout that isn't running yet.
        needed = []
        for e in entries:
            if e["app"] not in needed:
                needed.append(e["app"])
        launched = False
        for app in needed:
            if app in APPS and not _windows_for_app(app):
                try:
                    open_or_focus(app)
                    launched = True
                except Exception as ex:
                    print(f"  couldn't open {app}: {ex}")
        if launched:
            time.sleep(2.5)   # give freshly launched apps time to create windows
            _refresh_pid_cache()

        # 2. Position each window; track used hwnds so multiple windows of the
        #    same app go to different saved slots.
        used: set = set()
        placed = 0
        for e in entries:
            hwnds = _windows_for_app(e["app"])
            target = next((h for h in hwnds if h not in used), None)
            if target is None:
                continue
            used.add(target)
            try:
                rect = tuple(e["rect"])
                win32gui.SetWindowPlacement(
                    target, (0, int(e["show"]), (-1, -1), (-1, -1), rect))
                placed += 1
            except Exception as ex:
                print(f"  couldn't place {e['app']}: {ex}")
        print(f"📂  Layout {n} restored  ({placed}/{len(entries)} window(s) placed)")
        _status(f"Layout {n} restored")

    _threading.Thread(target=_worker, daemon=True).start()


def minimise_app(app_name: str | None = None) -> None:
    if app_name:
        if app_name not in APPS:
            print(f"  Don't know '{app_name}'")
            return

        # Echo's own window — minimise via Tk so the GUI stays in sync
        if _is_self_app(app_name) and _self_window_cb:
            _self_window_cb("minimise")
            print(f"  Minimised {app_name}!")
            _status(f"Minimising {app_name}")
            return

        # For apps whose visible window can't be found by process/class,
        # bring the window forward via its open handler then send Win+Down.
        if app_name in MINIMISE_VIA_FOCUS:
            if app_name in OPEN_OVERRIDE:
                OPEN_OVERRIDE[app_name]()
                time.sleep(0.6)     # let the window reach the foreground
            keyboard.send("windows+down")
            print(f"🗕  Minimised {app_name}!")
            _status(f"Minimising {app_name}")
            return

        hwnds = _windows_for_app(app_name)
        if hwnds:
            hwnd = _pick_window(hwnds, app_name)
            win32gui.ShowWindow(hwnd, 6)
            print(f"🗕  Minimised {app_name}!")
            _status(f"Minimising {app_name}")
        else:
            print(f"  Couldn't find a window for '{app_name}'")
    else:
        win = gw.getActiveWindow()
        if win:
            win.minimize()
            print("🗕  Minimised current window!")
            _status("Minimising current window")


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
        # Minimise so the user can see something happened.
        # SW_FORCEMINIMIZE (11) works even on Electron apps, dialogs, and
        # windows that ignore a normal SW_MINIMIZE (6).
        SW_FORCEMINIMIZE = 11
        for hwnd in hwnds:
            if win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, SW_FORCEMINIMIZE)

    _pending_cancel.clear()
    _pending_close = {"app": app_name, "hwnds": hwnds}

    print(f"⏳  Closing {app_name} in {delay}s — say 'undo' to cancel!")
    _status(f"Closing {app_name} in {delay}s  —  say '{_cw('undo')}' to cancel")

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
    # Restore force-minimised windows
    for hwnd in hwnds:
        try:
            if win32gui.IsWindow(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                _set_foreground(hwnd)
        except Exception:
            pass
    print(f"↩  Cancelled close — {app_name} restored!")
    _status(f"Undo — {app_name} restored")


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


def build_grammar(active_proc: str = "") -> str:
    """Build the Vosk recognition grammar for the given foreground process.

    App-control commands (open/close/minimise/…) are always included for every
    app — you need to be able to switch apps from anywhere.

    Context commands are filtered: only phrases whose context group matches
    *active_proc* (or is "any") are added.  This keeps the vocabulary small
    when, say, a game is focused — no browser shortcuts cluttering the model.
    The grammar is rebuilt dynamically whenever the foreground app changes.
    """
    words = ["[unk]"]
    groups = _active_groups()   # which command groups the current mode allows

    # Always available — engine control + mode switching (so you can switch back)
    for key in ("undo", "diagnose", "stop_engine", "restart_engine"):
        words.extend(_cw_all(key))
    for mw in _cw_all("set_mode"):
        for mode in _mode_names():
            words.append(f"{mw} {mode}")

    # ── Media group ───────────────────────────────────────────────────────
    if "media" in groups:
        for key in ("skip", "previous", "rewind", "play_pause", "mute"):
            words.extend(_cw_all(key))
        words.append("play")
        _play_words = list(dict.fromkeys(_cw_all("play_pause") + ["play"]))
        for pw in _play_words:
            for app in APPS:
                for sp in _spoken_all(app):
                    words.append(f"{pw} {sp}")
        for step in _VOLUME_STEPS:
            words.append(f"volume up {step}")
            words.append(f"volume down {step}")

    # ── Keyboard group ────────────────────────────────────────────────────
    if "keyboard" in groups:
        for key in ("copy", "paste", "save", "enter"):
            words.extend(_cw_all(key))

    # ── Apps group ────────────────────────────────────────────────────────
    if "apps" in groups:
        for ow in _cw_all("open"):
            words.append(ow)
            words.append(f"{ow} all")
            for app in APPS:
                for sp in _spoken_all(app):
                    words.append(f"{ow} {sp}")
                    words.append(f"{ow} new {sp}")
                    for pos in SNAP_POSITIONS:
                        words.append(f"{ow} {sp} {pos}")
        for mw in _cw_all("minimise"):
            words.append(mw)
            words.append(f"{mw} all")
            for app in APPS:
                for sp in _spoken_all(app):
                    words.append(f"{mw} {sp}")
        for xw in _cw_all("maximise"):
            words.append(xw)
            for app in APPS:
                for sp in _spoken_all(app):
                    words.append(f"{xw} {sp}")
        for cw in _cw_all("close"):
            words.append(cw)
            words.append(f"{cw} current")
            for app in APPS:
                for sp in _spoken_all(app):
                    words.append(f"{cw} {sp}")
        for mvw in _cw_all("move"):
            for pos in SNAP_POSITIONS:
                words.append(f"{mvw} {pos}")
            words.append(f"{mvw} to background")
            for app in APPS:
                for sp in _spoken_all(app):
                    for pos in SNAP_POSITIONS:
                        words.append(f"{mvw} {sp} {pos}")
                    words.append(f"{mvw} {sp} to background")
        for mgw in _cw_all("merge"):
            words.append(mgw)
            for app in APPS:
                for sp in _spoken_all(app):
                    words.append(f"{mgw} {sp}")
        for app in APPS:        # bare app names (deferred-verb completion)
            for sp in _spoken_all(app):
                words.append(sp)

    # ── Layouts group ─────────────────────────────────────────────────────
    if "layouts" in groups:
        for nw in _NUMBER_WORDS:
            for sw in _cw_all("save"):
                words.append(f"{sw} layout {nw}")
            for ow in _cw_all("open"):
                words.append(f"{ow} layout {nw}")

    # ── Audio group ───────────────────────────────────────────────────────
    if "audio" in groups:
        for cw in _cw_all("switch_audio"):
            for dev_name in _AUDIO_DEVICES:
                words.append(f"{cw} {dev_name}")

    # Custom commands for the active mode — filtered to the focused app's context.
    for phrase, targets in _active_context_commands().items():
        for context in targets:
            if context == "any" or _proc_matches_context(active_proc, context):
                words.append(phrase)
                break

    # Deduplicate while preserving order
    seen = set(); out = []
    for w in words:
        if w not in seen:
            seen.add(w); out.append(w)
    return json.dumps(out)


def _early_fire_set(grammar_json: str) -> set:
    """Return the grammar phrases that are safe to execute as soon as a partial
    result stabilises — i.e. complete, actionable commands.

    Bare action verbs ("open", "close", …) are excluded: on their own they're
    just prefixes still waiting for an app/position, so firing them early would
    cut the user off mid-phrase.  Everything else (one-shots, "open firefox",
    "volume up three", "move left", …) is a complete command.
    """
    try:
        phrases = set(json.loads(grammar_json))
    except Exception:
        return set()
    phrases.discard("[unk]")
    for key in ("open", "close", "minimise", "maximise", "move", "merge"):
        for v in _cw_all(key):
            phrases.discard(v)
    return phrases


def _prefix_fire_set(grammar_json: str, early_set: set) -> set:
    """Complete commands that are ALSO the start of a longer grammar phrase
    (e.g. "save" → "save layout three", "open files" → "open files left").

    These get a little extra settle time before firing so the early-fire doesn't
    act on the short version while the user is still saying the long one."""
    try:
        all_phrases = [p for p in json.loads(grammar_json) if p != "[unk]"]
    except Exception:
        return set()
    out = set()
    for p in early_set:
        pfx = p + " "
        if any(q != p and q.startswith(pfx) for q in all_phrases):
            out.add(p)
    return out


# Extra time (seconds) an app-name command must hold steady before firing.
# App names that sound alike ("files"/"firefox") need a moment for the decoder
# to settle, otherwise the early-fire grabs Vosk's first (often wrong) guess.
_APP_SETTLE_EXTRA = 0.12

def _app_forms_set() -> set:
    """All spoken forms of the configured apps (used to tell which complete
    commands contain an app name, so they can be given longer to settle)."""
    return {sp.lower() for a in APPS for sp in _spoken_all(a)}

def _phrase_has_app(phrase: str, app_forms: set) -> bool:
    """True if any run of 1-3 words in *phrase* is an app name."""
    parts = phrase.split()
    for i in range(len(parts)):
        for n in (3, 2, 1):
            if i + n <= len(parts) and " ".join(parts[i:i + n]) in app_forms:
                return True
    return False


# Bare verbs that do NOTHING useful on their own — they are pure prefixes that
# always need an app/position to follow.  "open" especially is the start of more
# grammar phrases than any other word, so the decoder hallucinates it during
# silence; since acting on a bare one is pointless we ignore them entirely.
_NULL_BARE_KEYS = ("open", "close", "move")

def _is_null_bare(text: str) -> bool:
    """True if *text* is exactly a bare prefix-verb that has no standalone action."""
    for key in _NULL_BARE_KEYS:
        if text in _cw_all(key):
            return True
    return False


def _command_trigger_words() -> set:
    """All single trigger words that are themselves complete/standalone commands.
    Used by the dual-model filter to tell a real 'open <app>' (keep) from a
    hallucinated 'open <command>' ghost (strip)."""
    keys = ("skip", "previous", "rewind", "play_pause", "mute", "copy", "paste",
            "save", "enter", "undo", "diagnose", "stop_engine", "restart_engine",
            "open", "close", "minimise", "maximise", "move", "merge")
    s = set()
    for k in keys:
        s.update(_cw_all(k))
    s.add("play")
    return s


def _build_cmd_timing() -> dict:
    """Map a command's trigger word -> required stable time in SECONDS, from the
    user's per-command timing settings (Commands tab "Speed (ms)").

    Applies to ANY command:
      • terminal commands (copy / paste / skip / …) — overrides the global
        response speed for that phrase, so you can make them near-instant.
      • bare action verbs (minimise / maximise / merge) — the grace time before
        the bare form fires, leaving room for a following app name.

    Only values > 0 are included; 0 / unset means "use the default timing".
    The pure-prefix verbs open / close / move never early-fire regardless
    (they're suppressed by _is_null_bare)."""
    out = {}
    for key, ms in (_WORD_DELAYS or {}).items():
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            continue
        if ms > 0:
            for w in _cw_all(key):
                out[w] = ms / 1000.0
    # Per-custom-command speed overrides (Custom Commands tab) — keyed by phrase.
    for phrase, ms in (_CONTEXT_DELAYS or {}).items():
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            continue
        if ms > 0:
            out[phrase.strip().lower()] = ms / 1000.0
    return out


def average_confidence(result: dict) -> float:
    words = result.get("result", [])
    if not words:
        return 0.0
    return sum(w.get("conf", 0.0) for w in words) / len(words)


def _parse_app(words: list[str], start: int) -> tuple[str | None, list[str]]:
    """Try to match the longest app name beginning at words[start].
    Checks display names then spoken aliases (longest match wins).
    Returns (display_name, remaining_words) or (None, words[start:])."""
    for length in range(min(3, len(words) - start), 0, -1):
        candidate = " ".join(words[start : start + length])
        if candidate in APPS:
            return candidate, words[start + length:]
        if candidate in _SPOKEN_TO_DISPLAY:
            display = _SPOKEN_TO_DISPLAY[candidate]
            if display in APPS:
                return display, words[start + length:]
    return None, words[start:]



last_command = None
last_command_time = 0


def handle_command(text: str) -> bool:
    """Process one recognised phrase.
    Returns True if the Vosk decoder should be reset (unused now, kept for API)."""
    global last_command, last_command_time
    if not text:
        return False
    words = text.split()
    now = time.time()

    # Identical-repeat cooldown (fast re-fires of the same phrase)
    if text == last_command and (now - last_command_time) < COOLDOWN:
        return False

    last_command = text
    last_command_time = now

    # ── Modes: "set mode film" ─────────────────────────────────────────────
    for _smw in _cw_all("set_mode"):
        if text.startswith(_smw + " "):
            target = text[len(_smw) + 1:].strip()
            if target in _mode_names():
                set_active_mode(target)
            else:
                print(f"  No mode called '{target}'")
            return False

    # App/group-specific custom command — overrides a built-in of the same name
    # (e.g. "enter" → ctrl+enter inside Claude).  "any" commands don't override.
    if _try_specific_context(text):
        return False

    # ── Layouts: "save layout three" / "open layout three" ─────────────────
    if len(words) == 3 and words[1] == "layout" and words[2] in _NUMBER_WORDS:
        num = _NUMBER_WORDS[words[2]]
        if words[0] in _cw_all("save"):
            save_layout(num)
            return False
        if words[0] in _cw_all("open"):
            restore_layout(num)
            return False

    # ── Audio: "change to headphones" ──────────────────────────────────────
    for _aw in _cw_all("switch_audio"):
        if text.startswith(_aw + " "):
            target = text[len(_aw) + 1:].strip()
            if target in _AUDIO_DEVICES:
                switch_audio(target)
                return False

    if text in _cw_all("skip"):
        print("⏭  Skipping track!")
        _status("Skipping track")
        keyboard.send("next track")
    elif text in _cw_all("previous"):
        print("⏮  Previous track!")
        _status("Previous track")
        keyboard.send("previous track")
    elif text in _cw_all("rewind"):
        print("🔁  Restarting track!")
        _status("Restarting track")
        keyboard.send("previous track")
        time.sleep(0.05)
        keyboard.send("previous track")
    elif text in _cw_all("play_pause") or text == "play" or (
            text.split()[0] in (_cw_all("play_pause") + ["play"])
            and len(text.split()) > 1):
        words_l = text.split()
        if len(words_l) > 1:
            app, _ = _parse_app(words_l, 1)
            if app:
                print(f"▶  Play {app}!")
                _status(f"Play {app.title()}")
                open_or_focus(app)
                # Give the window time to come to the foreground before we send
                time.sleep(1.2)
                # Send directly to the app's window so the OS routes it correctly
                if not _play_in_app(app):
                    keyboard.send("play/pause media")
            else:
                print("⏸  Toggling playback!")
                _status("Play / Pause")
                keyboard.send("play/pause media")
        else:
            print("⏸  Toggling playback!")
            _status("Play / Pause")
            keyboard.send("play/pause media")
    elif text in _cw_all("copy"):
        print("📋  Copy!")
        _status("Copy")
        keyboard.send("ctrl+c")
    elif text in _cw_all("paste"):
        print("📋  Paste!")
        _status("Paste")
        keyboard.send("ctrl+v")
    elif text in _cw_all("save"):
        print("💾  Save!")
        _status("Save")
        keyboard.send("ctrl+s")
    elif text in _cw_all("enter"):
        print("↵  Enter!")
        _status("Enter")
        keyboard.send("enter")
    elif text in _cw_all("undo"):
        undo_close()
    elif text in _cw_all("stop_engine"):
        print("🛑  Closing Echo!")
        _status("Stopping Echo")
        _stop_event.set()
    elif text in _cw_all("restart_engine"):
        print("🔄  Restarting Echo!")
        _status("Restarting Echo")
        _restart_requested = True
        _stop_event.set()
    elif words[0] == "volume" and len(words) == 3 and words[1] in ("up", "down"):
        change_volume(words[1], words[2])
    elif text in _cw_all("mute"):
        toggle_mute()
    elif text in _cw_all("diagnose"):
        _status("Running diagnostic")
        print_diagnostic()
    elif words[0] in _cw_all("move"):
        if len(words) < 2:
            print(f"  Say '{_cw('move')}' followed by an app name and/or position")
        elif words[-2:] == ["to", "background"]:
            app_words = words[1:-2]
            if app_words:
                app, _ = _parse_app(words[:-2], 1)
                send_to_background(app) if app else send_to_background(None)
            else:
                send_to_background(None)          # current window
        else:
            app, rest = _parse_app(words, 1)
            if app:
                snap_app(app, " ".join(rest))
            else:
                snap_app(None, " ".join(words[1:]))
    elif words[0] in _cw_all("open"):
        if len(words) == 1:
            print(f"  Say '{_cw('open')}' followed by an app name")
        elif words[1] == "all":
            keyboard.send("windows+d")
            print("🗖  Showing all windows!")
            _status("Show all windows")
        elif words[1] == "new":
            if len(words) > 2:
                app, _ = _parse_app(words, 2)
                _launch(app) if app else print(f"  Say '{_cw('open')} new' followed by an app name")
            else:
                print(f"  Say '{_cw('open')} new' followed by an app name")
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
    elif words[0] in _cw_all("minimise"):
        if len(words) > 1:
            if words[1] == "all":
                keyboard.send("windows+d")
                print("🗕  Minimised all windows!")
                _status("Minimise all windows")
            else:
                app, _ = _parse_app(words, 1)
                minimise_app(app) if app else minimise_app()
        else:
            minimise_app()
    elif words[0] in _cw_all("maximise"):
        app, _ = _parse_app(words, 1) if len(words) > 1 else (None, [])
        snap_app(app, "fullscreen")
    elif words[0] in _cw_all("merge"):
        merge_explorer_windows()
    elif words[0] in _cw_all("close") and len(words) > 1 and words[1] == "current":
        keyboard.send("ctrl+w")
        print("✕  Closed current tab!")
        _status("Close current tab")
    elif words[0] in _cw_all("close"):
        if len(words) > 1:
            app, _ = _parse_app(words, 1)
            close_app(app) if app else print(f"  Say '{_cw('close')}' followed by an app name")
        else:
            print(f"  Say '{_cw('close')}' followed by an app name")
    elif _try_context_command(text):
        pass   # handled inside _try_context_command
    return False


# ── ENGINE ───────────────────────────────────────────────────────────────
_stop_event     = _threading.Event()
_restart_requested = False


_SMALL_MODEL_NAME = "vosk-model-small-en-us-0.15"


# Cached model for the "Listen" feature so repeated uses don't reload it.
_listen_model = None

def listen_once(seconds: float = 2.0, on_start=None) -> str:
    """Record *seconds* of audio with an OPEN-vocabulary recogniser (no grammar
    restriction) and return what was heard, lowercased.

    *on_start* (if given) is called the moment recording actually begins, after
    the model has loaded — so the UI can prompt the user to speak at the right
    time rather than during the initial model load.

    Used by the App Manager's "Listen" button to discover how Vosk actually
    hears a spoken app name, so the user can use that as the spoken name.
    Opens its own short-lived audio stream; safe to call from the GUI.
    """
    global _listen_model
    if _listen_model is None:
        _listen_model = Model(user_config.get_model_path())
    rec = KaldiRecognizer(_listen_model, SAMPLE_RATE)
    rec.SetWords(True)

    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
                     input=True, frames_per_buffer=FRAMES_PER_BUFFER)
    try:
        stream.start_stream()
        if on_start:
            try: on_start()
            except Exception: pass
        needed = int(SAMPLE_RATE * max(0.2, seconds))
        read = 0
        while read < needed:
            data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
            rec.AcceptWaveform(data)
            read += FRAMES_PER_BUFFER
    finally:
        try:
            stream.stop_stream(); stream.close()
        except Exception:
            pass
        pa.terminate()
    return json.loads(rec.FinalResult()).get("text", "").strip().lower()


def _dual_model_filter(main_text: str, ref_text: str) -> tuple[str, str | None]:
    """Use the small model's output to detect a hallucinated leading word in
    the main model's result.

    Logic: if the small model's first word matches the main model's *second*
    word, the main model may have prepended a ghost word.  But we ONLY strip it
    when that second word is itself a complete/standalone command — otherwise
    "open firefox" (where the ref model simply missed "open") would be wrongly
    cut down to "firefox".

    Returns (filtered_text, stripped_word_or_None).

    Examples
    --------
    main="open skip",         ref="skip"          → ("skip",         "open")   # skip is a command → strip
    main="open firefox",      ref="firefox"       → ("open firefox", None)     # firefox is an app → keep
    main="open open firefox", ref="open firefox"  → ("open firefox", "open")   # doubled verb → strip
    main="close firefox",     ref="close firefox" → ("close firefox",None)
    """
    if not ref_text or not main_text:
        return main_text, None
    main_words = main_text.split()
    ref_words  = ref_text.split()
    if len(main_words) <= 1:
        return main_text, None    # only one word, nothing to strip
    # Strip only when the ref confirms the 2nd word AND that word is itself a
    # standalone command (so the first word really is a redundant ghost verb).
    if ref_words[0] == main_words[1] and main_words[1] in _command_trigger_words():
        stripped = " ".join(main_words[1:])
        return stripped, main_words[0]
    return main_text, None


def run(stop_event: _threading.Event | None = None) -> bool:
    """Start the voice engine.  Returns True if a restart was requested."""
    global APPS, PROC_NAMES, MODEL_PATH, _stop_event, _restart_requested
    global CONFIDENCE_THRESHOLD, COOLDOWN, _COMMAND_WORDS, _VOLUME_STEPS, _CONTEXT_COMMANDS
    global _SPOKEN_NAMES, _SPOKEN_TO_DISPLAY, PARTIAL_STABLE_SECS, _WORD_DELAYS
    global _AUDIO_DEVICES, _CONTEXT_DELAYS, _MODES, _ACTIVE_MODE
    _cfg                 = user_config.load()
    MODEL_PATH           = user_config.get_model_path()
    _MODES               = user_config.get_modes()
    _ACTIVE_MODE         = "default"   # always start in default mode
    APPS                 = _cfg.get("APPS", APPS)
    PROC_NAMES           = _cfg.get("PROC_NAMES", PROC_NAMES)
    CONFIDENCE_THRESHOLD = user_config.get_confidence_threshold()
    COOLDOWN             = user_config.get_cooldown()
    PARTIAL_STABLE_SECS  = user_config.get_response_delay()
    _WORD_DELAYS         = user_config.get_word_delays()
    _CONTEXT_DELAYS      = user_config.get_context_delays()
    _AUDIO_DEVICES       = user_config.get_audio_devices()
    _COMMAND_WORDS       = user_config.get_command_words()
    _VOLUME_STEPS        = user_config.get_volume_steps()
    _CONTEXT_COMMANDS    = user_config.get_context_commands()
    _spoken_raw          = user_config.get_spoken_names()
    _SPOKEN_NAMES        = _spoken_raw
    _SPOKEN_TO_DISPLAY   = {}
    for _disp, _raw in _spoken_raw.items():
        for _alias in (_raw or "").split(","):
            _alias = _alias.strip()
            if _alias:
                _SPOKEN_TO_DISPLAY[_alias] = _disp

    if stop_event is None:
        stop_event = _threading.Event()
    _stop_event        = stop_event
    _restart_requested = False

    # ── Load main model ───────────────────────────────────────────────────
    print("Loading model...")
    model   = Model(MODEL_PATH)
    grammar = build_grammar(_get_active_proc())
    # Build the recogniser with the grammar passed to the constructor rather than
    # rec.SetGrammar() — SetGrammar mid-stream is unstable and crashes Vosk; the
    # constructor path safely ignores any out-of-vocabulary words instead.
    rec     = KaldiRecognizer(model, SAMPLE_RATE, grammar)
    rec.SetWords(True)

    # ── Optionally load small reference model for dual-model ghost check ──
    # When the main model is NOT the small model itself, try to load the small
    # model from the same folder.  It runs in parallel, receiving the same audio,
    # and its first word is used to validate the main model's output.
    rec_ref   = None
    model_ref = None
    _ref_last_text = ""          # most recent finalised text from the ref model

    small_path = pathlib.Path(MODEL_PATH).parent / _SMALL_MODEL_NAME
    dual_enabled = user_config.get_dual_model_check()
    if dual_enabled and small_path.exists() and pathlib.Path(MODEL_PATH).name != _SMALL_MODEL_NAME:
        try:
            print(f"Loading reference model ({_SMALL_MODEL_NAME}) for dual-model ghost check…")
            model_ref = Model(str(small_path))
            rec_ref   = KaldiRecognizer(model_ref, SAMPLE_RATE, grammar)
            print("  ✓  Dual-model ghost check active.")
        except Exception as _e:
            print(f"  ✗  Could not load reference model: {_e}")
            rec_ref = None
    elif dual_enabled and not small_path.exists() and pathlib.Path(MODEL_PATH).name != _SMALL_MODEL_NAME:
        print(f"  ℹ  Dual-model noise filter enabled but small model not found at {small_path}")
        print(f"     Download '{_SMALL_MODEL_NAME}' in Settings → Models to activate it.")

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

    # ── Context grammar watcher ───────────────────────────────────────────
    # Rebuilds the Vosk grammar whenever the foreground app changes so that
    # only context commands relevant to the active app are in the vocabulary.
    #
    # The watcher thread MUST NOT touch the recogniser directly: Vosk objects
    # are not thread-safe, and SetGrammar mid-stream crashes.  Instead it just
    # publishes the new grammar string; the audio loop rebuilds the recogniser
    # on its own thread (via the constructor, which is crash-safe).
    _current_grammar = [grammar]    # last grammar we've seen
    _pending_grammar = [None]       # (grammar, proc) handed to the audio loop

    def _grammar_watcher():
        while not stop_event.is_set():
            proc        = _get_active_proc()
            new_grammar = build_grammar(proc)
            if new_grammar != _current_grammar[0]:
                _current_grammar[0] = new_grammar
                _pending_grammar[0] = (new_grammar, proc)
            stop_event.wait(0.8)   # check ~every 800 ms

    _threading.Thread(target=_grammar_watcher, daemon=True).start()

    # ── Low-latency partial tracking ──────────────────────────────────────
    _early_set     = _early_fire_set(grammar)   # complete commands we can fire early
    _prefix_set    = _prefix_fire_set(grammar, _early_set)  # could still be extended
    _cmd_timing    = _build_cmd_timing()         # per-command stable-time overrides
    _app_forms     = _app_forms_set()            # spoken app names (for settle time)
    _partial_text  = ""                          # last partial seen
    _partial_since = 0.0                         # when it last changed

    try:
        while not stop_event.is_set():
            # Apply a pending grammar change on THIS thread (Vosk is single-thread).
            pend = _pending_grammar[0]
            if pend is not None:
                _pending_grammar[0] = None
                new_grammar, proc = pend
                try:
                    rec = KaldiRecognizer(model, SAMPLE_RATE, new_grammar)
                    rec.SetWords(True)
                    if rec_ref is not None and model_ref is not None:
                        rec_ref = KaldiRecognizer(model_ref, SAMPLE_RATE, new_grammar)
                        _ref_last_text = ""
                    _early_set    = _early_fire_set(new_grammar)
                    _prefix_set   = _prefix_fire_set(new_grammar, _early_set)
                    _partial_text = ""
                    print(f"  ↻  Grammar updated for '{proc or 'unknown'}'")
                except Exception as _ge:
                    print(f"  Grammar update failed: {_ge}")

            data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)

            # ── Feed reference model (keep it in sync) ────────────────────
            if rec_ref is not None:
                if rec_ref.AcceptWaveform(data):
                    r = json.loads(rec_ref.Result())
                    t = r.get("text", "").strip().lower()
                    if t:
                        _ref_last_text = t

            # ── Main model ────────────────────────────────────────────────
            if rec.AcceptWaveform(data):
                _partial_text = ""    # utterance finalised — reset partial tracking
                result = json.loads(rec.Result())
                text   = result.get("text", "").strip().lower()
                if not text or text == "[unk]":
                    continue

                # Dual-model noise filter
                noise_word = None
                if rec_ref is not None:
                    ref_partial = json.loads(
                        rec_ref.PartialResult()
                    ).get("partial", "").strip().lower()
                    ref  = ref_partial or _ref_last_text
                    text, noise_word = _dual_model_filter(text, ref)
                    if not text:
                        continue

                # Ignore a bare prefix-verb on its own (e.g. a stray "open"
                # hallucinated during silence) — it has no standalone action.
                if _is_null_bare(text):
                    continue

                conf = average_confidence(result)
                if conf >= CONFIDENCE_THRESHOLD:
                    notes = []
                    if noise_word: notes.append(f"noise filter removed '{noise_word}'")
                    note_str = f"  [{', '.join(notes)}]" if notes else ""
                    print(f"🎤  '{text}'{note_str}")

                    if handle_command(text):
                        rec.Reset()
                        if rec_ref is not None:
                            rec_ref.Reset()
                            _ref_last_text = ""
                else:
                    print(f"💤  Low confidence ({conf:.0%}): ignored")

            else:
                # ── Low-latency path ──────────────────────────────────────
                # Act on a partial as soon as it stabilises into a complete
                # command, instead of waiting for end-of-speech silence (~0.5s).
                partial = json.loads(rec.PartialResult()).get("partial", "").strip().lower()
                tnow = time.time()
                if partial != _partial_text:
                    _partial_text  = partial
                    _partial_since = tnow
                elif partial:
                    # A per-command "Speed (ms)" override wins; otherwise complete
                    # commands fire after the global response delay (+ a little
                    # extra for app names / extendable phrases), and bare verbs
                    # never fire on their own here.
                    if _is_null_bare(partial):
                        required = None              # never fire a bare prefix-verb
                    elif partial in _cmd_timing:
                        required = _cmd_timing[partial]   # explicit per-command time
                    elif partial in _early_set:
                        # App-name commands and commands that could still be
                        # extended (e.g. "save" → "save layout three") settle a
                        # touch slower so the decoder doesn't fire the short form.
                        required = PARTIAL_STABLE_SECS
                        if (_phrase_has_app(partial, _app_forms)
                                or partial in _prefix_set):
                            required += _APP_SETTLE_EXTRA
                    else:
                        required = None
                    if required is not None and (tnow - _partial_since) >= required:
                        print(f"🎤  '{partial}'")
                        handle_command(partial)
                        rec.Reset()
                        if rec_ref is not None:
                            rec_ref.Reset()
                            _ref_last_text = ""
                        _partial_text = ""
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    return _restart_requested


if __name__ == "__main__":
    while run():          # loop handles "restart echo"
        print("Restarting...\n")