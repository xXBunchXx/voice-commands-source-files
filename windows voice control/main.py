"""
Echo — main launcher.
"""
import os
import sys
import json
import pathlib
import queue
import subprocess
import ssl
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import urllib.request

import pystray
from PIL import Image, ImageDraw, ImageTk
import user_config

def _resource_path(name: str) -> pathlib.Path:
    """Return path to a bundled resource whether frozen (PyInstaller) or in dev."""
    if getattr(sys, "frozen", False):
        base = pathlib.Path(sys._MEIPASS)
    else:
        base = pathlib.Path(__file__).resolve().parent
    return base / name


def _read_version() -> str:
    try:
        return _resource_path("version.txt").read_text().strip()
    except Exception:
        return "0.0.0"


def _load_icon() -> Image.Image | None:
    """Load icon.png, auto-crop transparent padding, and return it."""
    try:
        img = Image.open(_resource_path("icon.png")).convert("RGBA")
        bb  = img.getbbox()          # tight bounding box of non-transparent pixels
        if bb:
            img = img.crop(bb)
        return img
    except Exception:
        return None

VERSION = _read_version()

GITHUB_RAW         = "https://raw.githubusercontent.com/xXBunchXx/Voice-commands/main/"
GITHUB_RELEASES    = "https://github.com/xXBunchXx/Voice-commands/releases/download"
GITHUB_API_RELEASES = "https://api.github.com/repos/xXBunchXx/Voice-commands/releases"

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
    req = urllib.request.Request(url, headers={"User-Agent": "Echo/1.0"})
    return urllib.request.urlopen(req, context=_ssl_ctx(), timeout=timeout)


# ── Update helpers ─────────────────────────────────────────────────────────────

def _version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def _fetch_latest_version() -> str | None:
    """Return the highest *published release* version whose last digit is 0.

    Releases are only created for stable builds (A.B.C.0).  Development builds
    (last digit != 0) are never published as releases and can't be downloaded,
    so we ignore them entirely — we query GitHub's Releases API and pick the
    highest tag ending in .0, rather than reading the repo's version.txt (which
    is just whatever was last committed and may be a dev build)."""
    try:
        with _urlopen(GITHUB_API_RELEASES) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        _log_queue.put(f"Update check error: {e}\n")
        return None

    best, best_t = None, None
    for rel in (data if isinstance(data, list) else []):
        if rel.get("draft"):
            continue
        tag = (rel.get("tag_name") or "").lstrip("vV").strip()
        try:
            t = _version_tuple(tag)
        except Exception:
            continue
        if len(t) < 4 or t[-1] != 0:
            continue   # only stable, downloadable .0 releases count
        if best_t is None or t > best_t:
            best, best_t = tag, t
    return best


