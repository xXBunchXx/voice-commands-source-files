"""
Manages per-user config stored in ~/.config/Echo/config.json on Linux.
"""
import json
import os
import pathlib
import sys

# XDG-compliant config location
_XDG_CONFIG = pathlib.Path(os.getenv("XDG_CONFIG_HOME", "~/.config")).expanduser()
APPDATA_DIR = _XDG_CONFIG / "Echo"
CONFIG_FILE = APPDATA_DIR / "config.json"

# ── Version ───────────────────────────────────────────────────────────────────
APP_VERSION = "1.1.2.1"
RESET_BASELINE = (1, 0, 0, 0)


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0, 0, 0, 0)


def _exe_dir() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).parent
    return pathlib.Path(__file__).resolve().parent


DEFAULT_MODEL_FOLDER = "vosk-model-small-en-us-0.15"

# ── Context command system ────────────────────────────────────────────────────
BROWSER_PROCS: frozenset = frozenset({
    "chrome", "firefox", "chromium", "opera", "brave", "vivaldi",
    "waterfox", "librewolf", "epiphany", "midori",
    # some distros append -bin
    "chrome-bin", "firefox-bin", "chromium-bin", "brave-bin",
})
EXPLORER_PROCS: frozenset = frozenset({
    "nautilus", "dolphin", "thunar", "nemo", "pcmanfm", "konqueror",
})
EDITOR_PROCS: frozenset = frozenset({
    "code", "gedit", "kate", "atom", "sublime_text", "subl",
    "notepadqq", "mousepad", "pluma", "kwrite", "nvim", "vim",
})

DEFAULT_CONTEXT_COMMANDS: dict[str, dict[str, str]] = {
    "screenshot": {"any": "super+shift+s"},
}

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
    "open":           "open",
    "close":          "close",
    "minimise":       "minimise",
    "maximise":       "maximise",
    "move":           "move",
    "merge":          "merge",
    "switch_audio":   "change to",
    "minimise_all":   "minimise all",
    "open_all":       "open all",
    "set_mode":       "set mode",
    "undo":           "undo",
    "diagnose":       "diagnose",
    "stop_engine":    "close echo",
    "restart_engine": "restart echo",
}

MODE_GROUPS: tuple = ("media", "keyboard", "apps", "layouts", "audio")

DEFAULT_VOLUME_STEPS: dict[str, int] = {
    "one":   2,
    "two":   4,
    "three": 6,
    "four":  8,
    "five":  10,
}

# Linux default apps
DEFAULT_APPS: dict[str, str] = {
    "files":      "nautilus",
    "settings":   "gnome-control-center",
    "terminal":   "gnome-terminal",
    "text editor": "gedit",
    "calculator": "gnome-calculator",
    "task manager": "gnome-system-monitor",
}

DEFAULT_PROC_NAMES: dict[str, str] = {
    "files":      "nautilus",
    "settings":   "gnome-control-center",
    "terminal":   "gnome-terminal-server",
    "text editor": "gedit",
    "calculator": "gnome-calculator",
    "task manager": "gnome-system-monitor",
}

# ── Public API ────────────────────────────────────────────────────────────────

def load() -> dict:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        _write_defaults()
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        stored_ver = _parse_version(data.get("VERSION", "0.0.0.0"))
        if stored_ver < RESET_BASELINE:
            try:
                import shutil
                backup = CONFIG_FILE.with_name(
                    f"config.pre-{'_'.join(str(x) for x in RESET_BASELINE)}.json")
                if CONFIG_FILE.exists():
                    shutil.copy2(CONFIG_FILE, backup)
            except Exception:
                pass
            _write_defaults()
            return load()
        changed = False
        for key, default in _schema_defaults().items():
            if key not in data:
                data[key] = default
                changed = True
        # Ensure echo entry always exists (points to the script/exe)
        vc_exe = str(_exe_dir() / "echo")
        if "echo" not in data.get("APPS", {}):
            data.setdefault("APPS", {})["echo"] = vc_exe
            data.setdefault("PROC_NAMES", {})["echo"] = "echo"
            changed = True
        cw = data.get("COMMAND_WORDS", {})
        cleaned = {k: v for k, v in cw.items() if v and v.strip()}
        if cleaned != cw:
            data["COMMAND_WORDS"] = cleaned
            changed = True
        if changed:
            save(data)
        return data
    except (json.JSONDecodeError, OSError):
        try:
            bad = CONFIG_FILE.with_suffix(".broken")
            if CONFIG_FILE.exists():
                CONFIG_FILE.rename(bad)
        except Exception:
            pass
        _write_defaults()
        return load()


def save(data: dict) -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(CONFIG_FILE)


def get_apps() -> dict[str, str]:
    return load().get("APPS", DEFAULT_APPS)


def get_proc_names() -> dict[str, str]:
    return load().get("PROC_NAMES", DEFAULT_PROC_NAMES)


