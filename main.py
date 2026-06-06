"""
VoiceCommands — main launcher.
"""
import io
import os
import sys
import pathlib
import queue
import subprocess
import ssl
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import urllib.request
import zipfile

import user_config

VERSION = "1.0.0"

GITHUB_RAW     = "https://raw.githubusercontent.com/xXBunchXx/Voice-commands/main/"
GITHUB_EXE_URL = "https://github.com/xXBunchXx/Voice-commands/raw/main/dist/VoiceCommands.exe"

MODEL_NAME    = "vosk-model-small-en-us-0.15"
MODEL_ZIP_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"

# ── Log queue — voice engine writes here, UI reads it ─────────────────────────

_log_queue: queue.Queue = queue.Queue()


class _QueueWriter:
    """Replaces sys.stdout so print() in voice_controls shows in the debug panel."""
    def __init__(self, q: queue.Queue, original):
        self._q        = q
        self._original = original

    def write(self, text: str):
        if text.strip():
            self._q.put(text)
        if self._original:
            try:
                self._original.write(text)
            except Exception:
                pass

    def flush(self):
        if self._original:
            try:
                self._original.flush()
            except Exception:
                pass

    def reconfigure(self, **kw):
        if self._original and hasattr(self._original, "reconfigure"):
            self._original.reconfigure(**kw)


# ── SSL context ───────────────────────────────────────────────────────────────

