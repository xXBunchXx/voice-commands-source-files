"""
App Manager — add / delete entries in the user's local config.
Embeds directly in the main window as a tab (AppManagerWidget).
"""
import os
import pathlib
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import user_config

BG          = "#0a1020"
CARD        = "#0f1a2e"
ACC         = "#1a56db"
ACCENT_TEXT = "#4a8fe8"
FG          = "#ffffff"
ENTRY_BG    = "#162033"
MUTED       = "#3d5470"
GRN         = "#4ade80"
RED         = "#f87171"


# ── Built-in Windows apps ─────────────────────────────────────────────────────

_BUILTIN_APPS = [
    ("Notepad",          "notepad.exe",                          "notepad.exe"),
    ("Command Prompt",   r"C:\Windows\System32\cmd.exe",         "cmd.exe"),
    ("PowerShell",       r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                                                                 "powershell.exe"),
    ("Windows Terminal", "wt.exe",                               "WindowsTerminal.exe"),
    ("File Explorer",    r"C:\Windows\explorer.exe",             "explorer.exe"),
    ("Settings",         "ms-settings:",                         "SystemSettings.exe"),
    ("Calculator",       "calc.exe",                             "Calculator.exe"),
    ("Paint",            r"C:\Windows\System32\mspaint.exe",     "mspaint.exe"),
    ("Snipping Tool",    r"C:\Windows\System32\SnippingTool.exe","SnippingTool.exe"),
    ("Task Manager",     r"C:\Windows\System32\Taskmgr.exe",     "Taskmgr.exe"),
    ("WordPad",          r"C:\Program Files\Windows NT\Accessories\wordpad.exe",
                                                                 "wordpad.exe"),
    ("Control Panel",    r"C:\Windows\System32\control.exe",     "control.exe"),
    ("Registry Editor",  r"C:\Windows\regedit.exe",              "regedit.exe"),
    ("Character Map",    r"C:\Windows\System32\charmap.exe",     "charmap.exe"),
    ("Disk Cleanup",     r"C:\Windows\System32\cleanmgr.exe",    "cleanmgr.exe"),
    ("On-Screen Keyboard", r"C:\Windows\System32\osk.exe",       "osk.exe"),
]


def _builtin_apps() -> list[dict]:
    results = []
    for display, path, proc in _BUILTIN_APPS:
        voice_name = _to_voice_name(display)
        results.append({"display": f"{display}  (built-in)",
                         "name": voice_name, "path": path, "proc": proc})
    return results


def _to_voice_name(display: str) -> str:
    name = re.sub(r"\d[\d.]*", "", display)
    name = re.sub(r"[^a-z ]", "", name.lower()).strip()
    words = [w for w in name.split() if len(w) > 1]
    return words[-1] if words else name


# ── Folder scanner ────────────────────────────────────────────────────────────

def _scan_folder(folder: str) -> list[dict]:
    results = []
    seen = set()
    base = pathlib.Path(folder)
    if not base.is_dir():
        return results
    for exe in (list(base.glob("*.exe")) +
                list(base.glob("*/*.exe")) +
                list(base.glob("*/*/*.exe"))):
        key = str(exe).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if exe.stat().st_size < 200_000:
                continue
        except Exception:
            continue
        results.append({
            "display": f"{exe.stem}  ({base.name})",
            "name":    _to_voice_name(exe.stem),
            "path":    str(exe),
            "proc":    exe.name,
        })
    results.sort(key=lambda x: x["display"].lower())
    return results


# ── Registry scanner ──────────────────────────────────────────────────────────

def _scan_registry() -> list[dict]:
    import winreg
    results = []
    seen_paths = set()

    reg_keys = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    def _qval(sk, key):
        try:
            return winreg.QueryValueEx(sk, key)[0].strip()
        except Exception:
            return ""

    for hive, key_path in reg_keys:
        try:
            root_key = winreg.OpenKey(hive, key_path)
        except Exception:
            continue
        count = winreg.QueryInfoKey(root_key)[0]
        for i in range(count):
            try:
                sub_name = winreg.EnumKey(root_key, i)
                sk = winreg.OpenKey(root_key, sub_name)
                display = _qval(sk, "DisplayName")
                if not display:
                    continue
                if _qval(sk, "SystemComponent") == "1":
                    continue
                if re.search(r"(update|hotfix|kb\d{6}|redistributable|runtime|sdk|"
                             r"driver|pack|framework)", display, re.I):
                    continue
                exe_path = ""
                loc = _qval(sk, "InstallLocation")
                if loc and pathlib.Path(loc).is_dir():
                    folder = pathlib.Path(loc)
                    stem = re.sub(r"[^a-z0-9]", "", display.lower())
                    for exe in folder.glob("*.exe"):
                        if re.sub(r"[^a-z0-9]", "", exe.stem.lower()) in stem or \
                           stem in re.sub(r"[^a-z0-9]", "", exe.stem.lower()):
                            exe_path = str(exe)
                            break
                    if not exe_path:
                        exes = list(folder.glob("*.exe"))
                        if exes:
                            exe_path = str(exes[0])
                if not exe_path:
                    icon = _qval(sk, "DisplayIcon")
                    if icon:
                        icon = icon.split(",")[0].strip().strip('"')
                        if icon.lower().endswith(".exe") and pathlib.Path(icon).exists():
                            exe_path = icon
                if not exe_path or exe_path in seen_paths:
                    continue
                seen_paths.add(exe_path)
                voice_name = _to_voice_name(display)
                proc = pathlib.Path(exe_path).name
                results.append({"display": display, "name": voice_name,
                                 "path": exe_path, "proc": proc})
            except Exception:
                continue

    results += _builtin_apps()
    results.sort(key=lambda x: x["display"].lower())
    return results


