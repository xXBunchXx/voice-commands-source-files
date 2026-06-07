"""
App Manager GUI — add / delete entries in the user's local config.
Opened as a Toplevel from main.py, or runs standalone.
"""
import pathlib
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import user_config


# ── Simple input dialog ───────────────────────────────────────────────────────

class _SimpleDialog(tk.Toplevel):
    """Small popup with one or two labelled Entry fields and OK/Cancel."""

    def __init__(self, master, title, icon, fields, prefill=None):
        super().__init__(master)
        self.title(title)
        self.configure(bg="#1e1e2e")
        self.resizable(False, False)
        self.result = None
        BG = "#1e1e2e"; CARD = "#2a2a3e"; ACC = "#7c6af7"
        FG = "#cdd6f4"; ENTRY_BG = "#313244"; MUTED = "#585b70"

        tk.Label(self, text=f"{icon}  {title}", bg=BG, fg=ACC,
                 font=("Segoe UI Semibold", 11)).pack(padx=20, pady=(14, 8))

        self._entries = []
        for i, (label, hint) in enumerate(fields):
            f = tk.Frame(self, bg=BG)
            f.pack(fill="x", padx=20, pady=(0, 6))
            tk.Label(f, text=label, bg=BG, fg=FG,
                     font=("Segoe UI", 9)).pack(anchor="w")
            e = tk.Entry(f, width=42, bg=ENTRY_BG, fg=FG,
                         insertbackground=FG, relief="flat",
                         font=("Segoe UI", 10), bd=4)
            e.pack(fill="x")
            if prefill and i < len(prefill):
                e.insert(0, prefill[i])
            else:
                tk.Label(f, text=hint, bg=BG, fg=MUTED,
                         font=("Segoe UI", 8)).pack(anchor="w")
            self._entries.append(e)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=20, pady=(4, 14))

        def _ok():
            self.result = [e.get() for e in self._entries]
            self.destroy()

        tk.Button(btn_row, text="OK", command=_ok,
                  bg=ACC, fg="#fff", activebackground=ACC,
                  activeforeground="#fff", relief="flat",
                  font=("Segoe UI Semibold", 9), padx=14, pady=5,
                  cursor="hand2").pack(side="right")
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg=MUTED, fg="#fff", activebackground=MUTED,
                  activeforeground="#fff", relief="flat",
                  font=("Segoe UI Semibold", 9), padx=14, pady=5,
                  cursor="hand2").pack(side="right", padx=(0, 8))

        self._entries[0].focus_set()
        self.bind("<Return>", lambda _: _ok())
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()


# ── Built-in Windows apps ─────────────────────────────────────────────────────