def _ssl_ctx():
    """SSL context that works frozen and in dev. Falls back to no-verify."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        return ctx


def _urlopen(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers={"User-Agent": "VoiceCommands/1.0"})
    return urllib.request.urlopen(req, context=_ssl_ctx(), timeout=timeout)


# ── Model auto-downloader ─────────────────────────────────────────────────────

def _start_model_download(root: tk.Tk, model_var: tk.StringVar,
                           path_lbl: tk.Label, GRN: str, RED: str):
    """
    Called via root.after() so mainloop is already running.
    Shows a progress window and downloads the model in a background thread.
    UI updates happen via root.after() so tkinter never blocks.
    """
    model_dir = pathlib.Path(user_config._exe_dir()) / MODEL_NAME
    if model_dir.is_dir():
        return

    if not messagebox.askyesno(
        "Voice model not found",
        "The speech recognition model (~40 MB) is missing.\n\n"
        "Download it now? This only happens once.",
        parent=root,
    ):
        return

    # ── Progress window ───────────────────────────────────────────────────────
    prog = tk.Toplevel(root)
    prog.title("Downloading model…")
    prog.resizable(False, False)
    prog.configure(bg="#1e1e2e")
    prog.grab_set()
    prog.protocol("WM_DELETE_WINDOW", lambda: None)   # prevent closing mid-download

    tk.Label(prog, text="Downloading voice model…",
             bg="#1e1e2e", fg="#cdd6f4",
             font=("Segoe UI", 11)).pack(padx=30, pady=(20, 6))

    detail_var = tk.StringVar(value="Connecting…")
    tk.Label(prog, textvariable=detail_var,
             bg="#1e1e2e", fg="#585b70",
             font=("Segoe UI", 9)).pack(padx=30)

    bar = ttk.Progressbar(prog, length=340, mode="determinate", maximum=100)
    bar.pack(padx=30, pady=(10, 20))

    # ── Background download ───────────────────────────────────────────────────
    dl_queue: queue.Queue = queue.Queue()

    def _download():
        try:
            with _urlopen(MODEL_ZIP_URL, timeout=120) as resp:
                total      = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                buf        = io.BytesIO()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    buf.write(chunk)
                    downloaded += len(chunk)
                    pct = (downloaded / total * 100) if total else 0
                    mb  = downloaded / 1_048_576
                    dl_queue.put(("progress", pct, f"{mb:.1f} MB / {total/1_048_576:.0f} MB"))

            dl_queue.put(("extracting",))
            buf.seek(0)
            dest = pathlib.Path(user_config._exe_dir())
            with zipfile.ZipFile(buf) as zf:
                zf.extractall(dest)

            user_config.set_model_path(MODEL_NAME)
            dl_queue.put(("done",))
        except Exception as e:
            dl_queue.put(("error", str(e)))

    threading.Thread(target=_download, daemon=True).start()

    # ── Poll queue, update UI via after() — no blocking calls ─────────────────
    def _poll():
        try:
            while True:
                msg = dl_queue.get_nowait()
                if msg[0] == "progress":
                    bar["value"] = msg[1]
                    detail_var.set(msg[2])
                elif msg[0] == "extracting":
                    bar["value"] = 100
                    detail_var.set("Extracting… please wait")
                elif msg[0] == "done":
                    prog.destroy()
                    new = user_config.get_model_path()
                    model_var.set(new)
                    path_lbl.config(fg=GRN if pathlib.Path(new).is_dir() else RED)
                    _log_queue.put("✓  Model downloaded and ready.\n")
                    return
                elif msg[0] == "error":
                    prog.destroy()
                    messagebox.showerror(
                        "Download failed",
                        f"{msg[1]}\n\nUse the Browse… button to locate the model manually.",
                        parent=root,
                    )
                    return
        except queue.Empty:
            pass
        root.after(200, _poll)

    root.after(200, _poll)


# ── Update helpers ─────────────────────────────────────────────────────────────

def _fetch_latest_version() -> str | None:
    try:
        with _urlopen(GITHUB_RAW + "version.txt") as r:
            return r.read().decode().strip()
    except Exception as e:
        _log_queue.put(f"Update check error: {e}\n")
        return None


def _version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def _do_update(root: tk.Tk, status_var: tk.StringVar) -> None:
    exe_path = pathlib.Path(sys.executable)
    new_exe  = exe_path.with_name("VoiceCommands_new.exe")

    def _download():
        try:
            root.after(0, lambda: status_var.set("Downloading update…"))
            urllib.request.urlretrieve(GITHUB_EXE_URL, new_exe)
            bat = exe_path.with_name("_vc_updater.bat")
            bat.write_text(
                f'@echo off\ntimeout /t 2 /nobreak >nul\n'
                f'move /Y "{new_exe}" "{exe_path}"\n'
                f'start "" "{exe_path}"\ndel "%~f0"\n',
                encoding="ascii",
            )
            subprocess.Popen(["cmd", "/c", str(bat)],
                             creationflags=subprocess.CREATE_NO_WINDOW)
            root.after(0, root.destroy)
        except Exception as e:
            root.after(0, lambda: messagebox.showerror(
                "Update failed", str(e), parent=root))
            root.after(0, lambda: status_var.set("○ Stopped"))

    threading.Thread(target=_download, daemon=True).start()


# ── Voice engine ───────────────────────────────────────────────────────────────

_stop_event    = threading.Event()
_engine_thread: threading.Thread | None = None


def _engine_loop(stop_event, root, status_var, b_start, b_stop):
    import voice_controls
    while True:
        stop_event.clear()
        wants_restart = voice_controls.run(stop_event)
        if not wants_restart:
            break
        _log_queue.put("🔄  Restarting engine…\n")
    root.after(0, lambda: _ui_stopped(status_var, b_start, b_stop))


def _ui_stopped(status_var, b_start, b_stop):
    status_var.set("○ Stopped")
    b_start.config(state="normal")
    b_stop.config(state="disabled")


def _start_engine(root, status_var, b_start, b_stop):
    global _stop_event, _engine_thread
    if _engine_thread and _engine_thread.is_alive():
        return

    model_path = user_config.get_model_path()
    if not pathlib.Path(model_path).is_dir():
        messagebox.showerror(
            "Model not found",
            f"Could not find the Vosk model at:\n{model_path}\n\n"
            "Use the Browse… button to point to your model folder.",
            parent=root,
        )
        return

    sys.stdout = _QueueWriter(_log_queue, sys.__stdout__)
    _log_queue.put("▶  Starting engine…\n")
    _log_queue.put(f"   Model : {model_path}\n")
    _log_queue.put(f"   Apps  : {', '.join(user_config.get_apps().keys())}\n\n")

    _stop_event    = threading.Event()
    _engine_thread = threading.Thread(
        target=_engine_loop,
        args=(_stop_event, root, status_var, b_start, b_stop),
        daemon=True,
    )
    _engine_thread.start()
    status_var.set("● Running")
    b_start.config(state="disabled")
    b_stop.config(state="normal")


def _stop_engine(status_var, b_start, b_stop):
    _stop_event.set()
    _ui_stopped(status_var, b_start, b_stop)


# ── App Manager ────────────────────────────────────────────────────────────────

def _open_manager(root):
    from manage_apps import AppManagerWindow
    win = AppManagerWindow(root)
    win.grab_set()
    win.focus_set()


# ── Update check UI ────────────────────────────────────────────────────────────

def _check_updates_ui(root, status_var):
    status_var.set("Checking for updates…")
    root.update()
    latest = _fetch_latest_version()
    if latest is None:
        messagebox.showinfo("Update check failed",
                            "Could not reach GitHub.\n\nCheck your internet connection.",
                            parent=root)
        status_var.set("○ Stopped")
        return
    if _version_tuple(latest) > _version_tuple(VERSION):
        if messagebox.askyesno("Update available",
                               f"Version {latest} is available (you have {VERSION}).\n\nInstall now?",
                               parent=root):
            _do_update(root, status_var)
        else:
            status_var.set("○ Stopped")
    else:
        messagebox.showinfo("Up to date", f"You're on the latest version ({VERSION}).", parent=root)
        status_var.set("○ Stopped")


# ── Main window ───────────────────────────────────────────────────────────────

def main():
    BG    = "#1e1e2e"
    CARD  = "#2a2a3e"
    ACC   = "#7c6af7"
    FG    = "#cdd6f4"
    GRN   = "#a6e3a1"
    RED   = "#f38ba8"
    MUTED = "#585b70"
    LOG_BG = "#11111b"

    root = tk.Tk()
    root.title(f"Voice Commands  v{VERSION}")
    root.configure(bg=BG)
    root.resizable(True, True)

    def mkbtn(parent, text, cmd, color=ACC, state="normal", width=22):
        return tk.Button(parent, text=text, command=cmd,
                         bg=color, fg="#ffffff", activebackground=color,
                         activeforeground="#ffffff", relief="flat",
                         font=("Segoe UI Semibold", 10),
                         padx=14, pady=7, cursor="hand2", bd=0,
                         state=state, width=width)

    # Header
    hdr = tk.Frame(root, bg=ACC, pady=10)
    hdr.pack(fill="x")
    tk.Label(hdr, text="🎙  Voice Commands", bg=ACC, fg="#ffffff",
             font=("Segoe UI Semibold", 14)).pack()
    tk.Label(hdr, text=f"v{VERSION}", bg=ACC, fg="#c8b8ff",
             font=("Segoe UI", 9)).pack()

    # Status
    card = tk.Frame(root, bg=CARD, padx=20, pady=12)
    card.pack(fill="x", padx=16, pady=(14, 0))
    status_var = tk.StringVar(value="○ Stopped")
    tk.Label(card, textvariable=status_var, bg=CARD, fg=GRN,
             font=("Segoe UI Semibold", 13)).pack()

    # Buttons
    btns = tk.Frame(root, bg=BG, pady=4)
    btns.pack(fill="x", padx=16)

    b_start = mkbtn(btns, "▶  Start Voice Commands", lambda: None)
    b_stop  = mkbtn(btns, "■  Stop Voice Commands",
                    lambda: _stop_engine(status_var, b_start, b_stop),
                    color=MUTED, state="disabled")
    b_start.config(command=lambda: _start_engine(root, status_var, b_start, b_stop))
    b_apps = mkbtn(btns, "⚙  Manage Apps",      lambda: _open_manager(root))
    b_upd  = mkbtn(btns, "🔄  Check for Updates",
                   lambda: _check_updates_ui(root, status_var), color=MUTED)

    for b in (b_start, b_stop, b_apps, b_upd):
        b.pack(pady=3, fill="x")

    # Model path row
    model_row = tk.Frame(root, bg=CARD, padx=12, pady=8)
    model_row.pack(fill="x", padx=16, pady=(10, 0))
    tk.Label(model_row, text="Vosk model path:", bg=CARD, fg=FG,
             font=("Segoe UI", 9)).pack(anchor="w")
    path_row = tk.Frame(model_row, bg=CARD)
    path_row.pack(fill="x", pady=(3, 0))

    model_var = tk.StringVar(value=user_config.get_model_path())
    exists    = pathlib.Path(model_var.get()).is_dir()
    path_lbl  = tk.Label(path_row, textvariable=model_var, bg=CARD,
                         fg=GRN if exists else RED,
                         font=("Consolas", 8), anchor="w",
                         wraplength=310, justify="left")
    path_lbl.pack(side="left", fill="x", expand=True)

    def _pick_model():
        chosen = filedialog.askdirectory(title="Select Vosk model folder", parent=root)
        if chosen:
            user_config.set_model_path(chosen)
            model_var.set(chosen)
            path_lbl.config(fg=GRN if pathlib.Path(chosen).is_dir() else RED)

    mkbtn(path_row, "Browse…", _pick_model, color=MUTED, width=8).pack(
        side="right", padx=(6, 0))

    # Debug log panel
    debug_frame = tk.Frame(root, bg=BG)
    debug_frame.pack(fill="both", expand=True, padx=16, pady=(10, 0))

    debug_header = tk.Frame(debug_frame, bg=BG)
    debug_header.pack(fill="x")
    tk.Label(debug_header, text="Debug log", bg=BG, fg=MUTED,
             font=("Segoe UI Semibold", 9)).pack(side="left")

    def _clear_log():
        log_box.config(state="normal")
        log_box.delete("1.0", "end")
        log_box.config(state="disabled")

    mkbtn(debug_header, "Clear", _clear_log, color=MUTED, width=6).pack(side="right")

    log_box = scrolledtext.ScrolledText(
        debug_frame, height=14, wrap="word",
        bg=LOG_BG, fg=FG, font=("Consolas", 9),
        relief="flat", bd=0, state="disabled",
    )
    log_box.pack(fill="both", expand=True, pady=(4, 0))
    log_box.tag_config("hear",  foreground="#89b4fa")
    log_box.tag_config("cmd",   foreground="#a6e3a1")
    log_box.tag_config("low",   foreground="#f9e2af")
    log_box.tag_config("info",  foreground="#cdd6f4")
    log_box.tag_config("error", foreground="#f38ba8")
    log_box.tag_config("muted", foreground="#585b70")

    def _tag_for(text: str) -> str:
        t = text.lower()
        if "hearing:" in t or "👂" in t:       return "hear"
        if "command:" in t:                     return "cmd"
        if "low confidence" in t or "💤" in t:  return "low"
        if any(x in t for x in ("error", "failed", "traceback", "exception")):
            return "error"
        if any(x in t for x in ("──", "✓", "✗", "win —")):
            return "muted"
        return "info"

    def _append_log(text: str):
        log_box.config(state="normal")
        log_box.insert("end", text.rstrip("\n") + "\n", _tag_for(text))
        log_box.see("end")
        log_box.config(state="disabled")

    def _poll_log():
        try:
            while True:
                _append_log(_log_queue.get_nowait())
        except queue.Empty:
            pass
        root.after(100, _poll_log)

    root.after(100, _poll_log)

    # Footer
    tk.Label(root, text=f"Config: {user_config.config_path()}",
             bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(
        fill="x", padx=16, pady=(6, 8))

    # Log startup info
    _log_queue.put(f"Voice Commands v{VERSION} started\n")
    _log_queue.put(f"Model path : {user_config.get_model_path()}\n")
    _log_queue.put(f"Model found: {pathlib.Path(user_config.get_model_path()).is_dir()}\n")
    _log_queue.put(f"Config     : {user_config.config_path()}\n")
    _log_queue.put(f"Apps       : {', '.join(user_config.get_apps().keys())}\n\n")

    # Trigger model download check after mainloop starts (500ms delay)
    root.after(500, lambda: _start_model_download(root, model_var, path_lbl, GRN, RED))

    # Silent background update check
    def _bg_check():
        latest = _fetch_latest_version()
        if latest and _version_tuple(latest) > _version_tuple(VERSION):
            root.after(0, lambda: status_var.set(f"⬆  Update {latest} available!"))

    threading.Thread(target=_bg_check, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    main()