def get_model_path() -> str:
    raw = load().get("MODEL_PATH", DEFAULT_MODEL_FOLDER)
    p = pathlib.Path(raw)
    if p.is_absolute():
        return str(p)
    return str(_exe_dir() / p)


def set_model_path(path: str) -> None:
    data = load(); data["MODEL_PATH"] = path; save(data)


def get_close_delay() -> int:
    return int(load().get("CLOSE_DELAY", 5))


def set_close_delay(seconds: int) -> None:
    data = load(); data["CLOSE_DELAY"] = max(1, int(seconds)); save(data)


def get_command_words() -> dict[str, str]:
    stored = load().get("COMMAND_WORDS", {})
    non_empty = {k: v for k, v in stored.items() if v and v.strip()}
    return {**DEFAULT_COMMAND_WORDS, **non_empty}


def set_command_words(words: dict[str, str]) -> None:
    data = load(); data["COMMAND_WORDS"] = words; save(data)


def get_volume_steps() -> dict[str, int]:
    stored = load().get("VOLUME_STEPS", {})
    return {**DEFAULT_VOLUME_STEPS, **stored}


def set_volume_steps(steps: dict[str, int]) -> None:
    data = load(); data["VOLUME_STEPS"] = steps; save(data)


def get_confidence_threshold() -> float:
    return float(load().get("CONFIDENCE_THRESHOLD", 0.65))


def set_confidence_threshold(value: float) -> None:
    data = load()
    data["CONFIDENCE_THRESHOLD"] = round(max(0.0, min(1.0, value)), 2)
    save(data)


def get_cooldown() -> float:
    return float(load().get("COOLDOWN", 1.5))


def set_cooldown(value: float) -> None:
    data = load(); data["COOLDOWN"] = round(max(0.0, value), 1); save(data)


def get_response_delay() -> float:
    return float(load().get("RESPONSE_DELAY", 0.12))


def set_response_delay(value: float) -> None:
    data = load()
    data["RESPONSE_DELAY"] = round(max(0.04, min(1.0, value)), 2)
    save(data)


def get_context_delays() -> dict:
    return load().get("CONTEXT_DELAYS", {})


def set_context_delays(delays: dict) -> None:
    data = load()
    clean = {}
    for k, v in delays.items():
        try:
            ms = int(round(float(v)))
        except (TypeError, ValueError):
            continue
        if k and k.strip() and ms > 0:
            clean[k.strip().lower()] = max(0, min(2000, ms))
    data["CONTEXT_DELAYS"] = clean
    save(data)


def set_context_delay(phrase: str, ms) -> None:
    delays = get_context_delays()
    phrase = (phrase or "").strip().lower()
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        ms = 0
    if phrase:
        if ms > 0:
            delays[phrase] = max(0, min(2000, ms))
        else:
            delays.pop(phrase, None)
    set_context_delays(delays)


def get_audio_devices() -> dict:
    return load().get("AUDIO_DEVICES", {})


def set_audio_devices(devices: dict) -> None:
    data = load()
    data["AUDIO_DEVICES"] = {k.strip().lower(): v
                             for k, v in devices.items() if k and k.strip()}
    save(data)


def get_layouts() -> dict:
    return load().get("LAYOUTS", {})


def get_layout(n) -> list:
    return get_layouts().get(str(n), [])


def set_layout(n, entries) -> None:
    data = load()
    layouts = data.setdefault("LAYOUTS", {})
    layouts[str(n)] = entries
    save(data)


def get_word_delays() -> dict:
    return load().get("WORD_DELAYS", {})


def set_word_delays(delays: dict) -> None:
    data = load()
    clean = {}
    for k, v in delays.items():
        try:
            ms = int(round(float(v)))
        except (TypeError, ValueError):
            continue
        if ms > 0:
            clean[k] = max(0, min(2000, ms))
    data["WORD_DELAYS"] = clean
    save(data)


def get_dual_model_check() -> bool:
    return bool(load().get("DUAL_MODEL_CHECK", True))


def set_dual_model_check(enabled: bool) -> None:
    data = load(); data["DUAL_MODEL_CHECK"] = bool(enabled); save(data)


def get_overlay_enabled() -> bool:
    return bool(load().get("OVERLAY_ENABLED", True))


def set_overlay_enabled(enabled: bool) -> None:
    data = load(); data["OVERLAY_ENABLED"] = bool(enabled); save(data)


def get_overlay_position() -> str:
    return load().get("OVERLAY_POSITION", "bottom-right")


def set_overlay_position(pos: str) -> None:
    data = load(); data["OVERLAY_POSITION"] = pos; save(data)


def get_scan_folders() -> list[str]:
    return load().get("SCAN_FOLDERS", [])


def set_scan_folders(folders: list[str]) -> None:
    data = load(); data["SCAN_FOLDERS"] = folders; save(data)


def get_context_commands() -> dict[str, dict[str, str]]:
    stored = load().get("CONTEXT_COMMANDS", {})
    merged = {**DEFAULT_CONTEXT_COMMANDS}
    merged.update(stored)
    return merged