_BUILTIN_APPS = [
    ("Notepad",          "notepad.exe",                          "notepad.exe"),
    ("Command Prompt",   r"C:\Windows\System32\cmd.exe",         "cmd.exe"),
    ("PowerShell",       r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "powershell.exe"),
    ("Windows Terminal", "wt.exe",                               "WindowsTerminal.exe"),
    ("File Explorer",    r"C:\Windows\explorer.exe",             "explorer.exe"),
    ("Settings",         "ms-settings:",                         "SystemSettings.exe"),
    ("Calculator",       "calc.exe",                             "Calculator.exe"),
    ("Paint",            r"C:\Windows\System32\mspaint.exe",     "mspaint.exe"),
    ("Snipping Tool",    r"C:\Windows\System32\SnippingTool.exe","SnippingTool.exe"),
    ("Task Manager",     r"C:\Windows\System32\Taskmgr.exe",     "Taskmgr.exe"),
    ("WordPad",          r"C:\Program Files\Windows NT\Accessories\wordpad.exe", "wordpad.exe"),
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


# ── Registry scanner ──────────────────────────────────────────────────────────

def _scan_folder(folder: str) -> list[dict]:
    """Scan a folder (up to 3 levels deep) for exe files large enough to be a game/app."""
    results = []
    seen = set()
    base = pathlib.Path(folder)
    if not base.is_dir():
        return results
    for exe in list(base.glob("*.exe")) + \
               list(base.glob("*/*.exe")) + \
               list(base.glob("*/*/*.exe")):
        key = str(exe).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if exe.stat().st_size < 200_000:   # skip tiny helpers < 200 KB
                continue
        except Exception:
            continue
        voice_name = _to_voice_name(exe.stem)
        results.append({
            "display": f"{exe.stem}  ({base.name})",
            "name":    voice_name,
            "path":    str(exe),
            "proc":    exe.name,
        })
    results.sort(key=lambda x: x["display"].lower())
    return results


def _scan_registry() -> list[dict]:
    """Return list of {name, path, proc} dicts from the Windows registry."""
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
                # Skip system components / updates
                if _qval(sk, "SystemComponent") == "1":
                    continue
                if re.search(r"(update|hotfix|kb\d{6}|redistributable|runtime|sdk|"
                             r"driver|pack|framework)", display, re.I):
                    continue

                # Try to find an exe — InstallLocation, then DisplayIcon
                exe_path = ""
                loc = _qval(sk, "InstallLocation")
                if loc and pathlib.Path(loc).is_dir():
                    # Pick the first exe in the folder that matches the app name
                    folder = pathlib.Path(loc)
                    stem = re.sub(r"[^a-z0-9]", "", display.lower())
                    for exe in folder.glob("*.exe"):
                        if re.sub(r"[^a-z0-9]", "", exe.stem.lower()) in stem or \
                           stem in re.sub(r"[^a-z0-9]", "", exe.stem.lower()):
                            exe_path = str(exe)
                            break
                    if not exe_path:
                        # fallback: first exe in folder
                        exes = list(folder.glob("*.exe"))
                        if exes:
                            exe_path = str(exes[0])

                if not exe_path:
                    icon = _qval(sk, "DisplayIcon")
                    if icon:
                        # DisplayIcon may be "path.exe,0" or just "path.exe"
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


def _to_voice_name(display: str) -> str:
    """Turn a display name like 'Mozilla Firefox' into a voice command 'firefox'."""
    # Remove version numbers and punctuation
    name = re.sub(r"\d[\d.]*", "", display)
    name = re.sub(r"[^a-z ]", "", name.lower()).strip()
    # Take only the most meaningful word (usually last brand word)
    words = [w for w in name.split() if len(w) > 1]
    return words[-1] if words else name


# ── Scan dialog ───────────────────────────────────────────────────────────────

class ScanDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Scan Installed Apps")
        self.configure(bg="#1e1e2e")
        self.resizable(True, True)
        self.geometry("640x500")
        self._results = []
        self._vars = []
        self._build_ui()
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _build_ui(self):
        BG = "#1e1e2e"; CARD = "#2a2a3e"; ACC = "#7c6af7"
        FG = "#cdd6f4"; MUTED = "#585b70"; GRN = "#a6e3a1"

        tk.Label(self, text="🔍  Scan Installed Apps", bg=BG, fg=ACC,
                 font=("Segoe UI Semibold", 12)).pack(pady=(12, 2))
        self._status = tk.Label(self, text="Scanning registry…", bg=BG, fg=MUTED,
                                font=("Segoe UI", 9))
        self._status.pack()

        # Search bar
        search_row = tk.Frame(self, bg=BG)
        search_row.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(search_row, text="Filter:", bg=BG, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter())
        tk.Entry(search_row, textvariable=self._search_var,
                 bg="#313244", fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10), bd=4).pack(
            side="left", fill="x", expand=True, padx=(6, 0))

        # Checklist
        list_frame = tk.Frame(self, bg=CARD)
        list_frame.pack(fill="both", expand=True, padx=12, pady=8)

        self._canvas = tk.Canvas(list_frame, bg=CARD, highlightthickness=0)
        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._inner = tk.Frame(self._canvas, bg=CARD)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda e: self._canvas.configure(
                             scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(
                              self._canvas_win, width=e.width))
        self._canvas.bind_all("<MouseWheel>",
                              lambda e: self._canvas.yview_scroll(
                                  -1 * (e.delta // 120), "units"))

        # ── Extra search folders ──────────────────────────────────────────────
        folders_frame = tk.Frame(self, bg=BG)
        folders_frame.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(folders_frame, text="Extra search folders:", bg=BG, fg=MUTED,
                 font=("Segoe UI Semibold", 8)).pack(side="left")

        self._folders_lbl = tk.Label(folders_frame, text="", bg=BG, fg=FG,
                                     font=("Segoe UI", 8), anchor="w")
        self._folders_lbl.pack(side="left", padx=(6, 0), fill="x", expand=True)

        def _add_scan_folder():
            folder = filedialog.askdirectory(
                title="Select extra folder to search (e.g. D:\\SteamLibrary\\steamapps\\common)",
                parent=self)
            if not folder:
                return
            folders = user_config.get_scan_folders()
            if folder not in folders:
                folders.append(folder)
                user_config.set_scan_folders(folders)
            self._refresh_folders_lbl()
            self._status.config(text="Rescanning with new folder…")
            threading.Thread(target=self._do_scan, daemon=True).start()

        def _clear_scan_folders():
            user_config.set_scan_folders([])
            self._refresh_folders_lbl()
            self._status.config(text="Rescanning…")
            threading.Thread(target=self._do_scan, daemon=True).start()

        tk.Button(folders_frame, text="➕ Add Folder", command=_add_scan_folder,
                  bg=MUTED, fg="#fff", relief="flat",
                  font=("Segoe UI Semibold", 8), padx=6, pady=2,
                  cursor="hand2").pack(side="left", padx=(8, 0))
        tk.Button(folders_frame, text="✕ Clear", command=_clear_scan_folders,
                  bg=MUTED, fg="#fff", relief="flat",
                  font=("Segoe UI Semibold", 8), padx=6, pady=2,
                  cursor="hand2").pack(side="left", padx=(4, 0))
        self._refresh_folders_lbl()

        # Bottom buttons
        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=12, pady=(0, 10))

        def _sel_all():
            existing = set(user_config.get_apps().keys())
            for v, r in zip(self._vars, self._visible):
                if r["name"] not in existing:
                    v.set(True)

        tk.Button(bot, text="Select All", command=_sel_all,
                  bg=MUTED, fg="#fff", relief="flat",
                  font=("Segoe UI Semibold", 9), padx=10, pady=5,
                  cursor="hand2").pack(side="left")
        tk.Button(bot, text="Deselect All",
                  command=lambda: [v.set(False) for v in self._vars],
                  bg=MUTED, fg="#fff", relief="flat",
                  font=("Segoe UI Semibold", 9), padx=10, pady=5,
                  cursor="hand2").pack(side="left", padx=(6, 0))

        self._add_btn = tk.Button(
            bot, text="➕  Add Selected", command=self._add_selected,
            bg=ACC, fg="#fff", relief="flat",
            font=("Segoe UI Semibold", 10), padx=14, pady=5,
            cursor="hand2", state="disabled")
        self._add_btn.pack(side="right")

        self._count_lbl = tk.Label(bot, text="", bg=BG, fg=GRN,
                                   font=("Segoe UI", 9))
        self._count_lbl.pack(side="right", padx=(0, 10))

    def _do_scan(self):
        results = _scan_registry()
        for folder in user_config.get_scan_folders():
            results += _scan_folder(folder)
        # Deduplicate by path
        seen = set()
        deduped = []
        for r in results:
            k = r["path"].lower()
            if k not in seen:
                seen.add(k)
                deduped.append(r)
        deduped.sort(key=lambda x: x["display"].lower())
        self.after(0, lambda: self._populate(deduped))

    def _populate(self, results):
        self._results = results
        self._visible = results
        self._vars = []
        self._name_vars = []
        existing = set(user_config.get_apps().keys())
        for r in results:
            v = tk.BooleanVar(value=False)
            v.trace_add("write", self._update_count)
            self._vars.append(v)
            nv = tk.StringVar(value=r["name"])
            self._name_vars.append(nv)
            self._make_row(self._inner, r, v, nv, r["name"] in existing)
        self._status.config(text=f"Found {len(results)} apps")
        self._add_btn.config(state="normal")
        self._update_count()

    def _make_row(self, parent, r, var, name_var, already_added):
        BG = "#2a2a3e"; FG = "#cdd6f4"; MUTED = "#585b70"; GRN = "#a6e3a1"
        ENTRY_BG = "#1e1e2e"
        row = tk.Frame(parent, bg=BG, pady=3)
        row.pack(fill="x", padx=4, pady=1)
        cb = tk.Checkbutton(row, variable=var, bg=BG, activebackground=BG,
                            selectcolor="#313244", fg=FG,
                            disabledforeground=MUTED,
                            state="disabled" if already_added else "normal")
        cb.pack(side="left")
        info = tk.Frame(row, bg=BG)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=r["display"], bg=BG,
                 fg=MUTED if already_added else FG,
                 font=("Segoe UI Semibold", 9), anchor="w").pack(anchor="w")
        if already_added:
            tk.Label(info, text="  ✓ already added", bg=BG, fg=MUTED,
                     font=("Segoe UI", 8), anchor="w").pack(anchor="w")
        else:
            name_row = tk.Frame(info, bg=BG)
            name_row.pack(anchor="w", fill="x")
            tk.Label(name_row, text="  voice name:", bg=BG, fg=GRN,
                     font=("Segoe UI", 8)).pack(side="left")
            tk.Entry(name_row, textvariable=name_var, width=20,
                     bg=ENTRY_BG, fg=FG, insertbackground=FG,
                     relief="flat", font=("Segoe UI", 8), bd=2).pack(
                side="left", padx=(4, 0))

    def _filter(self):
        query = self._search_var.get().lower()
        for widget in self._inner.winfo_children():
            widget.destroy()
        existing = set(user_config.get_apps().keys())
        self._visible = [r for r in self._results
                         if query in r["display"].lower() or query in r["name"]]
        self._vars = []
        self._name_vars = []
        for r in self._visible:
            v = tk.BooleanVar(value=False)
            v.trace_add("write", self._update_count)
            self._vars.append(v)
            nv = tk.StringVar(value=r["name"])
            self._name_vars.append(nv)
            self._make_row(self._inner, r, v, nv, r["name"] in existing)
        self._update_count()

    def _update_count(self, *_):
        n = sum(v.get() for v in self._vars)
        self._count_lbl.config(text=f"{n} selected" if n else "")

    def _add_selected(self):
        selected = [(r, nv.get().strip().lower())
                    for r, v, nv in zip(self._visible, self._vars, self._name_vars)
                    if v.get()]
        if not selected:
            messagebox.showwarning("Nothing selected",
                                   "Tick at least one app to add.", parent=self)
            return
        # Validate names
        bad = [name for _, name in selected if not name]
        if bad:
            messagebox.showwarning("Empty name",
                                   "One or more voice names are empty. "
                                   "Please fill them in.", parent=self)
            return
        # Check for name conflicts
        existing = user_config.get_apps()
        conflicts = [name for _, name in selected if name in existing]
        if conflicts:
            names = ", ".join(f'"{n}"' for n in conflicts)
            if not messagebox.askyesno(
                    "Overwrite?",
                    f"These voice names already exist: {names}\n\nOverwrite them?",
                    parent=self):
                return
        for r, name in selected:
            user_config.add_entry(name, r["path"], r["proc"])
        messagebox.showinfo("Done",
                            f"Added {len(selected)} app(s).\n\n" +
                            "\n".join(f'  • {r["display"]} → "{name}"'
                                      for r, name in selected),
                            parent=self)
        self.destroy()


# ── Main app manager window ───────────────────────────────────────────────────

class AppManagerWindow(tk.Toplevel):

    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.title("Voice Commands — App Manager")
        self.resizable(False, False)
        self.configure(bg="#1e1e2e")
        self._build_ui()
        self._reload()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        BG       = "#1e1e2e"
        CARD     = "#2a2a3e"
        ACC      = "#7c6af7"
        FG       = "#cdd6f4"
        ENTRY_BG = "#313244"
        RED      = "#f38ba8"
        MUTED    = "#585b70"
        GRN      = "#a6e3a1"

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox",
                        fieldbackground=ENTRY_BG, background=CARD,
                        foreground=FG, selectbackground=ACC, arrowcolor=FG)
        style.map("TCombobox", fieldbackground=[("readonly", ENTRY_BG)])

        def lbl(parent, text, **kw):
            return tk.Label(parent, text=text, bg=BG, fg=FG,
                            font=("Segoe UI", 9), **kw)

        def inp(parent, width=42):
            return tk.Entry(parent, width=width, bg=ENTRY_BG, fg=FG,
                            insertbackground=FG, relief="flat",
                            font=("Segoe UI", 10), bd=4)

        def btn(parent, text, cmd, color=ACC):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg="#ffffff", activebackground=color,
                             activeforeground="#ffffff", relief="flat",
                             font=("Segoe UI Semibold", 9),
                             padx=10, pady=5, cursor="hand2", bd=0)

        def section(text):
            f = tk.Frame(self, bg=BG)
            tk.Label(f, text=text, bg=BG, fg=ACC,
                     font=("Segoe UI Semibold", 10)).pack(anchor="w")
            tk.Frame(f, bg=ACC, height=1).pack(fill="x", pady=(2, 6))
            return f

        PAD = 12

        # Config path info
        tk.Label(self, text=f"Config: {user_config.config_path()}",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(
            fill="x", padx=PAD, pady=(PAD, 0))

        # ── Quick-add buttons row ─────────────────────────────────────────────
        quick_row = tk.Frame(self, bg=BG)
        quick_row.pack(fill="x", padx=PAD, pady=(8, 0))
        btn(quick_row, "📂  Browse for App",
            self._browse_exe, color=MUTED).pack(side="left")
        btn(quick_row, "🌐  Add Website",
            self._add_website, color=MUTED).pack(side="left", padx=(8, 0))
        btn(quick_row, "📁  Add Folder",
            self._add_folder, color=MUTED).pack(side="left", padx=(8, 0))
        btn(quick_row, "🔍  Scan Installed Apps",
            self._open_scan, color=MUTED).pack(side="left", padx=(8, 0))

        # ── Add ───────────────────────────────────────────────────────────────
        add_sec = section("➕  Add / Edit Entry")
        add_sec.pack(fill="x", padx=PAD, pady=(6, 0))

        add_card = tk.Frame(add_sec, bg=CARD, padx=10, pady=10)
        add_card.pack(fill="x")

        lbl(add_card, "Voice command name  (e.g. notepad)").grid(
            row=0, column=0, sticky="w")
        lbl(add_card, "Exe / path  (e.g. notepad.exe or full path)").grid(
            row=0, column=1, sticky="w", padx=(10, 0))
        lbl(add_card, "Process name  (e.g. notepad.exe)").grid(
            row=0, column=2, sticky="w", padx=(10, 0))

        self.e_name = inp(add_card, 18)
        self.e_path = inp(add_card, 38)
        self.e_proc = inp(add_card, 26)

        self.e_name.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.e_path.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(2, 0))
        self.e_proc.grid(row=1, column=2, sticky="ew", padx=(10, 0), pady=(2, 0))

        btn(add_card, "Add Entry", self._on_add).grid(
            row=2, column=0, columnspan=3, pady=(10, 0), sticky="e")

        # ── Delete ────────────────────────────────────────────────────────────
        del_sec = section("🗑  Delete Entry")
        del_sec.pack(fill="x", padx=PAD, pady=(PAD, 0))

        del_card = tk.Frame(del_sec, bg=CARD, padx=10, pady=10)
        del_card.pack(fill="x")

        lbl(del_card, "Select entry to delete:").pack(anchor="w")

        self.combo_var = tk.StringVar()
        self.combo = ttk.Combobox(del_card, textvariable=self.combo_var,
                                  state="readonly", width=52,
                                  font=("Segoe UI", 10))
        self.combo.pack(fill="x", pady=(4, 8))

        self.preview = tk.Label(del_card, text="", bg=CARD, fg="#a6adc8",
                                font=("Consolas", 9), anchor="w", justify="left")
        self.preview.pack(fill="x", pady=(0, 8))
        self.combo.bind("<<ComboboxSelected>>", self._on_select)

        # Rename row
        rename_row = tk.Frame(del_card, bg=CARD)
        rename_row.pack(fill="x", pady=(0, 8))
        lbl(rename_row, "Rename voice command to:").pack(side="left")
        self.e_rename = inp(rename_row, width=20)
        self.e_rename.pack(side="left", padx=(8, 8))
        btn(rename_row, "Rename", self._on_rename, color=ACC).pack(side="left")

        btn(del_card, "Delete Selected", self._on_delete, color=RED).pack(anchor="e")

        # Status bar
        self.status = tk.Label(self, text="", bg=BG, fg=GRN,
                               font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", padx=PAD, pady=(PAD, PAD))

    # ── Browse / scan ─────────────────────────────────────────────────────────

    def _browse_exe(self):
        path = filedialog.askopenfilename(
            title="Select application executable",
            filetypes=[("Executables", "*.exe"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        p = pathlib.Path(path)
        voice_name = _to_voice_name(p.stem)
        proc = p.name
        self.e_name.delete(0, "end"); self.e_name.insert(0, voice_name)
        self.e_path.delete(0, "end"); self.e_path.insert(0, str(p))
        self.e_proc.delete(0, "end"); self.e_proc.insert(0, proc)
        self._flash(f'Auto-filled from {p.name} — edit the name if needed, then click Add Entry.')

    def _add_website(self):
        dlg = _SimpleDialog(self,
            title="Add Website",
            icon="🌐",
            fields=[
                ("Voice command name", "e.g.  youtube"),
                ("URL", "e.g.  https://www.youtube.com"),
            ])
        self.wait_window(dlg)
        if not dlg.result:
            return
        name, url = dlg.result
        name = name.strip().lower()
        url  = url.strip()
        if not name or not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        user_config.add_entry(name, url, "")   # empty proc — open-only
        self._reload()
        self._flash(f'✓  Added website "{name}" → {url}')

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder to open", parent=self)
        if not folder:
            return
        # Suggest a name from the folder's last component
        suggested = pathlib.Path(folder).name.lower().replace("_", " ").replace("-", " ")
        dlg = _SimpleDialog(self,
            title="Add Folder",
            icon="📁",
            fields=[("Voice command name", f"e.g.  {suggested}")],
            prefill=[suggested])
        self.wait_window(dlg)
        if not dlg.result:
            return
        name = dlg.result[0].strip().lower()
        if not name:
            return
        user_config.add_entry(name, folder, "explorer.exe")
        self._reload()
        self._flash(f'✓  Added folder "{name}" → {folder}')

    def _open_scan(self):
        dlg = ScanDialog(self)
        dlg.grab_set()
        self.wait_window(dlg)
        self._reload()

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _reload(self):
        apps  = user_config.get_apps()
        procs = user_config.get_proc_names()
        self._apps  = apps
        self._procs = procs
        names = sorted(apps.keys())
        self.combo["values"] = names
        if names:
            self.combo.set(names[0])
            self._show_preview(names[0])
            self.e_rename.delete(0, "end")
            self.e_rename.insert(0, names[0])
        else:
            self.combo.set("")
            self.preview.config(text="")
            self.e_rename.delete(0, "end")

    def _show_preview(self, name: str):
        path = self._apps.get(name, "—")
        proc = self._procs.get(name, "—")
        self.preview.config(text=f"  Path : {path}\n  Proc : {proc}")

    def _on_select(self, _=None):
        name = self.combo_var.get()
        self._show_preview(name)
        self.e_rename.delete(0, "end")
        self.e_rename.insert(0, name)

    def _flash(self, msg: str, color="#a6e3a1"):
        self.status.config(text=msg, fg=color)
        self.after(6000, lambda: self.status.config(text=""))

    def _on_add(self):
        name = self.e_name.get().strip().lower()
        path = self.e_path.get().strip()
        proc = self.e_proc.get().strip()
        if not name or not path or not proc:
            messagebox.showwarning("Missing fields",
                                   "Please fill in all three fields.", parent=self)
            return
        if name in self._apps:
            if not messagebox.askyesno("Overwrite?",
                                       f'"{name}" already exists. Overwrite it?',
                                       parent=self):
                return
        user_config.add_entry(name, path, proc)
        self._reload()
        self.e_name.delete(0, "end")
        self.e_path.delete(0, "end")
        self.e_proc.delete(0, "end")
        self._flash(f'✓  Added "{name}".')

    def _on_rename(self):
        old_name = self.combo_var.get()
        new_name = self.e_rename.get().strip().lower()
        if not old_name:
            return
        if not new_name:
            messagebox.showwarning("Empty name", "Please enter a new name.", parent=self)
            return
        if new_name == old_name:
            messagebox.showwarning("Same name", "The new name is the same as the current one.", parent=self)
            return
        if new_name in self._apps:
            if not messagebox.askyesno("Overwrite?",
                                       f'"{new_name}" already exists. Overwrite it?',
                                       parent=self):
                return
        # Copy entry under new name, delete old
        path = self._apps.get(old_name, "")
        proc = self._procs.get(old_name, "")
        user_config.delete_entry(old_name)
        user_config.add_entry(new_name, path, proc)
        self._reload()
        self._flash(f'✓  Renamed "{old_name}" → "{new_name}".')

    def _on_delete(self):
        name = self.combo_var.get()
        if not name:
            return
        if not messagebox.askyesno("Confirm delete",
                                   f'Delete "{name}" from your config?',
                                   parent=self):
            return
        user_config.delete_entry(name)
        self._reload()
        self._flash(f'✓  Deleted "{name}".', color="#f38ba8")


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    win = AppManagerWindow(root)
    win.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
