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


# ── Registry scanner ──────────────────────────────────────────────────────────

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
        self.after(0, lambda: self._populate(results))

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
        btn(quick_row, "🔍  Scan Installed Apps",
            self._open_scan, color=MUTED).pack(side="left", padx=(8, 0))
        tk.Label(quick_row,
                 text="← auto-fill fields  or  add many at once →",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(
            side="left", padx=(10, 0))

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
        else:
            self.combo.set("")
            self.preview.config(text="")

    def _show_preview(self, name: str):
        path = self._apps.get(name, "—")
        proc = self._procs.get(name, "—")
        self.preview.config(text=f"  Path : {path}\n  Proc : {proc}")

    def _on_select(self, _=None):
        self._show_preview(self.combo_var.get())

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
