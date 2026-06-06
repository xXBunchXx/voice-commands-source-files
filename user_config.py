"""
Manages per-user config stored in %APPDATA%\VoiceCommands\config.json.
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
    return pathlib.Path(sys.executable).parent

# The model folder name stored in config (relative, no leading path).
# Resolved against the exe directory at runtime so it works on any machine.
DEFAULT_MODEL_FOLDER = "vosk-model-small-en-us-0.15"

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
    "settings": "ms-settings:",
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
        # Ensure all expected keys exist (forward-compat for older configs)
        changed = False
        for key, default in _schema_defaults().items():
            if key not in data:
                data[key] = default
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

def _schema_defaults() -> dict:
    return {
        "APPS":       DEFAULT_APPS,
        "PROC_NAMES": DEFAULT_PROC_NAMES,
        "MODEL_PATH": DEFAULT_MODEL_FOLDER,   # bare folder name — resolved at runtime
    }


def _write_defaults() -> None:
    save(_schema_defaults())
