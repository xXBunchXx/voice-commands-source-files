"""
Manages per-user config stored in %APPDATA%/Echo/config.json.

Version scheme  A.B.C.D  (all integers, read left to right):
  A — every new build; likely to be buggy.  Reset to 0 when B increments.
  B — tested and confirmed stable.          Reset to 0 when C increments.
  C — fairly important / significant update. Reset to 0 when D increments.
  D — huge overhaul / milestone release.

Incrementing a number always resets every number to its left back to 0.
Example progression: 0.0.0.1 → 1.0.0.1 → 2.0.0.1 → 0.1.0.1 → 1.1.0.1 → 0.0.1.1 …

Save-file compatibility:
  Configs whose stored VERSION is older than RESET_BASELINE are wiped to
  defaults once, then the user's config is built fresh.  After that first
  reset, configs are migrated forward key-by-key and are never wiped again
  unless a future RESET_BASELINE bump is deliberately added.
"""
import json
import os
import pathlib
import sys

APPDATA_DIR = pathlib.Path(os.getenv("APPDATA", "~")) / "Echo"
CONFIG_FILE = APPDATA_DIR / "config.json"

# ── Version ───────────────────────────────────────────────────────────────────
APP_VERSION = "1.0.0.0"

# Any config whose stored VERSION parses to less than this tuple is wiped to
# defaults on next load.  Bump this only when a deliberate full reset is needed.
# Under normal development leave RESET_BASELINE alone and just update APP_VERSION.
RESET_BASELINE = (1, 0, 0, 0)


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0, 0, 0, 0)