def _do_update(root: tk.Tk, status_var: tk.StringVar, latest_version: str) -> None:
    exe_url  = f"{GITHUB_RELEASES}/v{latest_version}/Echo.exe"
    exe_path = pathlib.Path(sys.executable)
    new_exe  = exe_path.with_name("Echo_new.exe")

    def _download():
        try:
            root.after(0, lambda: status_var.set("Downloading update…"))
            with _urlopen(exe_url, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536
                with open(new_exe, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = int(downloaded / total * 100)
                            root.after(0, lambda p=pct: status_var.set(
                                f"Downloading… {p}%"))
            if new_exe.stat().st_size < 1_000_000:
                raise RuntimeError("Downloaded file is too small — may be corrupt.")
            bat = exe_path.with_name("_vc_updater.bat")
            bat.write_text(
                f'@echo off\n'
                f':wait\n'
                f'timeout /t 2 /nobreak >nul\n'
                f'move /Y "{new_exe}" "{exe_path}" >nul 2>&1\n'
                f'if errorlevel 1 goto wait\n'
                f'timeout /t 1 /nobreak >nul\n'
                f'start "" "{exe_path}"\n'
                f'del "%~f0"\n',
                encoding="ascii",
            )
            subprocess.Popen(["cmd", "/c", str(bat)],
                             creationflags=subprocess.CREATE_NO_WINDOW)
            root.after(0, root.destroy)
        except Exception as e:
            _err = str(e)
            root.after(0, lambda: messagebox.showerror(
                "Update failed", _err, parent=root))
            root.after(0, lambda: status_var.set("○ Stopped"))

    threading.Thread(target=_download, daemon=True).start()


# ── Voice engine ───────────────────────────────────────────────────────────────

_stop_event    = threading.Event()
_engine_thread: threading.Thread | None = None


def _engine_loop(stop_event, root, status_var, b_start, b_stop):
    try:
        _log_queue.put("   Importing voice engine…\n")
        import voice_controls
        import importlib
        importlib.reload(voice_controls)
        if _overlay:
            voice_controls._status_cb = lambda msg: root.after(0, _overlay.show, msg)

        # Let the engine control Echo's own window through Tk (keeps the GUI
        # painting correctly when you say "minimise echo" / "open echo").
        def _self_window(action):
            def _do():
                try:
                    if action == "minimise":
                        root.iconify()
                    else:  # restore / focus
                        root.deiconify()
                        root.lift()
                        root.attributes("-topmost", True)
                        root.after(300, lambda: root.attributes("-topmost", False))
                except Exception:
                    pass
            root.after(0, _do)
        voice_controls._self_window_cb = _self_window

        _log_queue.put("   Voice engine loaded — starting loop\n\n")
        while True:
            stop_event.clear()
            wants_restart = voice_controls.run(stop_event)
            if not wants_restart:
                break
            _log_queue.put("🔄  Restarting engine…\n")
    except Exception as e:
        import traceback
        _log_queue.put(f"\n❌  ENGINE CRASHED:\n{traceback.format_exc()}\n")
        _err = str(e)
        root.after(0, lambda: messagebox.showerror(
            "Engine error",
            f"The voice engine crashed:\n\n{_err}\n\nCheck the debug log for details.",
            parent=root,
        ))
    finally:
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


def _restart_engine_ui(root, status_var, b_start, b_stop):
    """Stop the engine (if running) and start it again once it has fully exited."""
    th = _engine_thread
    if th and th.is_alive():
        status_var.set("↻ Restarting…")
        _stop_event.set()

        def _wait_then_start():
            th.join(timeout=8)
            root.after(0, lambda: _start_engine(root, status_var, b_start, b_stop))
        threading.Thread(target=_wait_then_start, daemon=True).start()
    else:
        _start_engine(root, status_var, b_start, b_stop)


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
            _do_update(root, status_var, latest)
        else:
            status_var.set("○ Stopped")
    else:
        messagebox.showinfo("Up to date", f"You're on the latest version ({VERSION}).", parent=root)
        status_var.set("○ Stopped")


# ── System tray ───────────────────────────────────────────────────────────────

_tray_icon: pystray.Icon | None = None


def _make_tray_image() -> Image.Image:
    icon = _load_icon()
    if icon is not None:
        icon = icon.resize((85, 85), Image.LANCZOS)
        return icon
    # Fallback: simple drawn icon
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill="#1a56db")
    d.rounded_rectangle([22, 10, 42, 38], radius=8, fill="white")
    d.line([(32, 38), (32, 50)], fill="white", width=3)
    d.line([(22, 50), (42, 50)], fill="white", width=3)
    return img


def _setup_tray(root: tk.Tk) -> pystray.Icon:
    def _on_show(icon, item):
        root.after(0, _show_window)

    def _on_exit(icon, item):
        icon.stop()
        root.after(0, _quit_app)

    menu = pystray.Menu(
        pystray.MenuItem("Show Echo", _on_show, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", _on_exit),
    )
    icon = pystray.Icon("Echo", _make_tray_image(),
                        "Echo", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


def _hide_window(root: tk.Tk) -> None:
    global _tray_icon
    root.withdraw()
    if _tray_icon is None:
        _tray_icon = _setup_tray(root)


def _show_window() -> None:
    if _root_ref is not None:
        _root_ref.deiconify()
        _root_ref.lift()
        _root_ref.focus_force()


def _quit_app() -> None:
    global _tray_icon
    _stop_event.set()
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None
    if _root_ref is not None:
        _root_ref.destroy()


_root_ref: tk.Tk | None = None


# ── Status overlay ─────────────────────────────────────────────────────────────

class StatusOverlay:
    """Small always-on-top HUD that briefly shows what the engine just did."""

    def __init__(self, root: tk.Tk):
        self._root     = root
        self._win      = None
        self._label    = None
        self._after_id = None

    def show(self, text: str) -> None:
        if not user_config.get_overlay_enabled():
            return
        if self._after_id:
            self._root.after_cancel(self._after_id)
            self._after_id = None
        if self._win is None or not self._win.winfo_exists():
            self._build()
        self._label.config(text=f"🎙  {text}")
        self._reposition()
        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        self._after_id = self._root.after(2000, self._hide)

    def _hide(self) -> None:
        self._after_id = None
        if self._win and self._win.winfo_exists():
            self._win.withdraw()

    def _build(self) -> None:
        self._win = tk.Toplevel(self._root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.attributes("-alpha", 0.90)
        self._win.configure(bg="#0f1a2e")
        self._win.withdraw()
        outer = tk.Frame(self._win, bg="#1a56db", padx=2, pady=2)
        outer.pack()
        inner = tk.Frame(outer, bg="#0a1020", padx=20, pady=10)
        inner.pack()
        self._label = tk.Label(
            inner, text="", bg="#0a1020", fg="#ffffff",
            font=("Segoe UI Semibold", 12),
        )
        self._label.pack()
        for w in (self._win, outer, inner, self._label):
            w.bind("<Button-1>", lambda _e: self._hide())

    @staticmethod
    def _work_area() -> tuple[int, int, int, int]:
        try:
            import ctypes, ctypes.wintypes
            rc = ctypes.wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0)
            return rc.left, rc.top, rc.right, rc.bottom
        except Exception:
            import tkinter as _tk
            sw = _tk.Tk.winfo_screenwidth(_tk.Tk())
            sh = _tk.Tk.winfo_screenheight(_tk.Tk())
            return 0, 0, sw, sh

    def _reposition(self) -> None:
        self._win.update_idletasks()
        ow = self._win.winfo_reqwidth()
        oh = self._win.winfo_reqheight()
        wl, wt, wr, wb = self._work_area()
        pad = 16
        pos = user_config.get_overlay_position()
        options = {
            "top-left":      (wl + pad,           wt + pad),
            "top-center":    (wl + (wr-wl-ow)//2, wt + pad),
            "top-right":     (wr - ow - pad,       wt + pad),
            "bottom-left":   (wl + pad,            wb - oh - pad),
            "bottom-center": (wl + (wr-wl-ow)//2, wb - oh - pad),
            "bottom-right":  (wr - ow - pad,       wb - oh - pad),
        }
        x, y = options.get(pos, options["bottom-right"])
        self._win.geometry(f"+{x}+{y}")


_overlay: StatusOverlay | None = None


# ── Main window ───────────────────────────────────────────────────────────────

def main():
    BG     = "#0a1020"
    CARD   = "#0f1a2e"
    ACC    = "#1a56db"
    FG     = "#ffffff"
    GRN    = "#4ade80"
    RED    = "#f87171"
    MUTED  = "#3d5470"
    LOG_BG = "#060c17"

    global _root_ref, _overlay
    root = tk.Tk()
    _root_ref = root
    _overlay  = StatusOverlay(root)
    root.title(f"Echo  v{VERSION}")
    root.configure(bg=BG)
    root.geometry("960x740")

    # Dark title bar and border (Windows 11 DWM API)
    def _apply_dark_titlebar():
        try:
            import ctypes
            root.update()   # ensure the window is fully realised before grabbing hwnd
            hwnd = ctypes.windll.user32.FindWindowW(None, root.title())
            if not hwnd:
                # fallback: walk up from the inner tk frame handle
                hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)
            # Colour format: 0x00BBGGRR
            # Title bar #1a2840 → R=0x1a G=0x28 B=0x40 → 0x0040281a
            caption_col = ctypes.c_uint(0x0040281a)
            # Border matches title bar
            border_col  = ctypes.c_uint(0x0040281a)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(caption_col), ctypes.sizeof(caption_col))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 34, ctypes.byref(border_col),  ctypes.sizeof(border_col))
        except Exception:
            pass
    root.after(50, _apply_dark_titlebar)

    # Set window icon
    _icon_img = _load_icon()
    if _icon_img is not None:
        try:
            ico_path = _resource_path("icon.ico")
            if ico_path.exists():
                root.iconbitmap(str(ico_path))
            else:
                # Fallback: use PhotoImage from the PNG
                _tk_icon = ImageTk.PhotoImage(_icon_img.resize((32, 32), Image.LANCZOS))
                root.iconphoto(True, _tk_icon)
        except Exception:
            pass
    root.minsize(800, 600)
    root.resizable(True, True)
    root.protocol("WM_DELETE_WINDOW", lambda: _hide_window(root))

    def mkbtn(parent, text, cmd, color=ACC, state="normal", width=22):
        return tk.Button(parent, text=text, command=cmd,
                         bg=color, fg="#ffffff", activebackground=color,
                         activeforeground="#ffffff", relief="flat",
                         font=("Segoe UI Semibold", 10),
                         padx=14, pady=7, cursor="hand2", bd=0,
                         state=state, width=width)



    # ── Notebook ───────────────────────────────────────────────────────────────
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Main.TNotebook",
                    background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
    style.configure("Main.TNotebook.Tab",
                    background=CARD, foreground=FG,
                    padding=[20, 8], font=("Segoe UI Semibold", 10))
    style.map("Main.TNotebook.Tab",
              background=[("selected", ACC)],
              foreground=[("selected", "#ffffff")])

    nb = ttk.Notebook(root, style="Main.TNotebook")
    nb.pack(fill="both", expand=True, padx=0, pady=0)

    # ── Tab 1: Engine ──────────────────────────────────────────────────────────
    engine_tab = tk.Frame(nb, bg=BG)
    nb.add(engine_tab, text="🎙  Engine")

    # Status card
    status_card = tk.Frame(engine_tab, bg=CARD, padx=20, pady=12)
    status_card.pack(fill="x", padx=16, pady=(14, 0))
    status_var = tk.StringVar(value="○ Stopped")
    tk.Label(status_card, textvariable=status_var, bg=CARD, fg=GRN,
             font=("Segoe UI Semibold", 13)).pack()

    # Engine control buttons
    engine_btns = tk.Frame(engine_tab, bg=BG, pady=4)
    engine_btns.pack(fill="x", padx=16)

    b_start = mkbtn(engine_btns, "▶  Start Echo", lambda: None)
    b_stop  = mkbtn(engine_btns, "■  Stop Echo",
                    lambda: _stop_engine(status_var, b_start, b_stop),
                    color=MUTED, state="disabled")
    b_restart = mkbtn(engine_btns, "↻  Restart Echo",
                      lambda: _restart_engine_ui(root, status_var, b_start, b_stop),
                      color=MUTED)
    b_start.config(command=lambda: _start_engine(root, status_var, b_start, b_stop))
    b_upd = mkbtn(engine_btns, "🔄  Check for Updates",
                  lambda: _check_updates_ui(root, status_var), color=MUTED)

    for b in (b_start, b_restart, b_stop, b_upd):
        b.pack(pady=3, fill="x")

    # Model path row
    model_row = tk.Frame(engine_tab, bg=CARD, padx=12, pady=8)
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
                         wraplength=600, justify="left")
    path_lbl.pack(side="left", fill="x", expand=True)

    def _pick_model():
        chosen = filedialog.askdirectory(title="Select Vosk model folder", parent=root)
        if chosen:
            user_config.set_model_path(chosen)
            model_var.set(chosen)
            path_lbl.config(fg=GRN if pathlib.Path(chosen).is_dir() else RED)

    mkbtn(path_row, "Browse…", _pick_model, color=MUTED, width=8).pack(
        side="right", padx=(6, 0))

    # Debug log — hidden by default; shown only when the box is ticked.
    debug_ctrl = tk.Frame(engine_tab, bg=BG)
    debug_ctrl.pack(fill="x", padx=16, pady=(10, 0))

    debug_frame = tk.Frame(engine_tab, bg=BG)   # holds the log; packed on demand

    def _clear_log():
        log_box.config(state="normal")
        log_box.delete("1.0", "end")
        log_box.config(state="disabled")

    show_log_var = tk.BooleanVar(value=False)

    def _toggle_log():
        if show_log_var.get():
            debug_frame.pack(fill="both", expand=True, padx=16, pady=(4, 8))
            clear_btn.pack(side="right")
        else:
            debug_frame.pack_forget()
            clear_btn.pack_forget()

    tk.Checkbutton(debug_ctrl, text="Show debug log", variable=show_log_var,
                   command=_toggle_log, bg=BG, fg=FG, selectcolor=CARD,
                   activebackground=BG, activeforeground=FG,
                   font=("Segoe UI", 9)).pack(side="left")
    clear_btn = mkbtn(debug_ctrl, "Clear", _clear_log, color=MUTED, width=6)

    log_box = scrolledtext.ScrolledText(
        debug_frame, height=14, wrap="word",
        bg=LOG_BG, fg=FG, font=("Consolas", 9),
        relief="flat", bd=0, state="disabled",
    )
    log_box.pack(fill="both", expand=True, pady=(4, 0))
    log_box.tag_config("hear",  foreground="#89b4fa")
    log_box.tag_config("cmd",   foreground="#4ade80")
    log_box.tag_config("low",   foreground="#f9e2af")
    log_box.tag_config("info",  foreground="#ffffff")
    log_box.tag_config("error", foreground="#f87171")
    log_box.tag_config("muted", foreground="#3d5470")

    def _tag_for(text: str) -> str:
        t = text.lower()
        if "hearing:" in t or "👂" in t:       return "hear"
        if "command:" in t:                     return "cmd"
        if "low confidence" in t or "💤" in t:  return "low"
        if any(x in t for x in ("error", "failed", "traceback", "exception", "❌", "crashed")):
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

    # Engine tab footer
    eng_footer = tk.Frame(engine_tab, bg=BG)
    eng_footer.pack(fill="x", padx=16, pady=(6, 8))
    tk.Label(eng_footer, text=f"Config: {user_config.config_path()}",
             bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(side="left")
    mkbtn(eng_footer, "✕  Close App", _quit_app, color=RED, width=14).pack(side="right")

    # ── Tab 2: Apps ────────────────────────────────────────────────────────────
    apps_tab = tk.Frame(nb, bg=BG)
    nb.add(apps_tab, text="📦  Apps")

    from manage_apps import AppManagerWidget
    AppManagerWidget(apps_tab).pack(fill="both", expand=True)

    # ── Tab 3: Settings ────────────────────────────────────────────────────────
    settings_tab = tk.Frame(nb, bg=BG)
    nb.add(settings_tab, text="⚙  Settings")

    from settings_window import SettingsWidget
    SettingsWidget(settings_tab).pack(fill="both", expand=True)

    # ── Startup log ────────────────────────────────────────────────────────────
    _log_queue.put(f"Echo v{VERSION} started\n")
    _log_queue.put(f"Model path : {user_config.get_model_path()}\n")
    _log_queue.put(f"Model found: {pathlib.Path(user_config.get_model_path()).is_dir()}\n")
    _log_queue.put(f"Config     : {user_config.config_path()}\n")
    _log_queue.put(f"Apps       : {', '.join(user_config.get_apps().keys())}\n\n")

    # Silent background update check
    def _bg_check():
        latest = _fetch_latest_version()
        if latest and _version_tuple(latest) > _version_tuple(VERSION):
            root.after(0, lambda: status_var.set(f"⬆  Update {latest} available!"))

    threading.Thread(target=_bg_check, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    main()
