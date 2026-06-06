"""
Manages per-user config stored in %APPDATA%/VoiceCommands/config.json.
This file is NEVER overwritten by updates — each user keeps their own entries.
"""
import json
import os
import pathlib
import sys

APPDATA_DIR = pathlib.Path(os.getenv("APPDATA", "~")) / "VoiceCommands"
CONFIG_FILE = APPDATA_DIR / "config.json"

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

# ── Customisable command words ────────────────────────────────────────────────
DEFAULT_COMMAND_WORDS: dict[str, str] = {
    "skip":           "skip",
    "previous":       "restart",
    "rewind":         "rewind",
    "play_pause":     "pause",
    "mute":           "mute",
    "copy":           "copy",
    "paste":          "paste",
    "save":           "save",
    "enter":          "enter",
    "undo":           "undo",
    "diagnose":       "diagnose",
    "minimise_all":   "minimise all",
    "open_all":       "open all",
    "stop_engine":    "close voice commands",
    "restart_engine": "restart voice commands",
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
    "firefox":  r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "steam":    r"C:\Program Files (x86)\Steam\steam.exe",
    "files":    r"C:\Windows\explorer.exe",
    "spotify":  r"C:\Users\Default\AppData\Roaming\Spotify\Spotify.exe",
    "discord":  r"C:\Users\Default\AppData\Local\Discord\Discord.exe",
    "command":  r"C:\Windows\System32\cmd.exe",
    "settings": r"ms-settings:",
}

DEFAULT_PROC_NAMES: dict[str, str] = {
    "firefox":  "firefox.exe",
    "steam":    "steam.exe",
    "files":    "explorer.exe",
    "spotify":  "spotify.exe",
    "discord":  "discord.exe",
    "command":  "cmd.exe",
    "settings": "SystemSettings.exe",
}

# ── Public API ────────────────────────────────────────────────────────────────

def load() -> dict:
    """Load config from disk, creating defaults on first run."""
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        _write_defaults()
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all expected top-level keys exist (forward-compat for older configs)
        changed = False
        for key, default in _schema_defaults().items():
            if key not in data:
                data[key] = default
                changed = True
        # Ensure the "voice commands" entry always exists (added in a later version)
        vc_exe = str(_exe_dir() / "VoiceCommands.exe")
        if "voice commands" not in data.get("APPS", {}):
            data.setdefault("APPS", {})["voice commands"] = vc_exe
            data.setdefault("PROC_NAMES", {})["voice commands"] = "VoiceCommands.exe"
            changed = True
        # Fix legacy wrong proc name for settings
        if data.get("PROC_NAMES", {}).get("settings") == "ms-settings:":
            data["PROC_NAMES"]["settings"] = "SystemSettings.exe"
            changed = True
        if changed:
            save(data)
        return data
    except (json.JSONDecodeError, OSError):
        _write_defaults()
        return load()


def save(data: dict) -> None:
    """Write config back to disk."""
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


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
    return {**DEFAULT_COMMAND_WORDS, **stored}

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
    vc_exe = str(_exe_dir() / "VoiceCommands.exe")
    apps  = {**DEFAULT_APPS,       "voice commands": vc_exe}
    procs = {**DEFAULT_PROC_NAMES, "voice commands": "VoiceCommands.exe"}
    return {
        "APPS":        apps,
        "PROC_NAMES":  procs,
        "MODEL_PATH":  _auto_detect_model(),
        "CLOSE_DELAY": 5,   # seconds before a pending close is committed
    }


def _write_defaults() -> None:
    save(_schema_defaults())