def _scan_start_menu() -> list[dict]:
    """Scan the Windows Start Menu (.lnk shortcuts) and resolve their targets —
    this gives the same nicely-named entries you'd see searching the Start Menu
    (e.g. 'Visual Studio Code')."""
    results, seen = [], set()
    roots = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.getenv(env)
        if base:
            roots.append(pathlib.Path(base) / "Microsoft" / "Windows" /
                         "Start Menu" / "Programs")
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        shell = None
    _skip = ("uninstall", "setup", "readme", "help", "release notes",
             "website", "homepage", "documentation", "license")
    for root in roots:
        if not root.is_dir():
            continue
        try:
            lnks = list(root.rglob("*.lnk"))
        except Exception:
            continue
        for lnk in lnks:
            try:
                display = lnk.stem
                low = display.lower()
                if any(s in low for s in _skip):
                    continue
                target = ""
                if shell is not None:
                    target = shell.CreateShortcut(str(lnk)).TargetPath
                if not target or not target.lower().endswith(".exe"):
                    continue
                if not pathlib.Path(target).exists():
                    continue
                key = target.lower()
                if key in seen:
                    continue
                seen.add(key)
                results.append({"display": display,
                                "name":    _to_voice_name(display),
                                "path":    target,
                                "proc":    pathlib.Path(target).name})
            except Exception:
                continue
    return results


def _scan_apps_folder() -> list[dict]:
    """Enumerate the Windows 'Apps' shell folder — the exact list Start's app
    search uses.  Covers Win32 apps, UWP/Store apps, and launcher-registered
    apps (e.g. Unreal Engine, games) that have no plain .exe shortcut.

    These are launched the same way Start launches them:
    `explorer.exe shell:AppsFolder\\<AppID>`.  Window management (close/focus)
    isn't always possible for these, but opening always works."""
    results = []
    try:
        import win32com.client
        shell = win32com.client.Dispatch("Shell.Application")
        ns = shell.NameSpace("shell:AppsFolder")
        if ns is None:
            return results
        items = ns.Items()
        _skip = ("uninstall", "setup", "readme", "release notes",
                 "documentation", "license")
        for i in range(items.Count):
            try:
                item   = items.Item(i)
                name   = (item.Name or "").strip()
                app_id = (item.Path or "").strip()
                if not name or not app_id:
                    continue
                if any(s in name.lower() for s in _skip):
                    continue
                results.append({
                    "display": name,
                    "name":    _to_voice_name(name),
                    "path":    f"shell:AppsFolder\\{app_id}",
                    "proc":    "",   # unknown for shell apps; filled by .lnk/registry merge
                })
            except Exception:
                continue
    except Exception:
        pass
    return results


# ── App Manager Widget ────────────────────────────────────────────────────────