# ── Exe / script directory ────────────────────────────────────────────────────
# When frozen by PyInstaller sys.executable is the .exe; otherwise it's python.
# We use this to resolve the model path relative to wherever the exe lives.
def _exe_dir() -> pathlib.Path:
    """Return the folder that contains the exe (frozen) or the script (dev)."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller .exe — sys.executable is the .exe path
        return pathlib.Path(sys.executable).parent
    else:
        # Running as a plain .py script — use the directory of this file
        return pathlib.Path(__file__).resolve().parent

# The model folder name stored in config (relative, no leading path).
# Resolved against the exe directory at runtime so it works on any machine.
DEFAULT_MODEL_FOLDER = "vosk-model-small-en-us-0.15"

# ── Context command system ───────────────────────────────────────────────────
# Maps process names to context labels used in CONTEXT_COMMANDS
BROWSER_PROCS:  frozenset = frozenset({
    "chrome.exe", "firefox.exe", "msedge.exe", "opera.exe",
    "brave.exe", "vivaldi.exe", "waterfox.exe", "librewolf.exe",
})
EXPLORER_PROCS: frozenset = frozenset({"explorer.exe"})
EDITOR_PROCS:   frozenset = frozenset({
    "code.exe", "notepad.exe", "notepad++.exe", "atom.exe",
    "sublime_text.exe", "wordpad.exe", "gedit.exe",
})

# {voice phrase: {context: keyboard shortcut}}
# context can be "browser", "explorer", "editor", "any", or a proc name / custom group name.
# Only one command is included by default — add more in the Settings → Commands tab.
DEFAULT_CONTEXT_COMMANDS: dict[str, dict[str, str]] = {
    "screenshot": {"any": "windows+shift+s"},
}


# ── Customisable command words ────────────────────────────────────────────────
DEFAULT_COMMAND_WORDS: dict[str, str] = {
    # Media
    "skip":           "skip",
    "previous":       "restart",
    "rewind":         "rewind",
    "play_pause":     "pause",
    "mute":           "mute",
    # Keyboard
    "copy":           "copy",
    "paste":          "paste",
    "save":           "save",
    "enter":          "enter",
    # App control prefixes
    "open":           "open",
    "close":          "close",
    "minimise":       "minimise",
    "maximise":       "maximise",
    "move":           "move",
    "merge":          "merge",
    # Compound app commands
    "minimise_all":   "minimise all",
    "open_all":       "open all",
    # Engine
    "undo":           "undo",
    "diagnose":       "diagnose",
    "stop_engine":    "close echo",
    "restart_engine": "restart echo",
}

# Volume step words → percentage (integer 1-100)
DEFAULT_VOLUME_STEPS: dict[str, int] = {
    "one":   2,
    "two":   4,
    "three": 6,
    "four":  8,
    "five":  10,
}

# ── Defaults (used only on very first run if no config exists) ───────────────
DEFAULT_APPS: dict[str, str] = {
    "files":      r"C:\Windows\explorer.exe",
    "settings":   r"ms-settings:",
    "notepad":    r"C:\Windows\notepad.exe",
    "command":    r"C:\Windows\System32\cmd.exe",
    "calculator": r"calc.exe",
    "paint":      r"mspaint.exe",
    "task manager": r"C:\Windows\System32\Taskmgr.exe",
}

DEFAULT_PROC_NAMES: dict[str, str] = {
    "files":      "explorer.exe",
    "settings":   "SystemSettings.exe",
    "notepad":    "notepad.exe",
    "command":    "cmd.exe",
    "calculator": "Calculator.exe",
    "paint":      "mspaint.exe",
    "task manager": "Taskmgr.exe",
}

# ── Public API ────────────────────────────────────────────────────────────────

def load() -> dict:
    """Load config from disk, creating defaults on first run.

    If the stored VERSION is below RESET_BASELINE the config is backed up and
    wiped — this is the one-time migration for the 1.0.0.0 baseline reset.
    After that, future version increments only add missing keys; nothing is
    ever wiped unless RESET_BASELINE is deliberately bumped again.
    """
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        _write_defaults()
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)

        # ── One-time baseline reset ───────────────────────────────────────
        stored_ver = _parse_version(data.get("VERSION", "0.0.0.0"))
        if stored_ver < RESET_BASELINE:
            try:
                import shutil
                backup = CONFIG_FILE.with_name(
                    f"config.pre-{'_'.join(str(x) for x in RESET_BASELINE)}.json")
                if CONFIG_FILE.exists():
                    shutil.copy2(CONFIG_FILE, backup)
                print(f"  ↻  Config reset to v{APP_VERSION} defaults "
                      f"(old config backed up to {backup.name})")
            except Exception:
                pass
            _write_defaults()
            return load()

        # ── Forward-compat: add any keys added in newer versions ──────────
        changed = False
        for key, default in _schema_defaults().items():
            if key not in data:
                data[key] = default
                changed = True
        # Ensure the "echo" entry always exists
        vc_exe = str(_exe_dir() / "Echo.exe")
        if "echo" not in data.get("APPS", {}):
            data.setdefault("APPS", {})["echo"] = vc_exe
            data.setdefault("PROC_NAMES", {})["echo"] = "Echo.exe"
            changed = True
        # Fix legacy wrong proc name for settings
        if data.get("PROC_NAMES", {}).get("settings") == "ms-settings:":
            data["PROC_NAMES"]["settings"] = "SystemSettings.exe"
            changed = True
        # Remove any blank command words so defaults are used instead
        cw = data.get("COMMAND_WORDS", {})
        cleaned = {k: v for k, v in cw.items() if v and v.strip()}
        if cleaned != cw:
            data["COMMAND_WORDS"] = cleaned
            changed = True
        if changed:
            save(data)
        return data
    except (json.JSONDecodeError, OSError):
        # Back up the broken file so the user can recover it, then write
        # clean defaults.  Never silently discard a potentially good file.
        try:
            bad = CONFIG_FILE.with_suffix(".broken")
            if CONFIG_FILE.exists():
                CONFIG_FILE.rename(bad)
        except Exception:
            pass
        _write_defaults()
        return load()


def save(data: dict) -> None:
    """Write config back to disk atomically.

    Writes to a .tmp file first, then renames over the real config so a crash
    or power loss mid-write can never corrupt the saved config and cause all
    user apps (Steam games etc.) to be lost.
    """
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(CONFIG_FILE)   # atomic on the same filesystem


def get_apps() -> dict[str, str]:
    return load().get("APPS", DEFAULT_APPS)


def get_proc_names() -> dict[str, str]:
    return load().get("PROC_NAMES", DEFAULT_PROC_NAMES)


def get_model_path() -> str:
    """Return the absolute model path.

    The value stored in config is either:
    - An absolute path the user browsed to manually, OR
    - A bare folder name (e.g. 'vosk-model-small-en-us-0.15') treated as
      relative to the exe/script directory — works for everyone who puts
      the model next to the exe.
    """
    raw = load().get("MODEL_PATH", DEFAULT_MODEL_FOLDER)
    p = pathlib.Path(raw)
    if p.is_absolute():
        return str(p)
    # Relative — resolve next to the exe
    resolved = _exe_dir() / p
    return str(resolved)


def set_model_path(path: str) -> None:
    data = load()
    data["MODEL_PATH"] = path
    save(data)


def get_close_delay() -> int:
    return int(load().get("CLOSE_DELAY", 5))

def set_close_delay(seconds: int) -> None:
    data = load()
    data["CLOSE_DELAY"] = max(1, int(seconds))
    save(data)

def get_command_words() -> dict[str, str]:
    stored = load().get("COMMAND_WORDS", {})
    # Ignore any blank/empty stored values so defaults always show for unset keys
    non_empty = {k: v for k, v in stored.items() if v and v.strip()}
    return {**DEFAULT_COMMAND_WORDS, **non_empty}

def set_command_words(words: dict[str, str]) -> None:
    data = load()
    data["COMMAND_WORDS"] = words
    save(data)

def get_volume_steps() -> dict[str, int]:
    stored = load().get("VOLUME_STEPS", {})
    return {**DEFAULT_VOLUME_STEPS, **stored}

def set_volume_steps(steps: dict[str, int]) -> None:
    data = load()
    data["VOLUME_STEPS"] = steps
    save(data)

def get_confidence_threshold() -> float:
    return float(load().get("CONFIDENCE_THRESHOLD", 0.65))

def set_confidence_threshold(value: float) -> None:
    data = load()
    data["CONFIDENCE_THRESHOLD"] = round(max(0.0, min(1.0, value)), 2)
    save(data)

def get_cooldown() -> float:
    return float(load().get("COOLDOWN", 1.5))

def set_cooldown(value: float) -> None:
    data = load()
    data["COOLDOWN"] = round(max(0.0, value), 1)
    save(data)

def get_dual_model_check() -> bool:
    """Whether to load the small model alongside the main model to filter
    hallucinated leading words (noise at the start of a command)."""
    return bool(load().get("DUAL_MODEL_CHECK", True))

def set_dual_model_check(enabled: bool) -> None:
    data = load()
    data["DUAL_MODEL_CHECK"] = bool(enabled)
    save(data)

def get_overlay_enabled() -> bool:
    return bool(load().get("OVERLAY_ENABLED", True))

def set_overlay_enabled(enabled: bool) -> None:
    data = load()
    data["OVERLAY_ENABLED"] = bool(enabled)
    save(data)

def get_overlay_position() -> str:
    return load().get("OVERLAY_POSITION", "bottom-right")

def set_overlay_position(pos: str) -> None:
    data = load()
    data["OVERLAY_POSITION"] = pos
    save(data)

def get_scan_folders() -> list[str]:
    return load().get("SCAN_FOLDERS", [])

def set_scan_folders(folders: list[str]) -> None:
    data = load()
    data["SCAN_FOLDERS"] = folders
    save(data)

def get_context_commands() -> dict[str, dict[str, str]]:
    stored = load().get("CONTEXT_COMMANDS", {})
    merged = {**DEFAULT_CONTEXT_COMMANDS}
    merged.update(stored)   # user additions/overrides on top
    return merged

def set_context_commands(cmds: dict[str, dict[str, str]]) -> None:
    data = load()
    data["CONTEXT_COMMANDS"] = cmds
    save(data)

def get_custom_groups() -> dict[str, list[str]]:
    """Returns {group_name: [proc_name, ...]}  e.g. {"music": ["spotify.exe","chrome.exe"]}"""
    return load().get("CUSTOM_GROUPS", {})

def set_custom_groups(groups: dict[str, list[str]]) -> None:
    data = load()
    data["CUSTOM_GROUPS"] = groups
    save(data)

def get_spoken_names() -> dict[str, str]:
    """Returns {display_name: spoken_name}  e.g. {"aseprite": "ace sprite"}"""
    return load().get("SPOKEN_NAMES", {})

def set_spoken_names(names: dict[str, str]) -> None:
    data = load()
    data["SPOKEN_NAMES"] = {k: v for k, v in names.items() if v and v.strip()}
    save(data)

def set_spoken_name(display_name: str, spoken: str) -> None:
    """Set or clear a single spoken name entry."""
    names = get_spoken_names()
    if spoken and spoken.strip():
        names[display_name] = spoken.strip().lower()
    else:
        names.pop(display_name, None)
    data = load()
    data["SPOKEN_NAMES"] = names
    save(data)


def add_entry(name: str, path: str, proc: str) -> None:
    data = load()
    data["APPS"][name]       = path
    data["PROC_NAMES"][name] = proc
    save(data)


def delete_entry(name: str) -> None:
    data = load()
    data["APPS"].pop(name, None)
    data["PROC_NAMES"].pop(name, None)
    save(data)


def config_path() -> pathlib.Path:
    return CONFIG_FILE


# ── Internal ──────────────────────────────────────────────────────────────────

def _auto_detect_model() -> str:
    """Return the bare folder name of the first vosk-model* folder found next
    to the exe/script, or the default name if nothing is found yet."""
    base = _exe_dir()
    for p in base.iterdir():
        if p.is_dir() and p.name.startswith("vosk-model"):
            return p.name
    return DEFAULT_MODEL_FOLDER


def _schema_defaults() -> dict:
    vc_exe = str(_exe_dir() / "Echo.exe")
    apps  = {**DEFAULT_APPS,       "echo": vc_exe}
    procs = {**DEFAULT_PROC_NAMES, "echo": "Echo.exe"}
    return {
        "APPS":                 apps,
        "PROC_NAMES":           procs,
        "MODEL_PATH":           _auto_detect_model(),
        "CLOSE_DELAY":          5,
        "CONFIDENCE_THRESHOLD": 0.65,
        "COOLDOWN":             1.5,
        "COMMAND_WORDS":        DEFAULT_COMMAND_WORDS.copy(),
        "VOLUME_STEPS":         DEFAULT_VOLUME_STEPS.copy(),
        "CONTEXT_COMMANDS":     {},   # empty = use all defaults
        "SCAN_FOLDERS":         [],
        "CUSTOM_GROUPS":        {},   # {group_name: [proc_name, ...]}
        "SPOKEN_NAMES":         {},   # {display_name: spoken_name}
        "DUAL_MODEL_CHECK":     True, # run small model alongside main to strip noise words
        "OVERLAY_ENABLED":      True,
        "OVERLAY_POSITION":     "near cursor",
        "VERSION":              APP_VERSION,
    }


def _write_defaults() -> None:
    save(_schema_defaults())