def set_context_commands(cmds: dict[str, dict[str, str]]) -> None:
    data = load(); data["CONTEXT_COMMANDS"] = cmds; save(data)


def get_modes() -> dict:
    return load().get("MODES", {})


def get_mode(name: str) -> dict:
    if name == "default":
        return {"groups": {g: True for g in MODE_GROUPS},
                "commands": get_context_commands()}
    m = get_modes().get(name, {})
    return {"groups": m.get("groups", {g: False for g in MODE_GROUPS}),
            "commands": m.get("commands", {})}


def save_mode(name: str, groups: dict, commands: dict) -> None:
    name = (name or "").strip().lower()
    if not name or name == "default":
        return
    data = load()
    modes = data.setdefault("MODES", {})
    modes[name] = {"groups": {g: bool(groups.get(g, False)) for g in MODE_GROUPS},
                   "commands": commands or {}}
    save(data)


def delete_mode(name: str) -> None:
    data = load()
    modes = data.get("MODES", {})
    if name in modes:
        del modes[name]
        data["MODES"] = modes
        save(data)


def mode_names() -> list:
    return ["default"] + sorted(get_modes().keys())


def get_custom_groups() -> dict[str, list[str]]:
    return load().get("CUSTOM_GROUPS", {})


def set_custom_groups(groups: dict[str, list[str]]) -> None:
    data = load(); data["CUSTOM_GROUPS"] = groups; save(data)


def get_spoken_names() -> dict[str, str]:
    return load().get("SPOKEN_NAMES", {})


def set_spoken_names(names: dict[str, str]) -> None:
    data = load()
    data["SPOKEN_NAMES"] = {k: v for k, v in names.items() if v and v.strip()}
    save(data)


def set_spoken_name(display_name: str, spoken: str) -> None:
    names = get_spoken_names()
    if spoken and spoken.strip():
        names[display_name] = spoken.strip().lower()
    else:
        names.pop(display_name, None)
    data = load()
    data["SPOKEN_NAMES"] = names
    save(data)


def get_recognition_mode() -> str:
    return load().get("RECOGNITION_MODE", "grammar")


def set_recognition_mode(mode: str) -> None:
    data = load(); data["RECOGNITION_MODE"] = mode; save(data)


def get_llm_url() -> str:
    return load().get("LLM_URL", "http://127.0.0.1:11434")


def get_llm_model() -> str:
    return load().get("LLM_MODEL", "llama3.2:3b")


def set_llm_settings(url: str, model: str) -> None:
    data = load()
    data["LLM_URL"] = url
    data["LLM_MODEL"] = model
    save(data)


def add_entry(name: str, path: str, proc: str) -> None:
    data = load()
    data["APPS"][name] = path
    data["PROC_NAMES"][name] = proc
    save(data)


def delete_entry(name: str) -> None:
    data = load()
    data["APPS"].pop(name, None)
    data["PROC_NAMES"].pop(name, None)
    save(data)


def config_path() -> pathlib.Path:
    return CONFIG_FILE


def _auto_detect_model() -> str:
    base = _exe_dir()
    for p in base.iterdir():
        if p.is_dir() and p.name.startswith("vosk-model"):
            return p.name
    return DEFAULT_MODEL_FOLDER


def _schema_defaults() -> dict:
    vc_exe = str(_exe_dir() / "echo")
    apps  = {**DEFAULT_APPS,  "echo": vc_exe}
    procs = {**DEFAULT_PROC_NAMES, "echo": "echo"}
    return {
        "APPS":                 apps,
        "PROC_NAMES":           procs,
        "MODEL_PATH":           _auto_detect_model(),
        "CLOSE_DELAY":          5,
        "CONFIDENCE_THRESHOLD": 0.65,
        "COOLDOWN":             1.5,
        "RESPONSE_DELAY":       0.12,
        "WORD_DELAYS":          {},
        "CONTEXT_DELAYS":       {},
        "LAYOUTS":              {},
        "AUDIO_DEVICES":        {},
        "COMMAND_WORDS":        DEFAULT_COMMAND_WORDS.copy(),
        "VOLUME_STEPS":         DEFAULT_VOLUME_STEPS.copy(),
        "CONTEXT_COMMANDS":     {},
        "MODES":                {},
        "SCAN_FOLDERS":         [],
        "CUSTOM_GROUPS":        {},
        "SPOKEN_NAMES":         {},
        "DUAL_MODEL_CHECK":     True,
        "OVERLAY_ENABLED":      True,
        "OVERLAY_POSITION":     "bottom-right",
        "RECOGNITION_MODE":     "grammar",
        "LLM_URL":              "http://127.0.0.1:11434",
        "LLM_MODEL":            "llama3.2:3b",
        "VERSION":              APP_VERSION,
    }


def _write_defaults() -> None:
    save(_schema_defaults())