class AppManagerWidget(tk.Frame):
    """Embeds directly into a parent frame / notebook tab."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self._apps  = {}
        self._procs = {}
        self._scan_results   = []
        self._scan_visible   = []
        self._scan_vars      = []
        self._scan_name_vars = []
        self._all_candidates = None   # combined searchable app list (None = loading)
        self._build_ui()
        self._reload()
        self._load_candidates_bg()

    # ── Widget helpers ────────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, color=ACC, **kw):
        return tk.Button(parent, text=text, command=cmd,
                         bg=color, fg="#fff", activebackground=color,
                         activeforeground="#fff", relief="flat",
                         font=("Segoe UI Semibold", 9),
                         padx=10, pady=5, cursor="hand2", bd=0, **kw)

    def _inp(self, parent, width=42):
        return tk.Entry(parent, width=width, bg=ENTRY_BG, fg=FG,
                        insertbackground=FG, relief="flat",
                        font=("Segoe UI", 10), bd=4)

    def _lbl(self, parent, text, **kw):
        kw.setdefault("fg", FG)
        kw.setdefault("font", ("Segoe UI", 9))
        return tk.Label(parent, text=text, bg=parent["bg"], **kw)

    def _make_listen_widget(self, parent, target_entry):
        """A duration spinbox + 'Listen' button that records open-vocabulary
        speech and drops what it heard into *target_entry* (a spoken-name field)."""
        fr = tk.Frame(parent, bg=parent["bg"])
        dur = tk.DoubleVar(value=2.0)
        spin = tk.Spinbox(fr, from_=0.2, to=10.0, increment=0.1, textvariable=dur,
                          width=4, bg=ENTRY_BG, fg=FG, buttonbackground=CARD,
                          insertbackground=FG, relief="flat",
                          font=("Segoe UI", 9), justify="center")
        spin.pack(side="left")
        tk.Label(fr, text="s", bg=parent["bg"], fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(2, 6))
        btn = self._btn(fr, "🎤 Listen", lambda: None, color=MUTED)
        btn.config(command=lambda: self._do_listen(target_entry, dur, btn))
        btn.pack(side="left")
        return fr

    def _do_listen(self, entry, dur_var, btn):
        try:
            secs = float(dur_var.get())
        except Exception:
            secs = 2.0
        btn.config(state="disabled", text="Preparing…")
        self._flash("Preparing microphone…", "#fbbf24")
        self.update_idletasks()

        def _on_start():
            def _u():
                btn.config(text="Listening…")
                self._flash(f"Say the name now…  ({secs:.1f}s)", "#fbbf24")
            self.after(0, _u)

        def _work():
            text, err = "", ""
            try:
                import voice_controls
                text = voice_controls.listen_once(secs, on_start=_on_start)
            except Exception as e:
                err = str(e)

            def _done():
                btn.config(state="normal", text="🎤 Listen")
                if err:
                    self._flash(f"Listen failed: {err}", RED)
                elif text:
                    entry.delete(0, "end")
                    entry.insert(0, text)
                    self._flash(f'Heard "{text}" — set as spoken name.')
                else:
                    self._flash("Didn't catch anything — try again.", RED)
            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _section(self, parent, title):
        f = tk.Frame(parent, bg=BG)
        tk.Label(f, text=title, bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        tk.Frame(f, bg=ACC, height=1).pack(fill="x", pady=(2, 6))
        return f

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._main_page = tk.Frame(self, bg=BG)
        self._scan_page = tk.Frame(self, bg=BG)
        self._build_main_page(self._main_page)
        self._build_scan_page(self._scan_page)
        self._main_page.pack(fill="both", expand=True)

    def _build_main_page(self, outer):
        PAD = 12

        # Scrollable body — shows a scrollbar whenever the content (e.g. search
        # results) is taller than the window.
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vbar   = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        page = tk.Frame(canvas, bg=BG)
        _win = canvas.create_window((0, 0), window=page, anchor="nw")
        page.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(_win, width=e.width))

        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self._main_canvas  = canvas
        self._wheel_handler = _on_wheel
        canvas.bind("<MouseWheel>", _on_wheel)
        page.bind("<MouseWheel>", _on_wheel)
        self._main_inner = page   # bound recursively at the end of this method

        tk.Label(page, text=f"Config: {user_config.config_path()}",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(
            fill="x", padx=PAD, pady=(PAD, 0))

        # ── Search-to-add (Start-menu style) ──────────────────────────────
        find_sec = self._section(page, "🔍  Find an App")
        find_sec.pack(fill="x", padx=PAD, pady=(8, 0))
        find_card = tk.Frame(find_sec, bg=CARD, padx=10, pady=10)
        find_card.pack(fill="x")
        self._lbl(find_card,
                  "Type part of an app's name (e.g. \"code\") and click the right one — "
                  "its path is filled in for you. Then just add a spoken name and click Add Entry.",
                  fg=MUTED, font=("Segoe UI", 8), wraplength=620, justify="left").pack(anchor="w")
        self._search_var = tk.StringVar()
        se = tk.Entry(find_card, textvariable=self._search_var, bg=ENTRY_BG, fg=FG,
                      insertbackground=FG, relief="flat", font=("Segoe UI", 11), bd=5)
        se.pack(fill="x", pady=(6, 4))
        self._search_var.trace_add("write", lambda *a: self._refresh_search_results())
        self._search_results = tk.Frame(find_card, bg=CARD)
        self._results_packed = False   # results frame is only packed when it has content

        # Quick-add row
        quick = tk.Frame(page, bg=BG)
        quick.pack(fill="x", padx=PAD, pady=(8, 0))
        self._btn(quick, "📂  Manually add App",  self._browse_exe,     MUTED).pack(side="left")
        self._btn(quick, "🌐  Add Website",        self._add_website,    MUTED).pack(side="left", padx=(8, 0))
        self._btn(quick, "📁  Add Folder",         self._add_folder,     MUTED).pack(side="left", padx=(8, 0))
        self._btn(quick, "🎮  Add Steam Game",     self._add_steam_game, MUTED).pack(side="left", padx=(8, 0))

        # Add / Edit
        add_sec = self._section(page, "➕  Add / Edit Entry")
        add_sec.pack(fill="x", padx=PAD, pady=(8, 0))
        add_card = tk.Frame(add_sec, bg=CARD, padx=10, pady=10)
        add_card.pack(fill="x")

        self._lbl(add_card, "Display name  (shown in lists / logs)").grid(row=0, column=0, sticky="w")
        self._lbl(add_card, "Exe / path  (or steam:// URL)").grid(row=0, column=1, sticky="w", padx=(10,0))
        self._lbl(add_card, "Process name  (auto from path)").grid(row=0, column=2, sticky="w", padx=(10,0))

        self.e_name = self._inp(add_card, 18)
        self.e_path = self._inp(add_card, 38)
        self.e_proc = self._inp(add_card, 26)
        self.e_name.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.e_path.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(2, 0))
        self.e_proc.grid(row=1, column=2, sticky="ew", padx=(10, 0), pady=(2, 0))
        # Auto-fill the process name from the path as it's typed/pasted.
        self.e_path.bind("<KeyRelease>", lambda e: self._sync_proc_from_path())

        self._lbl(add_card, "Spoken name  (what you actually SAY to trigger it)",
                  fg=MUTED, font=("Segoe UI", 8)).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._lbl(add_card,
                  'e.g. say "ace sprite" for "aseprite"  (blank = use display name).  '
                  'Separate several aliases with commas, e.g. "code, editor, vs code".',
                  fg=MUTED, font=("Segoe UI", 8)).grid(
            row=2, column=2, sticky="w", padx=(10, 0), pady=(8, 0))

        self.e_spoken = self._inp(add_card, 30)
        self.e_spoken.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self._make_listen_widget(add_card, self.e_spoken).grid(
            row=3, column=2, sticky="w", padx=(10, 0), pady=(2, 0))

        self._btn(add_card, "Add Entry", self._on_add).grid(
            row=4, column=0, columnspan=3, pady=(10, 0), sticky="e")

        # Edit / Rename / Delete
        del_sec = self._section(page, "✏️  Edit / Rename / Delete Entry")
        del_sec.pack(fill="x", padx=PAD, pady=(PAD, 0))
        del_card = tk.Frame(del_sec, bg=CARD, padx=10, pady=10)
        del_card.pack(fill="x")

        self._lbl(del_card, "Select entry:").pack(anchor="w")

        style = ttk.Style(del_card); style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=ENTRY_BG, background=CARD,
                        foreground=FG, arrowcolor=FG)
        style.map("TCombobox", fieldbackground=[("readonly", ENTRY_BG)])

        self.combo_var = tk.StringVar()
        self.combo = ttk.Combobox(del_card, textvariable=self.combo_var,
                                  state="readonly", width=52, font=("Segoe UI", 10))
        self.combo.pack(fill="x", pady=(4, 6))
        self.combo.bind("<<ComboboxSelected>>", self._on_select)

        # Editable fields for the selected entry
        edit_grid = tk.Frame(del_card, bg=CARD)
        edit_grid.pack(fill="x", pady=(0, 6))

        self._lbl(edit_grid, "Path / URL").grid(row=0, column=0, sticky="w")
        self._lbl(edit_grid, "Process name").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self._lbl(edit_grid, "Spoken name").grid(row=0, column=2, sticky="w", padx=(10, 0))

        self.e_edit_path   = self._inp(edit_grid, 36)
        self.e_edit_proc   = self._inp(edit_grid, 22)
        self.e_edit_spoken = self._inp(edit_grid, 20)
        self.e_edit_path.grid  (row=1, column=0, sticky="ew", pady=(2, 0))
        self.e_edit_proc.grid  (row=1, column=1, sticky="ew", padx=(10, 0), pady=(2, 0))
        self.e_edit_spoken.grid(row=1, column=2, sticky="ew", padx=(10, 0), pady=(2, 0))
        self._make_listen_widget(edit_grid, self.e_edit_spoken).grid(
            row=2, column=2, sticky="w", padx=(10, 0), pady=(4, 0))

        # Browse button for path
        browse_row = tk.Frame(del_card, bg=CARD)
        browse_row.pack(fill="x", pady=(4, 0))
        self._btn(browse_row, "📂 Browse", self._browse_edit_exe, MUTED).pack(side="left")
        self._btn(browse_row, "🎯 Detect", self._detect_proc_edit, MUTED).pack(side="left", padx=(8, 0))
        self._btn(browse_row, "💾  Save Changes", self._on_save_edit, GRN).pack(side="left", padx=(8, 0))

        # Rename row
        rename_row = tk.Frame(del_card, bg=CARD)
        rename_row.pack(fill="x", pady=(10, 4))
        self._lbl(rename_row, "Rename display name to:").pack(side="left")
        self.e_rename = self._inp(rename_row, width=20)
        self.e_rename.pack(side="left", padx=(8, 8))
        self._btn(rename_row, "Rename", self._on_rename).pack(side="left")

        self._btn(del_card, "Delete Selected", self._on_delete, RED).pack(anchor="e")

        self._status_lbl = tk.Label(page, text="", bg=BG, fg=GRN,
                                    font=("Segoe UI", 9), anchor="w")
        self._status_lbl.pack(fill="x", padx=PAD, pady=(PAD, PAD))

        # Route mouse-wheel over any child widget to the scrolling canvas.
        self._bind_wheel(page)

    def _bind_wheel(self, widget):
        try:
            widget.bind("<MouseWheel>", self._wheel_handler)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _build_scan_page(self, page):
        # Header
        hdr = tk.Frame(page, bg=BG)
        hdr.pack(fill="x", padx=12, pady=(8, 0))
        self._btn(hdr, "←  Back", self._go_main, MUTED).pack(side="left")
        tk.Label(hdr, text="🔍  Scan Installed Apps", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(side="left", padx=(12, 0))

        self._scan_status = tk.Label(page, text="", bg=BG, fg=MUTED,
                                     font=("Segoe UI", 9))
        self._scan_status.pack()

        # Search
        sr = tk.Frame(page, bg=BG)
        sr.pack(fill="x", padx=12, pady=(6, 0))
        tk.Label(sr, text="Filter:", bg=BG, fg=FG, font=("Segoe UI", 9)).pack(side="left")
        self._scan_search = tk.StringVar()
        self._scan_search.trace_add("write", lambda *_: self._filter_scan())
        tk.Entry(sr, textvariable=self._scan_search, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief="flat", font=("Segoe UI", 10), bd=4).pack(
            side="left", fill="x", expand=True, padx=(6, 0))

        # Extra folders
        fr = tk.Frame(page, bg=BG)
        fr.pack(fill="x", padx=12, pady=(4, 0))
        tk.Label(fr, text="Extra search folders:", bg=BG, fg=MUTED,
                 font=("Segoe UI Semibold", 8)).pack(side="left")
        self._folders_lbl = tk.Label(fr, text="", bg=BG, fg=FG,
                                     font=("Segoe UI", 8), anchor="w")
        self._folders_lbl.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self._btn(fr, "➕ Add Folder",  self._add_scan_folder,   MUTED).pack(side="left", padx=(8, 0))
        self._btn(fr, "✕ Clear",        self._clear_scan_folders, MUTED).pack(side="left", padx=(4, 0))
        self._refresh_folders_lbl()

        # Results list
        lf = tk.Frame(page, bg=CARD)
        lf.pack(fill="both", expand=True, padx=12, pady=8)

        self._scan_canvas = tk.Canvas(lf, bg=CARD, highlightthickness=0)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self._scan_canvas.yview)
        self._scan_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._scan_canvas.pack(side="left", fill="both", expand=True)

        self._scan_inner = tk.Frame(self._scan_canvas, bg=CARD)
        cwin = self._scan_canvas.create_window((0, 0), window=self._scan_inner, anchor="nw")
        self._scan_inner.bind("<Configure>",
            lambda e: self._scan_canvas.configure(scrollregion=self._scan_canvas.bbox("all")))
        self._scan_canvas.bind("<Configure>",
            lambda e: self._scan_canvas.itemconfig(cwin, width=e.width))
        for w in (self._scan_canvas, self._scan_inner):
            w.bind("<MouseWheel>",
                   lambda e: self._scan_canvas.yview_scroll(-1*(e.delta//120), "units"))

        # Bottom row
        bot = tk.Frame(page, bg=BG)
        bot.pack(fill="x", padx=12, pady=(0, 10))

        self._btn(bot, "Select All",   self._sel_all_scan, MUTED).pack(side="left")
        self._btn(bot, "Deselect All",
                  lambda: [v.set(False) for v in self._scan_vars],
                  MUTED).pack(side="left", padx=(6, 0))

        self._scan_count_lbl = tk.Label(bot, text="", bg=BG, fg=GRN, font=("Segoe UI", 9))
        self._scan_count_lbl.pack(side="right", padx=(0, 10))

        self._btn(bot, "➕  Add Selected", self._add_scan_selected).pack(side="right")

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go_scan(self):
        self._scan_search.set("")
        self._scan_status.config(text="Scanning registry…")
        for w in self._scan_inner.winfo_children():
            w.destroy()
        self._main_page.pack_forget()
        self._scan_page.pack(fill="both", expand=True)
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _go_main(self):
        self._scan_page.pack_forget()
        self._main_page.pack(fill="both", expand=True)
        self._reload()

    # ── Inline overlay (for small add-forms) ──────────────────────────────────

    def _show_overlay(self, title, icon, fields, on_submit):
        """Cover the widget with a centred form card.
        fields = [(label, hint), ...]
        on_submit(list_of_str_values) called on OK; overlay closes on Cancel/Escape."""
        overlay = tk.Frame(self, bg="#0d0d1a")
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()

        card = tk.Frame(overlay, bg=CARD, padx=24, pady=20)
        card.place(relx=0.5, rely=0.4, anchor="center")

        tk.Label(card, text=f"{icon}  {title}", bg=CARD, fg=FG,
                 font=("Segoe UI Semibold", 11)).pack(pady=(0, 12))

        entries = []
        for label, hint in fields:
            f = tk.Frame(card, bg=CARD)
            f.pack(fill="x", pady=(0, 8))
            tk.Label(f, text=label, bg=CARD, fg=FG,
                     font=("Segoe UI", 9)).pack(anchor="w")
            e = tk.Entry(f, width=44, bg=ENTRY_BG, fg=FG,
                         insertbackground=FG, relief="flat",
                         font=("Segoe UI", 10), bd=4)
            e.pack(fill="x")
            if hint:
                tk.Label(f, text=hint, bg=CARD, fg=MUTED,
                         font=("Segoe UI", 8)).pack(anchor="w")
            entries.append(e)

        btn_row = tk.Frame(card, bg=CARD)
        btn_row.pack(fill="x", pady=(8, 0))

        def _ok(_e=None):
            vals = [e.get() for e in entries]
            overlay.destroy()
            on_submit(vals)

        def _cancel(_e=None):
            overlay.destroy()

        for e in entries:
            e.bind("<Return>", _ok)
        overlay.bind("<Escape>", _cancel)

        self._btn(btn_row, "OK",     _ok,     ACC ).pack(side="right")
        self._btn(btn_row, "Cancel", _cancel, MUTED).pack(side="right", padx=(0, 8))
        entries[0].focus_set()

    # ── Quick-add handlers ────────────────────────────────────────────────────

    @staticmethod
    def _auto_proc_from_path(path: str) -> str:
        """Process name = the part of an exe path after the last slash.
        Returns '' for URLs / folders / shell: items (no process to match)."""
        p = path.strip().strip('"')
        if not p or "://" in p or p.lower().startswith(("ms-", "shell:")):
            return ""
        base = p.replace("/", "\\").split("\\")[-1]
        return base if base.lower().endswith(".exe") else ""

    def _sync_proc_from_path(self):
        proc = self._auto_proc_from_path(self.e_path.get())
        if proc:
            self.e_proc.delete(0, "end")
            self.e_proc.insert(0, proc)

    def _browse_exe(self):
        path = filedialog.askopenfilename(
            title="Select application executable",
            filetypes=[("Executables", "*.exe"), ("All files", "*.*")],
            parent=self.winfo_toplevel(),
        )
        if not path:
            return
        p = pathlib.Path(path)
        self.e_name.delete(0, "end"); self.e_name.insert(0, _to_voice_name(p.stem))
        self.e_path.delete(0, "end"); self.e_path.insert(0, str(p))
        self.e_proc.delete(0, "end"); self.e_proc.insert(0, p.name)
        self.e_spoken.delete(0, "end")
        self._flash(f"Auto-filled from {p.name} — fill in Spoken name if the name is hard to say, then Add Entry.")

    def _add_website(self):
        def on_submit(vals):
            name, url = vals
            name = name.strip().lower(); url = url.strip()
            if not name or not url:
                return
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            user_config.add_entry(name, url, "")
            self._reload()
            self._flash(f'✓  Added website "{name}" → {url}')
        self._show_overlay("Add Website", "🌐", [
            ("Voice command name", "e.g.  youtube"),
            ("URL", "e.g.  https://www.youtube.com"),
        ], on_submit)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder to open",
                                         parent=self.winfo_toplevel())
        if not folder:
            return
        suggested = pathlib.Path(folder).name.lower().replace("_", " ").replace("-", " ")

        def on_submit(vals):
            name = vals[0].strip().lower()
            if not name:
                return
            user_config.add_entry(name, folder, "explorer.exe")
            self._reload()
            self._flash(f'✓  Added folder "{name}" → {folder}')
        self._show_overlay("Add Folder", "📁", [
            ("Voice command name  (pre-filled from folder name — change if needed)", ""),
        ], on_submit)
        # pre-fill after overlay is drawn
        self.after(50, lambda: self._prefill_overlay(suggested))

    def _prefill_overlay(self, text: str):
        """Try to pre-fill the first entry in any visible overlay."""
        for child in self.winfo_children():
            if isinstance(child, tk.Frame) and str(child.place_info()):
                for card in child.winfo_children():
                    if isinstance(card, tk.Frame):
                        for f in card.winfo_children():
                            if isinstance(f, tk.Frame):
                                for w in f.winfo_children():
                                    if isinstance(w, tk.Entry):
                                        w.delete(0, "end")
                                        w.insert(0, text)
                                        return

    def _add_steam_game(self):
        def on_submit(vals):
            name, app_id = vals
            name = name.strip().lower(); app_id = app_id.strip()
            if not name or not app_id:
                return
            if not app_id.isdigit():
                messagebox.showwarning("Invalid ID",
                                       "Steam App ID must be a number (e.g. 730).",
                                       parent=self.winfo_toplevel())
                return
            user_config.add_entry(name, f"steam://rungameid/{app_id}", "steam.exe")
            self._reload()
            self._flash(f'✓  Added Steam game "{name}" (App ID {app_id})')
        self._show_overlay("Add Steam Game", "🎮", [
            ("Voice command name", "e.g.  cyberpunk"),
            ("Steam App ID",
             "Find it in the store URL: store.steampowered.com/app/730/ → ID is  730"),
        ], on_submit)

    # ── Search-to-add ─────────────────────────────────────────────────────────

    def _load_candidates_bg(self):
        """Build the combined searchable app list (Start Menu + registry + scan
        folders) once, in the background, so search is instant afterwards."""
        def _work():
            cands = []
            # .lnk + registry first — they carry a real process name (so window
            # management works).  AppsFolder last — it adds full Start coverage.
            for fn in (_scan_start_menu, _scan_registry, _scan_apps_folder):
                try:
                    cands += fn()
                except Exception:
                    pass
            for folder in user_config.get_scan_folders():
                try:
                    cands += _scan_folder(folder)
                except Exception:
                    pass
            # Dedupe by display name; keep the entry that has a process name
            # (better for focus/close) over a bare shell:AppsFolder launcher.
            by_name = {}
            for r in cands:
                key = r["display"].strip().lower()
                if not key:
                    continue
                cur = by_name.get(key)
                if cur is None or (not cur.get("proc") and r.get("proc")):
                    by_name[key] = r
            deduped = sorted(by_name.values(), key=lambda x: x["display"].lower())
            self._all_candidates = deduped
            self.after(0, self._refresh_search_results)
        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _candidate_matches(query: str, r: dict) -> bool:
        q = query.lower().strip()
        if not q:
            return False
        disp    = r["display"].lower()
        hay     = disp + " " + r["proc"].lower()
        acronym = "".join(w[0] for w in re.split(r"[\s\-]+", disp) if w)
        # Whole query as an acronym, e.g. "vsc" → Visual Studio Code, "gc" → Google Chrome
        if acronym and acronym.startswith(q.replace(" ", "")):
            return True
        # Otherwise every token must be a substring OR an acronym prefix, so
        # "vs code" works (vs = acronym prefix, code = substring).
        for tok in q.split():
            if tok in hay or (acronym and acronym.startswith(tok)):
                continue
            return False
        return True

    def _show_results(self, show: bool):
        """Pack/unpack the results frame so the layout contracts when empty."""
        if show and not self._results_packed:
            self._search_results.pack(fill="x", pady=(4, 0))
            self._results_packed = True
        elif not show and self._results_packed:
            self._search_results.pack_forget()
            self._results_packed = False

    def _refresh_search_results(self):
        for w in self._search_results.winfo_children():
            w.destroy()
        q = self._search_var.get().strip()
        if not q:
            self._show_results(False)
            return
        self._show_results(True)
        if self._all_candidates is None:
            self._lbl(self._search_results, "Loading installed apps…",
                      fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=2)
            return
        matches = [r for r in self._all_candidates if self._candidate_matches(q, r)]
        # rank: display starting with the query first, then by name length
        ql = q.lower()
        matches.sort(key=lambda r: (not r["display"].lower().startswith(ql),
                                    len(r["display"])))
        matches = matches[:8]
        if not matches:
            self._lbl(self._search_results, "No matches — try a different word, "
                      "or use Browse / Scan below.",
                      fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=2)
            return
        existing = {p.lower() for p in user_config.get_apps().values()}
        for r in matches:
            self._make_search_row(r, r["path"].lower() in existing)

    def _make_search_row(self, r, already):
        row = tk.Frame(self._search_results, bg=CARD)
        row.pack(fill="x", pady=1)
        btn = tk.Button(row, text=f"  {r['display']}", anchor="w",
                        bg=ENTRY_BG, fg=(MUTED if already else FG),
                        activebackground=ACC, activeforeground="#fff",
                        relief="flat", font=("Segoe UI", 10), cursor="hand2",
                        bd=0, padx=8, pady=4,
                        command=lambda rr=r: self._pick_candidate(rr))
        btn.pack(side="left", fill="x", expand=True)
        tag = "  ✓ added" if already else r["proc"]
        tk.Label(row, text=tag, bg=CARD, fg=MUTED,
                 font=("Consolas", 8)).pack(side="right", padx=(6, 2))
        if getattr(self, "_wheel_handler", None):
            self._bind_wheel(row)

    def _pick_candidate(self, r):
        for entry, val in ((self.e_name, r["name"]),
                           (self.e_path, r["path"]),
                           (self.e_proc, r["proc"])):
            entry.delete(0, "end")
            entry.insert(0, val)
            entry.xview_moveto(0)
        self.e_spoken.focus_set()
        self._search_var.set("")
        self._flash(f'Selected "{r["display"]}" — set a spoken name (optional), '
                    f'then click Add Entry.')

    # ── Scan logic ────────────────────────────────────────────────────────────

    def _do_scan(self):
        results = _scan_registry()
        for folder in user_config.get_scan_folders():
            results += _scan_folder(folder)
        seen = set(); deduped = []
        for r in results:
            k = r["path"].lower()
            if k not in seen:
                seen.add(k); deduped.append(r)
        deduped.sort(key=lambda x: x["display"].lower())
        self.after(0, lambda: self._populate_scan(deduped))

    def _populate_scan(self, results):
        for w in self._scan_inner.winfo_children():
            w.destroy()
        self._scan_results  = results
        self._scan_visible  = results
        self._scan_vars     = []
        self._scan_name_vars = []
        existing = set(user_config.get_apps().keys())
        for r in results:
            v  = tk.BooleanVar(value=False)
            nv = tk.StringVar(value=r["name"])
            v.trace_add("write", self._update_scan_count)
            self._scan_vars.append(v)
            self._scan_name_vars.append(nv)
            self._make_scan_row(r, v, nv, r["name"] in existing)
        extra = len(user_config.get_scan_folders())
        suffix = f" + {extra} extra folder(s)" if extra else ""
        self._scan_status.config(text=f"Found {len(results)} apps{suffix}")
        self._update_scan_count()

    def _make_scan_row(self, r, var, name_var, already_added):
        row = tk.Frame(self._scan_inner, bg=CARD, pady=3)
        row.pack(fill="x", padx=4, pady=1)
        cb = tk.Checkbutton(row, variable=var, bg=CARD, activebackground=CARD,
                            selectcolor=ENTRY_BG, fg=FG, disabledforeground=MUTED,
                            state="disabled" if already_added else "normal")
        cb.pack(side="left")
        info = tk.Frame(row, bg=CARD)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=r["display"], bg=CARD,
                 fg=MUTED if already_added else FG,
                 font=("Segoe UI Semibold", 9), anchor="w").pack(anchor="w")
        if already_added:
            tk.Label(info, text="  ✓ already added", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w")
        else:
            nr = tk.Frame(info, bg=CARD)
            nr.pack(anchor="w", fill="x")
            tk.Label(nr, text="  display name:", bg=CARD, fg=GRN,
                     font=("Segoe UI", 8)).pack(side="left")
            tk.Entry(nr, textvariable=name_var, width=20, bg=ENTRY_BG, fg=FG,
                     insertbackground=FG, relief="flat",
                     font=("Segoe UI", 8), bd=2).pack(side="left", padx=(4, 0))
        scroll = lambda e: self._scan_canvas.yview_scroll(-1*(e.delta//120), "units")
        for w in (row, cb, info):
            w.bind("<MouseWheel>", scroll)

    def _filter_scan(self):
        query = self._scan_search.get().lower()
        for w in self._scan_inner.winfo_children():
            w.destroy()
        existing = set(user_config.get_apps().keys())
        self._scan_visible  = [r for r in self._scan_results
                                if query in r["display"].lower() or query in r["name"]]
        self._scan_vars     = []
        self._scan_name_vars = []
        for r in self._scan_visible:
            v  = tk.BooleanVar(value=False)
            nv = tk.StringVar(value=r["name"])
            v.trace_add("write", self._update_scan_count)
            self._scan_vars.append(v)
            self._scan_name_vars.append(nv)
            self._make_scan_row(r, v, nv, r["name"] in existing)
        self._update_scan_count()

    def _update_scan_count(self, *_):
        n = sum(v.get() for v in self._scan_vars)
        self._scan_count_lbl.config(text=f"{n} selected" if n else "")

    def _sel_all_scan(self):
        existing = set(user_config.get_apps().keys())
        for v, r in zip(self._scan_vars, self._scan_visible):
            if r["name"] not in existing:
                v.set(True)

    def _add_scan_folder(self):
        folder = filedialog.askdirectory(
            title="Select extra folder (e.g. D:\\SteamLibrary\\steamapps\\common)",
            parent=self.winfo_toplevel())
        if not folder:
            return
        folders = user_config.get_scan_folders()
        if folder not in folders:
            folders.append(folder)
            user_config.set_scan_folders(folders)
        self._refresh_folders_lbl()
        self._scan_status.config(text="Rescanning with new folder…")
        for w in self._scan_inner.winfo_children():
            w.destroy()
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _clear_scan_folders(self):
        user_config.set_scan_folders([])
        self._refresh_folders_lbl()
        self._scan_status.config(text="Rescanning…")
        for w in self._scan_inner.winfo_children():
            w.destroy()
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _refresh_folders_lbl(self):
        folders = user_config.get_scan_folders()
        self._folders_lbl.config(
            text=("  ·  ".join(pathlib.Path(f).name for f in folders))
                 if folders else "none — add a folder to search other drives")

    def _add_scan_selected(self):
        selected = [(r, nv.get().strip().lower())
                    for r, v, nv in zip(self._scan_visible, self._scan_vars, self._scan_name_vars)
                    if v.get()]
        if not selected:
            messagebox.showwarning("Nothing selected", "Tick at least one app to add.",
                                   parent=self.winfo_toplevel())
            return
        if any(not name for _, name in selected):
            messagebox.showwarning("Empty name", "One or more display names are empty.",
                                   parent=self.winfo_toplevel())
            return
        existing = user_config.get_apps()
        conflicts = [name for _, name in selected if name in existing]
        if conflicts:
            names = ", ".join(f'"{n}"' for n in conflicts)
            if not messagebox.askyesno("Overwrite?",
                    f"These display names already exist: {names}\n\nOverwrite them?",
                    parent=self.winfo_toplevel()):
                return
        for r, name in selected:
            user_config.add_entry(name, r["path"], r["proc"])
        messagebox.showinfo("Done",
                            f"Added {len(selected)} app(s).\n\n" +
                            "\n".join(f'  • {r["display"]} → "{name}"'
                                      for r, name in selected),
                            parent=self.winfo_toplevel())
        self._go_main()

        # Store / Start-menu apps come in without a process name (needed for
        # custom commands + window control).  Auto-detect those by launching
        # them and reading their process.  Steam games are skipped — their
        # steam://rungameid launcher isn't the game's own process.
        need = [(name, r["path"]) for r, name in selected
                if not (r.get("proc") or "").strip()
                and not str(r.get("path", "")).lower().startswith("steam:")]
        if need:
            self.after(400, lambda: self._auto_detect_queue(need))

    # ── Main-page logic ───────────────────────────────────────────────────────

    def _reload(self):
        self._apps  = user_config.get_apps()
        self._procs = user_config.get_proc_names()
        names = sorted(self._apps.keys())
        prev  = self.combo_var.get()          # keep selection if still present
        self.combo["values"] = names
        if names:
            sel = prev if prev in names else names[0]
            self.combo.set(sel)
            self._show_preview(sel)
            self.e_rename.delete(0, "end")
            self.e_rename.insert(0, sel)
        else:
            self.combo.set("")
            for e in (self.e_edit_path, self.e_edit_proc, self.e_edit_spoken):
                e.delete(0, "end")
            self.e_rename.delete(0, "end")

    def _show_preview(self, name: str):
        """Populate the edit fields from the selected app."""
        path   = self._apps.get(name, "")
        proc   = self._procs.get(name, "")
        spoken = user_config.get_spoken_names().get(name, "")
        for e, val in ((self.e_edit_path, path),
                       (self.e_edit_proc, proc),
                       (self.e_edit_spoken, spoken)):
            e.delete(0, "end")
            e.insert(0, val)

    def _on_select(self, _=None):
        name = self.combo_var.get()
        self._show_preview(name)
        self.e_rename.delete(0, "end")
        self.e_rename.insert(0, name)

    def _browse_edit_exe(self):
        """Browse for a new exe for the selected entry."""
        path = filedialog.askopenfilename(
            title="Select application executable",
            filetypes=[("Executables", "*.exe"), ("All files", "*.*")],
            parent=self.winfo_toplevel(),
        )
        if not path:
            return
        p = pathlib.Path(path)
        self.e_edit_path.delete(0, "end"); self.e_edit_path.insert(0, str(p))
        self.e_edit_proc.delete(0, "end"); self.e_edit_proc.insert(0, p.name)

    def _detect_proc_edit(self):
        """Capture the process name of whatever window the user focuses next.

        Useful for Store / Start-menu apps (e.g. Claude) that were added via
        shell:AppsFolder and therefore have no .exe process name on record."""
        if not self.combo_var.get():
            self._flash("Pick an app from the list first.", RED)
            return

        secs = 5

        def _tick(remaining):
            if remaining > 0:
                self._status_lbl.config(
                    text=f"Switch to the app's window now — capturing in "
                         f"{remaining}s …", fg=ACCENT_TEXT)
                self.after(1000, lambda: _tick(remaining - 1))
                return
            name = self._capture_foreground_proc()
            if not name:
                self._flash(
                    "Couldn't read the foreground app (or it was this window) — "
                    "try again and focus the target app.", RED)
                return
            self.e_edit_proc.delete(0, "end")
            self.e_edit_proc.insert(0, name)
            self._flash(
                f'Detected "{name}".  Press 💾 Save Changes to keep it.', GRN)

        self.after(1000, lambda: _tick(secs - 1))

    # ── Shared process-detection helpers ───────────────────────────────────────

    def _capture_foreground_proc(self) -> str:
        """Return the process name of the current foreground window, or '' if it
        can't be read or is one of our own / launcher windows."""
        try:
            import win32gui, win32process, psutil
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name()
        except Exception:
            return ""
        if not name or name.lower() in (
                "echo.exe", "python.exe", "pythonw.exe", "explorer.exe",
                "applicationframehost.exe"):
            return ""
        return name

    @staticmethod
    def _launch_path(path: str) -> None:
        """Launch an app by its stored path / URI / shell:AppsFolder id."""
        import subprocess
        path = str(path)
        if path.lower().startswith("shell:"):
            subprocess.Popen(["explorer.exe", path])
        else:
            os.startfile(path)

    def _auto_detect_queue(self, queue):
        """Sequentially launch each (name, path) and capture its process name."""
        if not queue:
            self._flash("Process detection finished.", GRN)
            self._reload()
            return
        (name, path), rest = queue[0], queue[1:]

        if not messagebox.askyesno(
                "Detect process?",
                f'"{name}" was added without a process name, which custom '
                "commands and window control need.\n\n"
                "Launch it now and auto-detect the process?\n"
                "(Tip: let its window come to the front and don't click away.)",
                parent=self.winfo_toplevel()):
            self.after(200, lambda: self._auto_detect_queue(rest))
            return

        try:
            self._launch_path(path)
        except Exception as e:
            self._flash(f'Could not launch "{name}": {e}', RED)
            self.after(200, lambda: self._auto_detect_queue(rest))
            return

        secs = 6

        def _tick(remaining):
            if remaining > 0:
                self._status_lbl.config(
                    text=f'Detecting "{name}" — let its window open … '
                         f"{remaining}s", fg=ACCENT_TEXT)
                self.after(1000, lambda: _tick(remaining - 1))
                return
            proc = self._capture_foreground_proc()
            if proc:
                user_config.add_entry(name, path, proc)
                self._flash(f'Detected "{proc}" for "{name}".', GRN)
            else:
                self._flash(
                    f'Couldn\'t detect a process for "{name}" — set it manually '
                    "via 🎯 Detect on the Apps tab.", RED)
            self.after(900, lambda: self._auto_detect_queue(rest))

        self.after(1800, lambda: _tick(secs))

    def _on_save_edit(self):
        """Save edited path, proc and spoken name for the selected entry."""
        name   = self.combo_var.get()
        path   = self.e_edit_path.get().strip()
        proc   = self.e_edit_proc.get().strip()
        spoken = self.e_edit_spoken.get().strip().lower()
        if not name:
            return
        if not path or not proc:
            messagebox.showwarning("Missing fields",
                                   "Path and Process name cannot be empty.",
                                   parent=self.winfo_toplevel())
            return
        user_config.add_entry(name, path, proc)
        user_config.set_spoken_name(name, spoken)
        self._reload()
        self.combo.set(name)
        self._show_preview(name)
        note = f'  (say "{spoken}")' if spoken else ""
        self._flash(f'✓  Updated "{name}"{note}.')

    def _flash(self, msg: str, color=GRN):
        self._status_lbl.config(text=msg, fg=color)
        self.after(6000, lambda: self._status_lbl.config(text=""))

    def _on_add(self):
        name   = self.e_name.get().strip().lower()
        path   = self.e_path.get().strip()
        proc   = self.e_proc.get().strip() or self._auto_proc_from_path(path)
        spoken = self.e_spoken.get().strip().lower()
        # Process name is optional for launch-only apps (e.g. Start "Apps" /
        # Store / launcher-registered apps) which can be opened but not
        # window-managed.  Display name + path are required.
        if not name or not path:
            messagebox.showwarning("Missing fields",
                                   "Display name and Exe / path are required.",
                                   parent=self.winfo_toplevel())
            return
        if name in self._apps and not messagebox.askyesno(
                "Overwrite?", f'"{name}" already exists. Overwrite it?',
                parent=self.winfo_toplevel()):
            return
        user_config.add_entry(name, path, proc)
        user_config.set_spoken_name(name, spoken)
        self._reload()
        for e in (self.e_name, self.e_path, self.e_proc, self.e_spoken):
            e.delete(0, "end")
        note = f'  (say "{spoken}")' if spoken else ""
        self._flash(f'✓  Added "{name}"{note}.')

    def _on_rename(self):
        old = self.combo_var.get()
        new = self.e_rename.get().strip().lower()
        if not old or not new or new == old:
            return
        if new in self._apps and not messagebox.askyesno(
                "Overwrite?", f'"{new}" already exists. Overwrite it?',
                parent=self.winfo_toplevel()):
            return
        # Preserve spoken name under new key
        spoken_names = user_config.get_spoken_names()
        old_spoken   = spoken_names.pop(old, "")
        if old_spoken:
            spoken_names[new] = old_spoken
        user_config.delete_entry(old)
        user_config.add_entry(new, self._apps.get(old, ""), self._procs.get(old, ""))
        user_config.set_spoken_names(spoken_names)
        self._reload()
        self._flash(f'✓  Renamed "{old}" → "{new}".')

    def _on_delete(self):
        name = self.combo_var.get()
        if not name:
            return
        if not messagebox.askyesno("Confirm delete", f'Delete "{name}" from your config?',
                                   parent=self.winfo_toplevel()):
            return
        user_config.delete_entry(name)
        user_config.set_spoken_name(name, "")
        self._reload()
        self._flash(f'✓  Deleted "{name}".', color=RED)


# Backward-compat alias
AppManagerWindow = AppManagerWidget


# ── Standalone ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    root.title("App Manager")
    root.configure(bg=BG)
    root.geometry("960x680")
    AppManagerWidget(root).pack(fill="both", expand=True)
    root.mainloop()
