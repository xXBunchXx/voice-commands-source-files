"""
Settings widget — embeds directly in the main window as a tab (SettingsWidget).
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import pathlib
import user_config

BG          = "#0a1020"
CARD        = "#0f1a2e"
ACC         = "#1a56db"   # button / header backgrounds
ACCENT_TEXT = "#4a8fe8"   # blue used as label / title text (readable on dark bg)
FG          = "#ffffff"
ENTRY_BG    = "#162033"
MUTED       = "#3d5470"
GRN         = "#4ade80"
RED         = "#f87171"
AMBER       = "#fbbf24"

_KNOWN_CONTEXTS = ("browser", "explorer", "editor", "any")
_CTX_ICONS      = {"browser": "🌐", "explorer": "📁", "editor": "✏️", "any": "🌍"}
_CTX_COLOURS    = {"browser": "#082048", "explorer": "#4ade80",
                   "editor": "#fbbf24",  "any":     "#082048"}

_MOD_MAP = {
    "control_l": "ctrl",  "control_r": "ctrl",
    "shift_l":   "shift", "shift_r":   "shift",
    "alt_l":     "alt",   "alt_r":     "alt",
    "super_l":   "windows", "super_r": "windows",
    "alt_gr":    "altgr",
}
_MODS = frozenset(_MOD_MAP.values()) | frozenset(_MOD_MAP.keys())

# Curated list of well-known Vosk English models
VOSK_MODELS = [
    {
        "name":  "vosk-model-small-en-us-0.15",
        "size":  "40 MB",
        "desc":  "Lightweight reference model — very low RAM, fast load.  "
                 "When downloaded alongside the medium model it acts as a noise filter, "
                 "cross-checking the first word of every recognised command to remove "
                 "words hallucinated from background noise.",
        "url":   "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "recommended": True,
    },
    {
        "name":  "vosk-model-en-us-0.22-lgraph",
        "size":  "128 MB",
        "desc":  "Medium lattice-graph model — best accuracy for command use.  ★ Default",
        "url":   "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip",
        "recommended": True,
        "is_default": True,
    },
]


def _norm_key(sym: str) -> str:
    s = sym.lower()
    return _MOD_MAP.get(s, s)


def _combo_str(held: set) -> str:
    order = ["windows", "ctrl", "shift", "alt", "altgr"]
    mods  = [m for m in order if m in held]
    rest  = sorted(k for k in held if k not in order and k not in _MODS)
    return "+".join(mods + rest)


def _value_preview(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("type") == "macro":
        n   = len(value.get("steps", []))
        rep = value.get("repeat", 1)
        s   = f"Macro · {n} step{'s' if n != 1 else ''}"
        if rep > 1:
            s += f" × {rep}"
        return s
    return str(value)


def _lbl(parent, text, fg=FG, font=("Segoe UI", 9), **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=fg, font=font, **kw)

def _inp(parent, width=28, **kw):
    return tk.Entry(parent, width=width, bg=ENTRY_BG, fg=FG,
                    insertbackground=FG, relief="flat",
                    font=("Segoe UI", 10), bd=4, **kw)

def _spin(parent, from_, to, var, width=6):
    return tk.Spinbox(parent, from_=from_, to=to, textvariable=var,
                      width=width, bg=ENTRY_BG, fg=FG,
                      buttonbackground=CARD, insertbackground=FG,
                      relief="flat", font=("Segoe UI", 10))

def _section(parent, title):
    f = tk.Frame(parent, bg=parent["bg"] if hasattr(parent, "__getitem__") else BG)
    tk.Label(f, text=title, bg=f["bg"], fg=FG,
             font=("Segoe UI Semibold", 10)).pack(anchor="w")
    tk.Frame(f, bg=ACCENT_TEXT, height=1).pack(fill="x", pady=(2, 8))
    return f

def _card(parent):
    return tk.Frame(parent, bg=CARD, padx=14, pady=10)

def _btn(parent, text, cmd, color=ACC, **kw):
    return tk.Button(parent, text=text, command=cmd,
                     bg=color, fg="#fff", activebackground=color,
                     activeforeground="#fff", relief="flat",
                     font=("Segoe UI Semibold", 9), cursor="hand2",
                     padx=10, pady=5, **kw)


def _all_context_values() -> list[str]:
    """Return all values valid for the context dropdown: known + custom groups + proc names."""
    vals = list(_KNOWN_CONTEXTS)
    groups = sorted(user_config.get_custom_groups().keys())
    if groups:
        vals += groups
    procs = sorted(set(user_config.get_proc_names().values()))
    if procs:
        vals += procs
    return vals


def _context_display_maps():
    """Maps for the context picker so apps show as their display name, not .exe.

    Returns (display_values, disp_to_val, val_to_disp):
      - display_values: list to show in the dropdown (apps as display names)
      - disp_to_val:    what the user picked/typed  -> stored context value
      - val_to_disp:    stored context value         -> label to display
    """
    proc_names = user_config.get_proc_names()          # {display: proc.exe}
    apps       = user_config.get_apps()                 # {display: target}

    # Every known app shows up, whether or not it has a process name yet.
    displays = set(apps.keys()) | set(proc_names.keys())
    disp_to_val, val_to_disp = {}, {}
    for disp in displays:
        pr  = (proc_names.get(disp) or "").strip()
        val = pr if pr else disp        # match by .exe if known, else by name
        disp_to_val[disp] = val
        val_to_disp[val]  = disp

    display_values  = list(_KNOWN_CONTEXTS)
    display_values += sorted(user_config.get_custom_groups().keys(), key=str.lower)
    display_values += sorted(displays, key=str.lower)
    return display_values, disp_to_val, val_to_disp


# ─────────────────────────────────────────────────────────────────────────────

class SettingsWidget(tk.Frame):
    """Embeds directly into a parent frame / notebook tab."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self._build_ui()
        self._load()
        self.after(200, self._load)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook",      background=BG, borderwidth=0)
        style.configure("TNotebook.Tab",  background=CARD, foreground=FG,
                        padding=[12, 6], font=("Segoe UI Semibold", 9))
        style.map("TNotebook.Tab",
                  background=[("selected", ACC)],
                  foreground=[("selected", "#ffffff")])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=12)

        self._tab_engine(nb)
        self._tab_commands(nb)
        self._tab_context(nb)
        self._tab_groups(nb)
        self._tab_audio(nb)
        self._tab_models(nb)

        self._status = tk.Label(self, text="", bg=BG, fg=GRN,
                                font=("Segoe UI", 9), anchor="w")
        self._status.pack(fill="x", padx=14, pady=(0, 10))

    # ── Engine tab ────────────────────────────────────────────────────────────

    def _tab_engine(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="⚙  Engine")

        sec = _section(frame, "Recognition")
        sec.pack(fill="x", padx=2, pady=(8, 0))
        card = _card(sec); card.pack(fill="x")

        _lbl(card, "Confidence threshold  (how sure it must be before acting)").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 2))
        self._conf_spin = _spin(card, 1, 100, tk.IntVar())
        self._conf_spin.grid(row=1, column=0, sticky="w")
        _lbl(card, "%", fg=MUTED).grid(row=1, column=1, sticky="w", padx=(4, 20))
        self._conf_note = _lbl(card, "", fg=MUTED)
        self._conf_note.grid(row=1, column=2, sticky="w")
        self._conf_spin.bind("<KeyRelease>", self._on_conf_change)
        self._conf_spin.bind("<<Increment>>", self._on_conf_change)
        self._conf_spin.bind("<<Decrement>>", self._on_conf_change)

        _lbl(card, "Cooldown  (ignore repeated command within this window)").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self._cooldown_spin = _spin(card, 0.0, 10.0, tk.DoubleVar(), width=7)
        self._cooldown_spin.grid(row=3, column=0, sticky="w")
        _lbl(card, "seconds", fg=MUTED).grid(row=3, column=1, sticky="w", padx=(4, 0))

        _lbl(card, "Response speed  (delay before a command fires — lower = snappier)").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self._response_spin = _spin(card, 40, 1000, tk.IntVar(), width=7)
        self._response_spin.grid(row=5, column=0, sticky="w")
        _lbl(card, "ms  (try 80–200; raise it if commands fire mid-sentence)",
             fg=MUTED).grid(row=5, column=1, sticky="w", padx=(4, 0))

        sec2 = _section(frame, "Close-App Undo Window")
        sec2.pack(fill="x", padx=2, pady=(14, 0))
        card2 = _card(sec2); card2.pack(fill="x")

        _lbl(card2, "Duration  (seconds to re-open a closed app)").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self._delay_spin = _spin(card2, 1, 60, tk.IntVar(), width=6)
        self._delay_spin.grid(row=1, column=0, sticky="w")
        _lbl(card2, "seconds", fg=MUTED).grid(row=1, column=1, sticky="w", padx=(4, 0))

        sec3 = _section(frame, "Status Overlay")
        sec3.pack(fill="x", padx=2, pady=(14, 0))
        card3 = _card(sec3); card3.pack(fill="x")

        self._overlay_enabled = tk.BooleanVar()
        tk.Checkbutton(card3, text="Show overlay when a command fires",
                       variable=self._overlay_enabled,
                       bg=CARD, fg=FG, selectcolor=ENTRY_BG,
                       activebackground=CARD, activeforeground=FG,
                       font=("Segoe UI", 9)).grid(row=0, column=0, columnspan=2, sticky="w")

        _lbl(card3, "Position:", fg=MUTED).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._overlay_pos = tk.StringVar()
        ttk.Combobox(card3, textvariable=self._overlay_pos,
                     state="readonly", width=18,
                     values=["bottom-right", "bottom-center", "bottom-left",
                             "top-right", "top-center", "top-left"]
                     ).grid(row=1, column=1, sticky="w", padx=(10, 0))

        sec4 = _section(frame, "Desktop Shortcut")
        sec4.pack(fill="x", padx=2, pady=(14, 0))
        card4 = _card(sec4); card4.pack(fill="x")

        shortcut_row = tk.Frame(card4, bg=CARD)
        shortcut_row.pack(fill="x")

        self._shortcut_var = tk.BooleanVar(value=False)

        shortcut_btn = _btn(shortcut_row, "🖥  Create Desktop Shortcut",
                            lambda: self._create_shortcut(),
                            color=MUTED)
        shortcut_btn.pack(side="right")

        def _on_shortcut_toggle(*_):
            if self._shortcut_var.get():
                shortcut_btn.config(bg=ACC, cursor="hand2", state="normal")
            else:
                shortcut_btn.config(bg=MUTED, cursor="arrow", state="disabled")

        tk.Checkbutton(shortcut_row,
                       text="I want a desktop shortcut for Echo",
                       variable=self._shortcut_var,
                       command=_on_shortcut_toggle,
                       bg=CARD, fg=FG, selectcolor=ENTRY_BG,
                       activebackground=CARD, activeforeground=FG,
                       font=("Segoe UI", 9)).pack(side="left", anchor="w")
        shortcut_btn.config(state="disabled")

        self._make_save_btn(frame, self._save_engine)

    def _create_shortcut(self):
        import sys, pathlib
        try:
            import win32com.client
            shell    = win32com.client.Dispatch("WScript.Shell")
            desktop  = pathlib.Path(shell.SpecialFolders("Desktop"))
            exe_path = pathlib.Path(sys.executable)
            lnk_path = desktop / "Echo.lnk"
            sc       = shell.CreateShortcut(str(lnk_path))
            sc.TargetPath       = str(exe_path)
            sc.WorkingDirectory = str(exe_path.parent)
            sc.Description      = "Echo voice commands"
            # Use icon.ico next to the exe if it exists, else fall back to the exe itself
            ico = exe_path.parent / "icon.ico"
            sc.IconLocation = f"{ico},0" if ico.exists() else f"{exe_path},0"
            sc.save()
            self._flash(f"✓  Shortcut created on Desktop: {lnk_path.name}")
        except ImportError:
            # win32com not available — fall back to pure PowerShell
            try:
                import subprocess, sys, pathlib
                exe_path = pathlib.Path(sys.executable)
                desktop  = pathlib.Path.home() / "Desktop"
                lnk_path = desktop / "Echo.lnk"
                ico      = exe_path.parent / "icon.ico"
                icon_str = str(ico) if ico.exists() else str(exe_path)
                ps = (
                    f'$ws=New-Object -ComObject WScript.Shell;'
                    f'$s=$ws.CreateShortcut("{lnk_path}");'
                    f'$s.TargetPath="{exe_path}";'
                    f'$s.WorkingDirectory="{exe_path.parent}";'
                    f'$s.IconLocation="{icon_str},0";'
                    f'$s.Description="Echo voice commands";'
                    f'$s.Save()'
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    check=True, capture_output=True,
                )
                self._flash(f"✓  Shortcut created on Desktop.")
            except Exception as e:
                self._flash(f"Failed to create shortcut: {e}", RED)
        except Exception as e:
            self._flash(f"Failed to create shortcut: {e}", RED)

    def _on_conf_change(self, _e=None):
        try:
            v = int(self._conf_spin.get())
            if v < 50:
                note = "⚠  Very low — many false triggers"
            elif v < 65:
                note = "Low — occasional false triggers"
            elif v <= 80:
                note = "Recommended"
            else:
                note = "High — may miss quiet speech"
            self._conf_note.config(text=note)
        except ValueError:
            pass

    def _save_engine(self):
        try:
            user_config.set_confidence_threshold(int(self._conf_spin.get()) / 100)
            user_config.set_cooldown(float(self._cooldown_spin.get()))
            user_config.set_response_delay(int(self._response_spin.get()) / 1000)
            user_config.set_close_delay(int(self._delay_spin.get()))
            user_config.set_overlay_enabled(self._overlay_enabled.get())
            user_config.set_overlay_position(self._overlay_pos.get())
            self._flash("✓  Engine settings saved — restart engine to apply.")
        except Exception as e:
            self._flash(f"Error: {e}", RED)

    # ── Volume tab ────────────────────────────────────────────────────────────

    def _build_volume_section(self, parent):
        sec = _section(parent, "Volume Step Words")
        sec.pack(fill="x", padx=2, pady=(12, 0))
        card = _card(sec); card.pack(fill="x")

        _lbl(card, 'Say  "volume up <word>"  or  "volume down <word>"  to change by that amount.',
             fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 8))

        self._vol_spins = {}
        for word in user_config.DEFAULT_VOLUME_STEPS:
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", pady=2)
            _lbl(row, f'"{word}"', width=8, anchor="w").pack(side="left")
            sp = _spin(row, 1, 100, tk.IntVar(), width=5)
            sp.pack(side="left", padx=(4, 0))
            _lbl(row, "%", fg=MUTED).pack(side="left", padx=(4, 0))
            self._vol_spins[word] = sp

    def _save_volume(self):
        try:
            steps = {w: int(sp.get()) for w, sp in self._vol_spins.items()}
            user_config.set_volume_steps(steps)
            self._flash("✓  Volume steps saved — restart engine to apply.")
        except Exception as e:
            self._flash(f"Error: {e}", RED)

    # ── Commands tab ──────────────────────────────────────────────────────────

    def _tab_commands(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="🗣  Commands")

        _lbl(frame,
             "Customise the trigger word for each command.\n"
             "Separate multiple trigger words with a comma (e.g. pause,play)",
             fg=MUTED, font=("Segoe UI", 8), justify="left").pack(
            anchor="w", padx=4, pady=(8, 4))

        canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=BG)
        cwin = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: self._set_scrollregion(canvas))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(cwin, width=e.width))
        self._cmd_canvas = canvas

        self._cmd_entries = {}
        self._cmd_delay_entries = {}
        groups = [
            ("Media",       ["skip", "previous", "rewind", "play_pause", "mute", "switch_audio"]),
            ("Keyboard",    ["copy", "paste", "save", "enter", "undo"]),
            ("App control", ["open", "close", "minimise", "maximise", "move", "merge"]),
            ("Modes",       ["set_mode"]),
            ("Engine",      ["diagnose", "stop_engine", "restart_engine"]),
        ]
        for group_name, keys in groups:
            is_app_ctrl = (group_name == "App control")
            sec = _section(inner, group_name)
            sec.pack(fill="x", padx=4, pady=(8, 0))
            card = _card(sec); card.pack(fill="x")
            if is_app_ctrl:
                _lbl(card,
                     "Wait (ms): saying the bare verb on its own fires after this long, "
                     "leaving that gap to add an app name.  0 = wait for you to stop talking.",
                     fg=MUTED, font=("Segoe UI", 8), wraplength=560,
                     justify="left").pack(anchor="w", pady=(0, 4))
                ms_label = "Wait (ms)"
            else:
                _lbl(card,
                     "Speed (ms): how long the word must hold steady before it fires.  "
                     "0 = use the global Response speed.  Set it low (e.g. 40) to make a "
                     "command like copy/paste near-instant.",
                     fg=MUTED, font=("Segoe UI", 8), wraplength=560,
                     justify="left").pack(anchor="w", pady=(0, 4))
                ms_label = "Speed (ms)"
            hdr = tk.Frame(card, bg=CARD)
            hdr.pack(fill="x", pady=(0, 4))
            w = 18
            _lbl(hdr, "Action", fg=FG, font=("Segoe UI Semibold", 8),
                 width=w, anchor="w").pack(side="left")
            _lbl(hdr, "Trigger word(s)", fg=FG, font=("Segoe UI Semibold", 8),
                 anchor="w").pack(side="left")
            _lbl(hdr, ms_label, fg=FG, font=("Segoe UI Semibold", 8),
                 anchor="w").pack(side="right")
            for key in keys:
                row = tk.Frame(card, bg=CARD)
                row.pack(fill="x", pady=2)
                _lbl(row, key.replace("_", " "), width=w, anchor="w").pack(side="left")
                e = _inp(row, width=22)
                e.pack(side="left")
                self._cmd_entries[key] = e
                dspin = tk.Spinbox(row, from_=0, to=2000, increment=10,
                                   textvariable=tk.IntVar(value=0), width=6, bg=ENTRY_BG,
                                   fg=FG, buttonbackground=CARD,
                                   insertbackground=FG, relief="flat",
                                   font=("Segoe UI", 10), justify="center")
                dspin.pack(side="right")
                self._cmd_delay_entries[key] = dspin

        self._make_save_btn(inner, self._save_commands)
        self._bind_wheel_tree(inner, canvas)

    # ── Scroll helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _set_scrollregion(canvas):
        """Pin the scroll region's top to 0 so you can't scroll above the
        first item into blank space."""
        bb = canvas.bbox("all")
        if bb:
            canvas.configure(scrollregion=(0, 0, bb[2], bb[3]))

    def _bind_wheel_tree(self, widget, canvas):
        """Bind the mouse wheel on *widget* and all its descendants so the
        wheel scrolls the canvas no matter which child is hovered."""
        def _on_wheel(e):
            bb = canvas.bbox("all")
            if bb and bb[3] > canvas.winfo_height():
                canvas.yview_scroll(-1 * (e.delta // 120), "units")
            return "break"

        def _bind(w):
            try:
                w.bind("<MouseWheel>", _on_wheel)
            except Exception:
                pass
            for c in w.winfo_children():
                _bind(c)
        _bind(widget)

    def _save_commands(self):
        try:
            words = {k: e.get().strip() for k, e in self._cmd_entries.items()}
            user_config.set_command_words(words)
            delays = {}
            for k, sp in self._cmd_delay_entries.items():
                try:
                    delays[k] = int(sp.get())
                except (ValueError, TypeError):
                    delays[k] = 0
            user_config.set_word_delays(delays)
            self._flash("✓  Command words saved — restart engine to apply.")
        except Exception as e:
            self._flash(f"Error: {e}", RED)

    # ── Context tab ───────────────────────────────────────────────────────────

    def _tab_context(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="🖱  Custom Commands")

        self._editing_mode   = "default"
        self._mode_group_vars = {}

        # ── Mode selector ─────────────────────────────────────────────────
        mode_row = tk.Frame(frame, bg=BG)
        mode_row.pack(fill="x", padx=4, pady=(8, 2))
        _lbl(mode_row, "Mode:", fg=FG, font=("Segoe UI Semibold", 9)).pack(side="left")
        self._mode_var = tk.StringVar(value="default")
        self._mode_combo = ttk.Combobox(mode_row, textvariable=self._mode_var,
                                        state="readonly", width=20,
                                        values=user_config.mode_names())
        self._mode_combo.pack(side="left", padx=(6, 8))
        self._mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_mode_change())
        _btn(mode_row, "➕  New", self._new_mode, MUTED).pack(side="left")
        _btn(mode_row, "🗑  Delete", self._delete_mode, RED).pack(side="left", padx=(6, 0))
        _lbl(mode_row, "Say \"set mode <name>\" to switch.",
             fg=MUTED, font=("Segoe UI", 8)).pack(side="right")

        # ── Per-mode enabled built-in groups (hidden for default) ─────────
        self._mode_groups_frame = tk.Frame(frame, bg=CARD, padx=8, pady=6)

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=4, pady=(6, 4))
        _lbl(top,
             "Custom commands for this mode, grouped by context "
             "(built-in group, custom group, or any .exe name).",
             fg=MUTED, font=("Segoe UI", 8)).pack(side="left")
        _btn(top, "➕  Add Command", self._add_context_cmd).pack(side="right")

        list_outer = tk.Frame(frame, bg=CARD)
        list_outer.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._ctx_canvas = tk.Canvas(list_outer, bg=BG, highlightthickness=0)
        ctx_sb = ttk.Scrollbar(list_outer, orient="vertical",
                               command=self._ctx_canvas.yview)
        self._ctx_canvas.configure(yscrollcommand=ctx_sb.set)
        ctx_sb.pack(side="right", fill="y")
        self._ctx_canvas.pack(side="left", fill="both", expand=True)

        self._ctx_inner = tk.Frame(self._ctx_canvas, bg=BG)
        _cwin = self._ctx_canvas.create_window(
            (0, 0), window=self._ctx_inner, anchor="nw")
        self._ctx_inner.bind(
            "<Configure>",
            lambda e: self._set_scrollregion(self._ctx_canvas))
        self._ctx_canvas.bind(
            "<Configure>",
            lambda e: self._ctx_canvas.itemconfig(_cwin, width=e.width))

        def _scroll(e):
            bb = self._ctx_canvas.bbox("all")
            if bb and bb[3] > self._ctx_canvas.winfo_height():
                self._ctx_canvas.yview_scroll(-1*(e.delta//120), "units")
        self._ctx_scroll = _scroll
        self._ctx_canvas.bind("<MouseWheel>", _scroll)
        self._ctx_inner.bind("<MouseWheel>", _scroll)

        bot = tk.Frame(frame, bg=BG)
        bot.pack(fill="x", padx=4, pady=(0, 4))
        _btn(bot, "🗑  Delete Selected", self._del_selected_ctx,
             color=RED).pack(side="left")
        _btn(bot, "📥  Import", self._import_cmds,
             color=MUTED).pack(side="left", padx=(8, 0))
        _btn(bot, "↺  Clear", self._reset_context_cmds,
             color=MUTED).pack(side="right")

        self._on_mode_change()

    def _reload_context_list(self):
        for w in self._ctx_inner.winfo_children():
            w.destroy()
        self._ctx_row_vars = []

        cmds   = self._mode_commands()
        groups = user_config.get_custom_groups()

        # Map process names (e.g. "firefox.exe") back to their app display name
        # so the list reads "firefox  (app)" instead of "firefox.exe  (app)".
        proc_to_display = {}
        for disp, pr in user_config.get_proc_names().items():
            if pr:
                proc_to_display[pr.lower()] = disp

        # Build display groups: known contexts, custom groups, then individual procs
        group_order: dict[str, list] = {c: [] for c in _KNOWN_CONTEXTS}
        for gname in sorted(groups.keys()):
            group_order.setdefault(gname, [])

        for phrase, contexts in sorted(cmds.items()):
            for ctx, value in contexts.items():
                group_order.setdefault(ctx, []).append((phrase, value))

        def _scroll_pass(e):
            self._ctx_canvas.yview_scroll(-1*(e.delta//120), "units")

        for ctx_name, entries in group_order.items():
            if not entries:
                continue

            is_custom_group = ctx_name in groups
            is_known        = ctx_name in _KNOWN_CONTEXTS
            icon  = _CTX_ICONS.get(ctx_name, "👤" if is_custom_group else "🔧")
            color = _CTX_COLOURS.get(ctx_name, "#082048" if is_custom_group else AMBER)

            if is_known:
                label = ctx_name
            elif is_custom_group:
                label = f"{ctx_name}  (group)"
            else:
                display = proc_to_display.get(ctx_name.lower(), ctx_name)
                label = f"{display}  (app)"

            hdr = tk.Frame(self._ctx_inner, bg=CARD)
            hdr.pack(fill="x", pady=(8, 1))
            tk.Label(hdr, text=f"  {icon}  {label}", bg=CARD, fg=FG,
                     font=("Segoe UI Semibold", 10),
                     padx=6, pady=5).pack(side="left")
            _btn(hdr, "📤  Export",
                 lambda c=ctx_name, l=label: self._export_cmds(c, l),
                 color=MUTED).pack(side="right", padx=(0, 6))
            hdr.bind("<MouseWheel>", _scroll_pass)

            for phrase, value in sorted(entries, key=lambda x: x[0]):
                var = tk.BooleanVar(value=False)
                row = tk.Frame(self._ctx_inner, bg=BG)
                row.pack(fill="x", padx=2, pady=1)

                cb = tk.Checkbutton(row, variable=var, bg=BG, fg=FG,
                                    activebackground=BG, activeforeground=FG,
                                    selectcolor=ENTRY_BG)
                cb.pack(side="left")
                tk.Label(row, text=phrase, bg=BG, fg=FG,
                         font=("Segoe UI", 9), width=24, anchor="w").pack(side="left")

                preview  = _value_preview(value)
                prev_fg  = AMBER if isinstance(value, dict) else MUTED
                tk.Label(row, text=preview, bg=BG, fg=prev_fg,
                         font=("Consolas", 8), width=26, anchor="w").pack(side="left")

                _btn(row, "✏ Edit",
                     lambda p=phrase, c=ctx_name, v=value: self._edit_cmd(p, c, v),
                     color=ACC).pack(side="right", padx=(2, 0))
                _btn(row, "✕",
                     lambda p=phrase, c=ctx_name: self._del_one_ctx(p, c),
                     color=RED).pack(side="right", padx=(2, 0))

                for w in (row, cb):
                    w.bind("<MouseWheel>", _scroll_pass)
                self._ctx_row_vars.append((var, phrase, ctx_name))

        # Recompute the scroll region and snap back to the top so the view
        # never starts scrolled into blank space above the first item.
        self._ctx_canvas.update_idletasks()
        self._set_scrollregion(self._ctx_canvas)
        self._ctx_canvas.yview_moveto(0)

    def _add_context_cmd(self):
        self._show_cmd_editor()

    def _edit_cmd(self, phrase, context, value):
        self._show_cmd_editor(phrase=phrase, context=context, value=value,
                              old_phrase=phrase, old_context=context)

    def _del_one_ctx(self, phrase, context):
        cmds = self._mode_commands()
        if phrase in cmds and context in cmds[phrase]:
            del cmds[phrase][context]
            if not cmds[phrase]:
                del cmds[phrase]
        self._mode_set_commands(cmds)
        self._reload_context_list()
        self._flash(f'✓  Deleted "{phrase}" [{context}].')

    def _del_selected_ctx(self):
        to_del = [(p, c) for v, p, c in self._ctx_row_vars if v.get()]
        if not to_del:
            self._flash("Select rows to delete first.", GRN)
            return
        cmds = self._mode_commands()
        for phrase, context in to_del:
            if phrase in cmds and context in cmds[phrase]:
                del cmds[phrase][context]
                if not cmds[phrase]:
                    del cmds[phrase]
        self._mode_set_commands(cmds)
        self._reload_context_list()
        self._flash(f"✓  Deleted {len(to_del)} rule(s).")

    def _reset_context_cmds(self):
        if messagebox.askyesno("Reset?",
                               "Clear all custom commands for this mode?\n"
                               "Your custom additions will be lost.",
                               parent=self.winfo_toplevel()):
            self._mode_set_commands({})
            self._reload_context_list()
            self._flash("✓  Cleared.")

    # ── Mode helpers ───────────────────────────────────────────────────────────

    def _mode_commands(self):
        if self._editing_mode == "default":
            return user_config.get_context_commands()
        return user_config.get_mode(self._editing_mode).get("commands", {})

    def _mode_set_commands(self, cmds):
        if self._editing_mode == "default":
            user_config.set_context_commands(cmds)
        else:
            groups = {g: v.get() for g, v in self._mode_group_vars.items()}
            if not groups:
                groups = user_config.get_mode(self._editing_mode).get("groups", {})
            user_config.save_mode(self._editing_mode, groups, cmds)

    def _on_mode_change(self):
        self._editing_mode = self._mode_var.get()
        self._reload_mode_groups()
        self._reload_context_list()

    def _reload_mode_groups(self):
        for w in self._mode_groups_frame.winfo_children():
            w.destroy()
        self._mode_group_vars = {}
        if self._editing_mode == "default":
            self._mode_groups_frame.pack_forget()
            return
        self._mode_groups_frame.pack(fill="x", padx=4, pady=(2, 0),
                                     after=self._mode_combo.master)
        md = user_config.get_mode(self._editing_mode)
        tk.Label(self._mode_groups_frame,
                 text="Built-in commands enabled in this mode:",
                 bg=CARD, fg=FG, font=("Segoe UI Semibold", 9)).pack(anchor="w")
        row = tk.Frame(self._mode_groups_frame, bg=CARD)
        row.pack(anchor="w", pady=(4, 0))
        _labels = {"media": "🎵 Media", "keyboard": "⌨ Keyboard",
                   "apps": "🪟 Apps/Windows", "layouts": "🗔 Layouts",
                   "audio": "🔊 Audio out"}
        for g in user_config.MODE_GROUPS:
            var = tk.BooleanVar(value=bool(md.get("groups", {}).get(g, False)))
            cb = tk.Checkbutton(row, text=_labels.get(g, g), variable=var,
                                bg=CARD, fg=FG, selectcolor=ENTRY_BG,
                                activebackground=CARD, activeforeground=FG,
                                font=("Segoe UI", 9),
                                command=self._save_mode_groups)
            cb.pack(side="left", padx=(0, 10))
            self._mode_group_vars[g] = var

    def _save_mode_groups(self):
        if self._editing_mode == "default":
            return
        groups = {g: v.get() for g, v in self._mode_group_vars.items()}
        cmds = user_config.get_mode(self._editing_mode).get("commands", {})
        user_config.save_mode(self._editing_mode, groups, cmds)

    def _new_mode(self):
        name = simpledialog.askstring(
            "New Mode", "Name for the new mode (e.g. film):",
            parent=self.winfo_toplevel())
        if not name:
            return
        name = name.strip().lower()
        if name == "default" or name in user_config.get_modes():
            self._flash("That mode name is taken.", RED)
            return
        if not name:
            return
        user_config.save_mode(name, {g: False for g in user_config.MODE_GROUPS}, {})
        self._mode_combo["values"] = user_config.mode_names()
        self._mode_var.set(name)
        self._on_mode_change()
        self._flash(f'✓  Created mode "{name}".')

    def _delete_mode(self):
        if self._editing_mode == "default":
            self._flash("The default mode cannot be deleted.", RED)
            return
        name = self._editing_mode
        if not messagebox.askyesno(
                "Delete mode?", f'Delete mode "{name}" and its commands?',
                parent=self.winfo_toplevel()):
            return
        user_config.delete_mode(name)
        self._mode_combo["values"] = user_config.mode_names()
        self._mode_var.set("default")
        self._on_mode_change()
        self._flash(f'✓  Deleted mode "{name}".')

    # ── Export / Import command files ──────────────────────────────────────────

    def _export_cmds(self, context, label=None):
        import json
        import re
        all_cmds = self._mode_commands()

        # Export just this group/app's commands.
        commands: dict = {}
        for phrase, ctxs in all_cmds.items():
            if context in ctxs:
                commands.setdefault(phrase, {})[context] = ctxs[context]

        if not commands:
            self._flash("Nothing to export.", RED)
            return

        # Carry along any custom-group definitions the commands reference,
        # so the recipient gets the group membership too.
        all_groups = user_config.get_custom_groups()
        used_ctx = {c for ctxs in commands.values() for c in ctxs}
        groups = {g: list(all_groups[g]) for g in used_ctx if g in all_groups}

        payload = {
            "echo_command_file": 1,
            "source_mode": self._editing_mode,
            "commands": commands,
            "groups": groups,
        }

        safe = re.sub(r"[^\w.-]+", "-", context).strip("-") or "commands"
        default_name = f"echo-{safe}-commands.json"
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title=f"Export commands for {label or context}",
            defaultextension=".json", initialfile=default_name,
            filetypes=[("Echo command file", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            messagebox.showerror("Export failed", str(e),
                                 parent=self.winfo_toplevel())
            return
        n = sum(len(c) for c in commands.values())
        self._flash(f"✓  Exported {n} command(s).")

    def _import_cmds(self):
        import json
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="Import commands",
            filetypes=[("Echo command file", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            messagebox.showerror("Import failed",
                                 f"Could not read file:\n{e}",
                                 parent=self.winfo_toplevel())
            return

        if not isinstance(payload, dict) or "commands" not in payload \
                or not isinstance(payload["commands"], dict):
            messagebox.showerror("Import failed",
                                 "This is not a valid Echo command file.",
                                 parent=self.winfo_toplevel())
            return

        new_cmds = payload["commands"]
        n = sum(len(c) for c in new_cmds.values() if isinstance(c, dict))
        if not messagebox.askyesno(
                "Import commands?",
                f'Import {n} command(s) into mode "{self._editing_mode}"?\n'
                "Existing commands with the same phrase + context "
                "will be overwritten.",
                parent=self.winfo_toplevel()):
            return

        # Merge commands into the current mode.
        cmds = self._mode_commands()
        for phrase, ctxs in new_cmds.items():
            if not isinstance(ctxs, dict):
                continue
            cmds.setdefault(phrase, {}).update(ctxs)
        self._mode_set_commands(cmds)

        # Merge any custom-group definitions that came with the file.
        groups_in = payload.get("groups", {})
        if isinstance(groups_in, dict) and groups_in:
            groups = user_config.get_custom_groups()
            for gname, procs in groups_in.items():
                if not isinstance(procs, list):
                    continue
                existing = groups.setdefault(gname, [])
                for p in procs:
                    if p not in existing:
                        existing.append(p)
            user_config.set_custom_groups(groups)

        self._reload_context_list()
        self._flash(f"✓  Imported {n} command(s).")

    # ── Command editor overlay ────────────────────────────────────────────────

    def _show_cmd_editor(self, *, phrase="", context="browser",
                         value=None, old_phrase=None, old_context=None):
        overlay = tk.Frame(self, bg="#05080f")
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()
        overlay.focus_set()

        card = tk.Frame(overlay, bg=CARD)
        card.place(relx=0.5, rely=0.5, anchor="center",
                   relwidth=0.84, relheight=0.93)

        title_text = "✏  Edit Command" if old_phrase else "➕  Add Command"
        title_bar = tk.Frame(card, bg=ACC, pady=8)
        title_bar.pack(fill="x", side="top")
        tk.Label(title_bar, text=title_text, bg=ACC, fg="#fff",
                 font=("Segoe UI Semibold", 12)).pack()

        cap_bar     = tk.Frame(card, bg="#0d1525", pady=5)
        cap_bar_lbl = tk.Label(cap_bar, text="", bg="#0d1525", fg=AMBER,
                               font=("Segoe UI Semibold", 9))
        cap_bar_lbl.pack()

        footer = tk.Frame(card, bg=CARD, padx=16, pady=10)
        footer.pack(fill="x", side="bottom")

        scroll_host = tk.Frame(card, bg=CARD)
        scroll_host.pack(fill="both", expand=True, side="top")

        body_canvas = tk.Canvas(scroll_host, bg=CARD, highlightthickness=0)
        body_sb = ttk.Scrollbar(scroll_host, orient="vertical",
                                command=body_canvas.yview)
        body_canvas.configure(yscrollcommand=body_sb.set)
        body_sb.pack(side="right", fill="y")
        body_canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(body_canvas, bg=CARD, padx=18, pady=12)
        _bwin = body_canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: body_canvas.configure(
                      scrollregion=body_canvas.bbox("all")))
        body_canvas.bind("<Configure>",
                         lambda e: body_canvas.itemconfig(_bwin, width=e.width))

        def _scroll_body(e):
            body_canvas.yview_scroll(-1*(e.delta//120), "units")
        body.bind("<MouseWheel>", _scroll_body)
        body_canvas.bind("<MouseWheel>", _scroll_body)

        if isinstance(value, dict) and value.get("type") == "macro":
            init_mode     = "macro"
            init_shortcut = ""
            init_steps    = [dict(s) for s in value.get("steps", [])]
            init_repeat   = int(value.get("repeat", 1))
        else:
            init_mode     = "shortcut"
            init_shortcut = value if isinstance(value, str) else ""
            init_steps    = []
            init_repeat   = 1

        init_speed   = user_config.get_context_delays().get(
            (old_phrase or phrase).strip().lower(), 0)
        ctx_display_values, ctx_disp_to_val, ctx_val_to_disp = _context_display_maps()
        imported     = {"flat": None}
        phrase_var   = tk.StringVar(value=phrase)
        context_var  = tk.StringVar(value=ctx_val_to_disp.get(context, context))
        mode_var     = tk.StringVar(value=init_mode)
        shortcut_var = tk.StringVar(value=init_shortcut)
        repeat_var   = tk.IntVar(value=init_repeat)
        speed_var    = tk.IntVar(value=init_speed)
        steps        = list(init_steps)

        def field_row(label, widget_fn):
            f = tk.Frame(body, bg=CARD)
            f.pack(fill="x", pady=4)
            tk.Label(f, text=label, bg=CARD, fg=MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w")
            widget_fn(f).pack(fill="x")

        # ── Import a whole command file into the chosen app/group ──────────────
        imp_bar = tk.Frame(body, bg=CARD)
        imp_bar.pack(fill="x", pady=(0, 6))
        imp_lbl = tk.Label(imp_bar, text="", bg=CARD, fg=AMBER,
                           font=("Segoe UI", 8), justify="left", wraplength=380)

        def _do_import_file():
            import json
            p = filedialog.askopenfilename(
                parent=overlay, title="Import command file",
                filetypes=[("Echo command file", "*.json"), ("All files", "*.*")])
            if not p:
                return
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except Exception as e:
                messagebox.showerror("Import failed", str(e), parent=overlay)
                return
            cin = payload.get("commands") if isinstance(payload, dict) else None
            if not isinstance(cin, dict) or not cin:
                messagebox.showerror("Import failed",
                                     "Not a valid Echo command file.", parent=overlay)
                return
            flat = {}
            for ph, ctxs in cin.items():
                if isinstance(ctxs, dict):
                    for v in ctxs.values():
                        flat[ph.strip().lower()] = v
            imported["flat"] = flat
            imp_lbl.config(
                text=f"✓  {len(flat)} command(s) loaded — set the app/group "
                     "below, then Save to add them all.")

        _btn(imp_bar, "📥  Import file…", _do_import_file,
             color=MUTED).pack(side="left")
        imp_lbl.pack(side="left", padx=(8, 0))

        field_row("Voice phrase  (what you say)",
                  lambda f: tk.Entry(f, textvariable=phrase_var,
                                     bg=ENTRY_BG, fg=FG, insertbackground=FG,
                                     relief="flat", font=("Segoe UI", 10), bd=4))

        # Context dropdown — includes known, custom groups, added app procs
        def _ctx_widget(f):
            cb = ttk.Combobox(f, textvariable=context_var, state="normal",
                              values=ctx_display_values, font=("Segoe UI", 10))
            return cb
        field_row("Context", _ctx_widget)
        tk.Label(body,
                 text="  Built-in: browser · explorer · editor · any\n"
                      "  Your groups appear here too — or type any .exe name (e.g. blender.exe)",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8), justify="left").pack(anchor="w")

        # Per-command speed override
        speed_row = tk.Frame(body, bg=CARD)
        speed_row.pack(fill="x", pady=(8, 0))
        tk.Label(speed_row, text="Speed (ms):", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(speed_row, from_=0, to=2000, increment=10, textvariable=speed_var,
                   width=6, bg=ENTRY_BG, fg=FG, buttonbackground=CARD,
                   insertbackground=FG, relief="flat", font=("Segoe UI", 10),
                   justify="center").pack(side="left", padx=(6, 0))
        tk.Label(speed_row, text="0 = use global Response speed; lower (e.g. 40) = near-instant",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

        tk.Frame(body, bg=MUTED, height=1).pack(fill="x", pady=(10, 8))

        sc_frame  = tk.Frame(body, bg=CARD)
        mac_frame = tk.Frame(body, bg=CARD)

        mode_row = tk.Frame(body, bg=CARD)
        mode_row.pack(fill="x", pady=(0, 8))
        tk.Label(mode_row, text="Action:", bg=CARD, fg=FG,
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=(0, 12))

        def _toggle_mode():
            if mode_var.get() == "shortcut":
                mac_frame.pack_forget()
                sc_frame.pack(fill="x", pady=4)
            else:
                sc_frame.pack_forget()
                mac_frame.pack(fill="x", pady=4)

        for lbl_text, val in [("Keyboard shortcut", "shortcut"),
                               ("Macro / sequence", "macro")]:
            tk.Radiobutton(mode_row, text=lbl_text, variable=mode_var, value=val,
                           bg=CARD, fg=FG, selectcolor=ENTRY_BG,
                           activebackground=CARD, activeforeground=FG,
                           font=("Segoe UI", 9),
                           command=_toggle_mode).pack(side="left", padx=(0, 14))

        # ── Shortcut section ──────────────────────────────────────────────────
        tk.Label(sc_frame,
                 text="Shortcut  (e.g.  ctrl+w  ·  f5  ·  windows+l  ·  ctrl+shift+t)",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 4))

        sc_inp_row = tk.Frame(sc_frame, bg=CARD)
        sc_inp_row.pack(fill="x")
        sc_entry = tk.Entry(sc_inp_row, textvariable=shortcut_var,
                            bg=ENTRY_BG, fg=FG, insertbackground=FG,
                            relief="flat", font=("Consolas", 10), bd=4)
        sc_entry.pack(side="left", fill="x", expand=True)

        sc_cap_btn = tk.Button(sc_inp_row, text="🎹  Capture",
                               bg=MUTED, fg="#fff", activebackground=MUTED,
                               activeforeground="#fff", relief="flat",
                               font=("Segoe UI Semibold", 9),
                               padx=8, pady=5, cursor="hand2")
        sc_cap_btn.pack(side="left", padx=(8, 0))

        # ── Macro section ─────────────────────────────────────────────────────
        rep_row = tk.Frame(mac_frame, bg=CARD)
        rep_row.pack(fill="x", pady=(0, 10))
        tk.Label(rep_row, text="Repeat:", bg=CARD, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(rep_row, from_=1, to=999, textvariable=repeat_var, width=5,
                   bg=ENTRY_BG, fg=FG, buttonbackground=CARD, insertbackground=FG,
                   relief="flat", font=("Segoe UI", 10)).pack(side="left", padx=(6, 0))
        tk.Label(rep_row, text="times", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))

        tk.Label(mac_frame, text="Steps:", bg=CARD, fg=FG,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w")

        steps_outer = tk.Frame(mac_frame, bg=ENTRY_BG)
        steps_outer.pack(fill="x", pady=(4, 0))
        steps_cv = tk.Canvas(steps_outer, bg=ENTRY_BG, highlightthickness=0, height=200)
        steps_sb2 = ttk.Scrollbar(steps_outer, orient="vertical", command=steps_cv.yview)
        steps_cv.configure(yscrollcommand=steps_sb2.set)
        steps_sb2.pack(side="right", fill="y")
        steps_cv.pack(side="left", fill="both", expand=True)
        steps_inner = tk.Frame(steps_cv, bg=ENTRY_BG)
        _swin = steps_cv.create_window((0, 0), window=steps_inner, anchor="nw")
        steps_inner.bind("<Configure>",
                         lambda e: steps_cv.configure(scrollregion=steps_cv.bbox("all")))
        steps_cv.bind("<Configure>",
                      lambda e: steps_cv.itemconfig(_swin, width=e.width))
        steps_cv.bind("<MouseWheel>", _scroll_body)
        steps_inner.bind("<MouseWheel>", _scroll_body)

        def _redraw_steps():
            for w in steps_inner.winfo_children():
                w.destroy()
            if not steps:
                tk.Label(steps_inner,
                         text="  No steps yet — use the buttons below.",
                         bg=ENTRY_BG, fg=MUTED, font=("Segoe UI", 8),
                         pady=10).pack(anchor="w")
            for idx, step in enumerate(steps):
                _make_step_row(idx, step)
            steps_cv.update_idletasks()
            steps_cv.yview_moveto(1.0)

        def _make_step_row(idx, step):
            alt    = idx % 2 == 0
            row_bg = "#1a2d44" if alt else ENTRY_BG
            e_bg   = CARD if alt else "#1e3550"

            f = tk.Frame(steps_inner, bg=row_bg, pady=4, padx=6)
            f.pack(fill="x")
            f.bind("<MouseWheel>", _scroll_body)

            tk.Label(f, text=f"{idx+1:2d}.", bg=row_bg, fg=MUTED,
                     font=("Consolas", 9), width=3).pack(side="left")

            t_color = ACC if step["type"] == "press" else AMBER
            def _toggle_type(i=idx):
                steps[i]["type"] = "wait" if steps[i]["type"] == "press" else "press"
                steps[i].setdefault("ms", 200)
                _redraw_steps()

            tk.Button(f, text=step["type"].upper(), command=_toggle_type,
                      bg=t_color, fg="#fff", activebackground=t_color,
                      activeforeground="#fff", relief="flat",
                      font=("Segoe UI Semibold", 8), padx=6, pady=2,
                      cursor="hand2", width=5).pack(side="left", padx=(2, 6))

            if step["type"] == "press":
                key_var = tk.StringVar(value=step.get("keys", ""))
                def _kchange(*_, i=idx, v=key_var): steps[i]["keys"] = v.get()
                key_var.trace_add("write", _kchange)
                e = tk.Entry(f, textvariable=key_var, bg=e_bg, fg=FG,
                             insertbackground=FG, relief="flat",
                             font=("Consolas", 9), bd=2, width=18)
                e.pack(side="left", padx=(0, 4))
                e.bind("<MouseWheel>", _scroll_body)
                def _cap_step(v=key_var): _start_capture(v, None)
                tk.Button(f, text="🎹", command=_cap_step,
                          bg=MUTED, fg="#fff", activebackground=MUTED,
                          activeforeground="#fff", relief="flat",
                          font=("Segoe UI", 8), padx=5, pady=2,
                          cursor="hand2").pack(side="left", padx=(0, 6))
            else:
                ms_var = tk.StringVar(value=str(step.get("ms", 200)))
                def _mschange(*_, i=idx, v=ms_var):
                    try: steps[i]["ms"] = max(1, int(v.get()))
                    except ValueError: pass
                ms_var.trace_add("write", _mschange)
                e = tk.Entry(f, textvariable=ms_var, bg=e_bg, fg=FG,
                             insertbackground=FG, relief="flat",
                             font=("Consolas", 9), bd=2, width=7)
                e.pack(side="left", padx=(0, 4))
                e.bind("<MouseWheel>", _scroll_body)
                tk.Label(f, text="ms", bg=row_bg, fg=MUTED,
                         font=("Segoe UI", 8)).pack(side="left", padx=(0, 8))

            def _up(i=idx):
                if i > 0: steps[i-1], steps[i] = steps[i], steps[i-1]; _redraw_steps()
            def _dn(i=idx):
                if i < len(steps)-1: steps[i+1], steps[i] = steps[i], steps[i+1]; _redraw_steps()
            def _del(i=idx): steps.pop(i); _redraw_steps()
            for txt, cmd, col in [("↑", _up, MUTED), ("↓", _dn, MUTED), ("✕", _del, RED)]:
                tk.Button(f, text=txt, command=cmd,
                          bg=col, fg="#fff", activebackground=col,
                          activeforeground="#fff", relief="flat",
                          font=("Segoe UI", 8), padx=5, pady=2,
                          cursor="hand2").pack(side="right", padx=1)

        add_row   = tk.Frame(mac_frame, bg=CARD)
        add_row.pack(fill="x", pady=(8, 0))
        rec_state = {"on": False}
        rec_btn_r = [None]

        def _add_press_step(): steps.append({"type": "press", "keys": ""}); _redraw_steps()
        def _add_wait_step():  steps.append({"type": "wait",  "ms": 200}); _redraw_steps()
        def _toggle_record():
            if rec_state["on"]: _stop_record()
            else:               _start_record()

        for txt, cmd in [("+ Press", _add_press_step), ("+ Wait", _add_wait_step)]:
            _btn(add_row, txt, cmd).pack(side="left", padx=(0, 6))

        rec_b = tk.Button(add_row, text="🔴  Record", command=_toggle_record,
                          bg=MUTED, fg="#fff", activebackground=MUTED,
                          activeforeground="#fff", relief="flat",
                          font=("Segoe UI Semibold", 9), padx=8, pady=5, cursor="hand2")
        rec_b.pack(side="left")
        rec_btn_r[0] = rec_b

        tk.Label(mac_frame,
                 text="Record: click here to focus, then press key combos — each combo = one step.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        _redraw_steps()

        if init_mode == "shortcut":
            sc_frame.pack(fill="x", pady=4)
        else:
            mac_frame.pack(fill="x", pady=4)

        # ── Key capture ───────────────────────────────────────────────────────
        _held    = set()
        _cap_tgt = {"var": None}

        def _start_capture(target_var, _btn_ref):
            _cap_tgt["var"] = target_var
            _held.clear()
            overlay.focus_set()
            cap_bar_lbl.config(text="🎹  Hold your combo then release the last key…")
            cap_bar.pack(fill="x", after=title_bar)

        def _stop_capture_mode():
            _cap_tgt["var"] = None
            _held.clear()
            cap_bar.pack_forget()

        sc_cap_btn.config(command=lambda: _start_capture(shortcut_var, None))

        def _start_record():
            rec_state["on"] = True
            rec_btn_r[0].config(text="⏹  Stop", bg=RED)
            _held.clear()
            overlay.focus_set()
            cap_bar_lbl.config(text="🔴  Recording — press combos. Click ⏹ Stop when done.")
            cap_bar.pack(fill="x", after=title_bar)

        def _stop_record():
            rec_state["on"] = False
            rec_btn_r[0].config(text="🔴  Record", bg=MUTED)
            _held.clear()
            cap_bar.pack_forget()

        def _on_kp(e):
            sym = _norm_key(e.keysym)
            _held.add(sym)
            if rec_state["on"] and sym not in _MODS:
                combo = _combo_str(_held)
                _held.clear()
                steps.append({"type": "press", "keys": combo})
                _redraw_steps()

        def _on_kr(e):
            sym = _norm_key(e.keysym)
            if (not rec_state["on"] and _cap_tgt["var"] is not None
                    and sym not in _MODS and _held):
                _cap_tgt["var"].set(_combo_str(_held))
                _stop_capture_mode()
            _held.discard(sym)

        overlay.bind("<KeyPress>",   _on_kp, add="+")
        overlay.bind("<KeyRelease>", _on_kr, add="+")

        # ── Footer ────────────────────────────────────────────────────────────
        def _cancel(_e=None):
            _stop_record(); _stop_capture_mode(); overlay.destroy()

        def _save(_e=None):
            phrase_txt  = phrase_var.get().strip().lower()
            context_raw = context_var.get().strip()
            # Map the display name back to the stored value (e.g. Opera -> opera.exe)
            context_txt = ctx_disp_to_val.get(context_raw, context_raw)

            # Bulk import: drop every loaded command into the chosen app/group.
            if imported["flat"]:
                if not context_txt:
                    messagebox.showwarning("Missing context",
                        "Choose an app or group to import into.", parent=overlay)
                    return
                cmds = self._mode_commands()
                for ph, v in imported["flat"].items():
                    cmds.setdefault(ph, {})[context_txt] = v
                self._mode_set_commands(cmds)
                overlay.destroy()
                self._reload_context_list()
                self._flash(f'✓  Imported {len(imported["flat"])} '
                            f'command(s) into [{context_txt}]')
                return

            if not phrase_txt or not context_txt:
                messagebox.showwarning("Missing fields",
                    "Voice phrase and context are required.", parent=overlay)
                return
            if mode_var.get() == "shortcut":
                sc = shortcut_var.get().strip().lower()
                if not sc:
                    messagebox.showwarning("Missing shortcut",
                        "Enter a shortcut or switch to Macro mode.", parent=overlay)
                    return
                new_value = sc
            else:
                if not steps:
                    messagebox.showwarning("Empty macro",
                        "Add at least one Press step.", parent=overlay)
                    return
                new_value = {"type": "macro", "repeat": max(1, repeat_var.get()),
                             "steps": [dict(s) for s in steps]}

            cmds = self._mode_commands()
            if old_phrase and old_context:
                if old_phrase in cmds and old_context in cmds[old_phrase]:
                    del cmds[old_phrase][old_context]
                    if not cmds[old_phrase]:
                        del cmds[old_phrase]
            cmds.setdefault(phrase_txt, {})[context_txt] = new_value
            self._mode_set_commands(cmds)
            # Per-command speed override (moves with the phrase if it was renamed)
            if old_phrase and old_phrase.strip().lower() != phrase_txt:
                user_config.set_context_delay(old_phrase, 0)
            try:
                user_config.set_context_delay(phrase_txt, int(speed_var.get()))
            except Exception:
                pass
            overlay.destroy()
            self._reload_context_list()
            verb = "Updated" if old_phrase else "Added"
            self._flash(f'✓  {verb} "{phrase_txt}" [{context_txt}]')

        overlay.bind("<Escape>", _cancel)
        _btn(footer, "Save", _save).pack(side="right", padx=(8, 0))
        _btn(footer, "Cancel", _cancel, color=MUTED).pack(side="right")

    # ── Groups tab ────────────────────────────────────────────────────────────

    def _tab_groups(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="👥  Groups")

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=4, pady=(8, 4))
        _lbl(top,
             "Define named groups of apps that share context commands.\n"
             'e.g. a "music" group with Spotify + YouTube Music → one set of commands for both.',
             fg=MUTED, font=("Segoe UI", 8), justify="left").pack(side="left")

        list_outer = tk.Frame(frame, bg=CARD)
        list_outer.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._grp_canvas = tk.Canvas(list_outer, bg=BG, highlightthickness=0)
        grp_sb = ttk.Scrollbar(list_outer, orient="vertical",
                               command=self._grp_canvas.yview)
        self._grp_canvas.configure(yscrollcommand=grp_sb.set)
        grp_sb.pack(side="right", fill="y")
        self._grp_canvas.pack(side="left", fill="both", expand=True)

        self._grp_inner = tk.Frame(self._grp_canvas, bg=BG)
        _gwin = self._grp_canvas.create_window(
            (0, 0), window=self._grp_inner, anchor="nw")
        self._grp_inner.bind(
            "<Configure>",
            lambda e: self._grp_canvas.configure(
                scrollregion=self._grp_canvas.bbox("all")))
        self._grp_canvas.bind(
            "<Configure>",
            lambda e: self._grp_canvas.itemconfig(_gwin, width=e.width))

        def _scroll(e):
            self._grp_canvas.yview_scroll(-1*(e.delta//120), "units")
        self._grp_canvas.bind("<MouseWheel>", _scroll)
        self._grp_inner.bind("<MouseWheel>", _scroll)

        bot = tk.Frame(frame, bg=BG)
        bot.pack(fill="x", padx=4, pady=(0, 4))
        _btn(bot, "➕  New Group",
             lambda: self._show_group_editor()).pack(side="left")

    def _reload_groups_list(self):
        for w in self._grp_inner.winfo_children():
            w.destroy()

        groups = user_config.get_custom_groups()
        if not groups:
            tk.Label(self._grp_inner,
                     text="  No custom groups yet.  Click ➕ New Group to create one.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 9), pady=20).pack(anchor="w")
            return

        def _scroll_pass(e):
            self._grp_canvas.yview_scroll(-1*(e.delta//120), "units")

        for gname, procs in sorted(groups.items()):
            card = tk.Frame(self._grp_inner, bg=CARD, padx=12, pady=10)
            card.pack(fill="x", padx=4, pady=4)
            card.bind("<MouseWheel>", _scroll_pass)

            top_row = tk.Frame(card, bg=CARD)
            top_row.pack(fill="x")
            tk.Label(top_row, text=f"👥  {gname}", bg=CARD, fg=FG,
                     font=("Segoe UI Semibold", 11)).pack(side="left")

            _btn(top_row, "✏ Edit",
                 lambda n=gname, p=procs: self._show_group_editor(name=n, procs=p),
                 color=ACC).pack(side="right", padx=(4, 0))
            _btn(top_row, "🗑 Delete",
                 lambda n=gname: self._del_group(n),
                 color=RED).pack(side="right")

            members = "  ·  ".join(procs) if procs else "  (no members)"
            tk.Label(card, text=members, bg=CARD, fg=MUTED,
                     font=("Consolas", 8), anchor="w",
                     wraplength=500, justify="left").pack(anchor="w", pady=(4, 0))

    def _del_group(self, name):
        groups = user_config.get_custom_groups()
        groups.pop(name, None)
        user_config.set_custom_groups(groups)
        self._reload_groups_list()
        self._reload_context_list()
        self._flash(f'✓  Deleted group "{name}".')

    def _show_group_editor(self, *, name="", procs=None):
        """Inline overlay to create or edit a custom app group."""
        if procs is None:
            procs = []
        old_name = name if name else None

        overlay = tk.Frame(self, bg="#05080f")
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()
        overlay.focus_set()

        card = tk.Frame(overlay, bg=CARD, padx=24, pady=20)
        card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.72)

        title = "✏  Edit Group" if old_name else "➕  New Group"
        tk.Label(card, text=title, bg=CARD, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(pady=(0, 14))

        # Group name
        tk.Label(card, text="Group name  (one word, used as the context in commands)",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        name_var = tk.StringVar(value=name)
        tk.Entry(card, textvariable=name_var, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief="flat",
                 font=("Segoe UI", 10), bd=4).pack(fill="x", pady=(2, 12))

        # App member checkboxes — scrollable so large app lists don't overflow
        tk.Label(card, text="Member apps  (check each app to include in this group):",
                 bg=CARD, fg=FG, font=("Segoe UI Semibold", 9)).pack(anchor="w")
        tk.Label(card, text="  Commands targeting this group fire when any of these apps is focused.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 6))

        members_outer = tk.Frame(card, bg=ENTRY_BG)
        members_outer.pack(fill="x")

        members_cv  = tk.Canvas(members_outer, bg=ENTRY_BG, highlightthickness=0, height=220)
        members_sb  = ttk.Scrollbar(members_outer, orient="vertical",
                                    command=members_cv.yview)
        members_cv.configure(yscrollcommand=members_sb.set)
        members_sb.pack(side="right", fill="y")
        members_cv.pack(side="left", fill="both", expand=True)

        members_frame = tk.Frame(members_cv, bg=ENTRY_BG, padx=12, pady=8)
        _mwin = members_cv.create_window((0, 0), window=members_frame, anchor="nw")
        members_frame.bind("<Configure>",
                           lambda e: members_cv.configure(
                               scrollregion=members_cv.bbox("all")))
        members_cv.bind("<Configure>",
                        lambda e: members_cv.itemconfig(_mwin, width=e.width))

        def _mscroll(e):
            members_cv.yview_scroll(-1*(e.delta//120), "units")
        members_cv.bind("<MouseWheel>", _mscroll)
        members_frame.bind("<MouseWheel>", _mscroll)

        all_procs = user_config.get_proc_names()   # {name: proc}
        member_vars: dict[str, tk.BooleanVar] = {}

        if not all_procs:
            tk.Label(members_frame, text="No apps added yet — add apps in the 📦 Apps tab first.",
                     bg=ENTRY_BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        else:
            for app_name in sorted(all_procs.keys()):
                proc = all_procs[app_name]
                var  = tk.BooleanVar(value=(proc in procs or app_name in procs))
                member_vars[proc] = var
                row = tk.Frame(members_frame, bg=ENTRY_BG)
                row.pack(fill="x", pady=1)
                row.bind("<MouseWheel>", _mscroll)
                tk.Checkbutton(row, variable=var, bg=ENTRY_BG,
                               activebackground=ENTRY_BG,
                               selectcolor=CARD).pack(side="left")
                tk.Label(row, text=f"{app_name}",
                         bg=ENTRY_BG, fg=FG, font=("Segoe UI", 9),
                         width=18, anchor="w").pack(side="left")
                tk.Label(row, text=proc, bg=ENTRY_BG, fg=MUTED,
                         font=("Consolas", 8)).pack(side="left")

        # Buttons
        btn_row = tk.Frame(card, bg=CARD)
        btn_row.pack(fill="x", pady=(14, 0))

        def _cancel(_e=None): overlay.destroy()

        def _save(_e=None):
            gname = name_var.get().strip().lower().replace(" ", "_")
            if not gname:
                messagebox.showwarning("Name required",
                    "Enter a group name.", parent=overlay)
                return
            selected_procs = [p for p, v in member_vars.items() if v.get()]
            groups = user_config.get_custom_groups()
            if old_name and old_name != gname:
                groups.pop(old_name, None)
            groups[gname] = selected_procs
            user_config.set_custom_groups(groups)
            overlay.destroy()
            self._reload_groups_list()
            self._reload_context_list()
            verb = "Updated" if old_name else "Created"
            self._flash(f'✓  {verb} group "{gname}" with {len(selected_procs)} app(s).')

        overlay.bind("<Escape>", _cancel)
        _btn(btn_row, "Save", _save).pack(side="right", padx=(8, 0))
        _btn(btn_row, "Cancel", _cancel, color=MUTED).pack(side="right")

    # ── Audio tab ───────────────────────────────────────────────────────────

    def _tab_audio(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="🔊  Audio")

        sec = _section(frame, "Output Devices")
        sec.pack(fill="x", padx=2, pady=(8, 0))
        top = tk.Frame(sec, bg=BG)
        top.pack(fill="x", pady=(0, 4))
        _lbl(top,
             "Give a spoken name to each output device you want to switch to, then say "
             "\"change to <name>\" to make it the default.  Leave a name blank to ignore it.",
             fg=MUTED, font=("Segoe UI", 8), justify="left", wraplength=520).pack(side="left")
        _btn(top, "↻  Refresh", self._reload_audio_list).pack(side="right")

        list_outer = tk.Frame(frame, bg=CARD, height=200)
        list_outer.pack(fill="x", padx=2, pady=(2, 0))
        list_outer.pack_propagate(False)
        self._audio_canvas = tk.Canvas(list_outer, bg=CARD, highlightthickness=0)
        sb = ttk.Scrollbar(list_outer, orient="vertical", command=self._audio_canvas.yview)
        self._audio_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._audio_canvas.pack(side="left", fill="both", expand=True)
        self._audio_inner = tk.Frame(self._audio_canvas, bg=CARD)
        _awin = self._audio_canvas.create_window((0, 0), window=self._audio_inner, anchor="nw")
        self._audio_inner.bind("<Configure>",
                               lambda e: self._audio_canvas.configure(
                                   scrollregion=self._audio_canvas.bbox("all")))
        self._audio_canvas.bind("<Configure>",
                                lambda e: self._audio_canvas.itemconfig(_awin, width=e.width))

        # Volume controls live here too (audio-related).
        self._build_volume_section(frame)

        self._make_save_btn(frame, self._save_audio_tab)
        self._audio_name_vars = {}   # device_id -> (StringVar, friendly_name)
        self._reload_audio_list()

    def _save_audio_tab(self):
        self._save_audio()
        self._save_volume()
        self._flash("✓  Audio settings saved — restart engine to apply.")

    def _reload_audio_list(self):
        for w in self._audio_inner.winfo_children():
            w.destroy()
        self._audio_name_vars = {}
        try:
            import audio_devices
            devices = audio_devices.list_output_devices()
        except Exception as e:
            tk.Label(self._audio_inner, text=f"Couldn't read audio devices: {e}",
                     bg=CARD, fg=RED, font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=8)
            return
        if not devices:
            tk.Label(self._audio_inner, text="No output devices found.",
                     bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=8)
            return
        saved = user_config.get_audio_devices()
        id_to_name = {v.get("id"): k for k, v in saved.items()}
        for dev_id, friendly in devices:
            row = tk.Frame(self._audio_inner, bg=CARD)
            row.pack(fill="x", padx=6, pady=3)
            tk.Label(row, text=friendly, bg=CARD, fg=FG, width=42, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, text="say:", bg=CARD, fg=MUTED,
                     font=("Segoe UI", 8)).pack(side="left", padx=(8, 2))
            var = tk.StringVar(value=id_to_name.get(dev_id, ""))
            ent = tk.Entry(row, textvariable=var, width=18, bg=ENTRY_BG, fg=FG,
                           insertbackground=FG, relief="flat", font=("Segoe UI", 9), bd=4)
            ent.pack(side="left")
            self._audio_name_vars[dev_id] = (var, friendly)

    def _save_audio(self):
        try:
            devices = {}
            for dev_id, (var, friendly) in self._audio_name_vars.items():
                spoken = var.get().strip().lower()
                if spoken:
                    devices[spoken] = {"id": dev_id, "name": friendly}
            user_config.set_audio_devices(devices)
            n = len(devices)
            self._flash(f"✓  Saved {n} audio device name(s) — restart engine to apply.")
        except Exception as e:
            self._flash(f"Error: {e}", RED)

    # ── Models tab ────────────────────────────────────────────────────────────

    def _tab_models(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="📥  Models")

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=4, pady=(8, 4))
        _lbl(top,
             "Download and switch between Vosk speech-recognition models.\n"
             "Larger models are more accurate but use more RAM and take longer to load.",
             fg=MUTED, font=("Segoe UI", 8), justify="left").pack(anchor="w")

        # Current model indicator
        cur_card = tk.Frame(frame, bg=CARD, padx=14, pady=8)
        cur_card.pack(fill="x", padx=4, pady=(0, 8))
        tk.Label(cur_card, text="Active model:", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        self._active_model_lbl = tk.Label(cur_card, text="",
                                          bg=CARD, fg=GRN,
                                          font=("Consolas", 9), anchor="w")
        self._active_model_lbl.pack(anchor="w")

        # Model cards — scrollable
        canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=BG)
        cwin = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(cwin, width=e.width))

        def _scroll(e): canvas.yview_scroll(-1*(e.delta//120), "units")
        canvas.bind("<MouseWheel>", _scroll)
        inner.bind("<MouseWheel>", _scroll)

        self._model_cards = {}   # name → dict of widgets

        for m in VOSK_MODELS:
            self._build_model_card(inner, m, _scroll)

        self._refresh_model_statuses()

    def _build_model_card(self, parent, m: dict, scroll_fn):
        name       = m["name"]
        is_default = m.get("is_default", False)
        is_small   = (name == "vosk-model-small-en-us-0.15")

        card = tk.Frame(parent, bg=CARD, padx=14, pady=10)
        card.pack(fill="x", padx=4, pady=4)
        card.bind("<MouseWheel>", scroll_fn)

        # Header row
        hdr = tk.Frame(card, bg=CARD)
        hdr.pack(fill="x")
        tk.Label(hdr, text=name, bg=CARD, fg=FG,
                 font=("Segoe UI Semibold", 10)).pack(side="left")
        tk.Label(hdr, text=m["size"], bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))

        tk.Label(card, text=m["desc"], bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w",
                 wraplength=600, justify="left").pack(anchor="w", pady=(2, 6))

        # Noise-filter checkbox — only on the default (medium) card
        if is_default:
            import user_config as _uc
            _small_name = "vosk-model-small-en-us-0.15"
            noise_var = tk.BooleanVar(value=_uc.get_dual_model_check())

            def _on_noise_toggle():
                _uc.set_dual_model_check(noise_var.get())

            noise_row = tk.Frame(card, bg=CARD)
            noise_row.pack(anchor="w", pady=(0, 4))
            tk.Checkbutton(
                noise_row, text="Enable noise filter  (uses the small model to remove random words heard at the start of commands)",
                variable=noise_var, command=_on_noise_toggle,
                bg=CARD, fg=FG, selectcolor=ENTRY_BG,
                activebackground=CARD, activeforeground=FG,
                font=("Segoe UI", 8), cursor="hand2",
            ).pack(side="left")

            # Indicate whether the small model is already present
            def _update_noise_hint():
                try:
                    exe_dir = pathlib.Path(_uc.get_model_path()).parent
                    present = (exe_dir / _small_name).is_dir()
                except Exception:
                    present = False
                hint_lbl.config(
                    text="✓ small model found" if present else
                         "⚠ download the small model above to use this",
                    fg=GRN if present else AMBER,
                )
            hint_lbl = tk.Label(noise_row, text="", bg=CARD,
                                font=("Segoe UI", 8))
            hint_lbl.pack(side="left", padx=(8, 0))
            _update_noise_hint()
            # Re-check whenever the tab is shown so the hint stays current
            card.bind("<Visibility>", lambda e: _update_noise_hint())
            self._noise_hint_refresh = _update_noise_hint  # callable from outside

        status_lbl = tk.Label(card, text="", bg=CARD, fg=MUTED,
                               font=("Segoe UI", 8))
        status_lbl.pack(anchor="w")

        btn_row = tk.Frame(card, bg=CARD)
        btn_row.pack(fill="x", pady=(4, 0))

        progress_lbl = tk.Label(btn_row, text="", bg=CARD, fg=FG,
                                font=("Segoe UI", 8))
        progress_lbl.pack(side="left")

        use_btn = tk.Button(btn_row, text="✓ Use This Model",
                            bg=GRN, fg="#11111b", activebackground=GRN,
                            activeforeground="#11111b", relief="flat",
                            font=("Segoe UI Semibold", 9), padx=8, pady=4,
                            cursor="hand2",
                            command=lambda n=name: self._select_model(n))

        btn_kw = dict(bg=ACC, fg="#fff", activebackground=ACC,
                      activeforeground="#fff", relief="flat",
                      font=("Segoe UI Semibold", 9), padx=8, pady=4,
                      cursor="hand2")

        def _after_dl():
            """Called when a download finishes — refresh the noise hint."""
            if hasattr(self, "_noise_hint_refresh"):
                self._noise_hint_refresh()
            self._refresh_model_statuses()

        if "url" in m:
            dl_btn = tk.Button(btn_row, text="⬇  Download", **btn_kw,
                               command=lambda info=m:
                                   self._download_model(info, progress_lbl,
                                                        status_lbl, _after_dl))
        else:
            dl_btn = None

        self._model_cards[name] = {
            "status":   status_lbl,
            "progress": progress_lbl,
            "use_btn":  use_btn,
            "dl_btn":   dl_btn,
        }

        use_btn.pack(side="right", padx=(4, 0))
        if dl_btn:
            dl_btn.pack(side="right")

    def _refresh_model_statuses(self):
        """Update each model card to show downloaded/active status."""
        try:
            import user_config as _uc
            active = pathlib.Path(_uc.get_model_path()).name
            self._active_model_lbl.config(text=active or "—")
            exe_dir = pathlib.Path(_uc.get_model_path()).parent
        except Exception:
            exe_dir = pathlib.Path(".")
            active  = ""

        for m in VOSK_MODELS:
            name     = m["name"]
            present  = (exe_dir / name).is_dir()
            is_active = name == active
            wdg      = self._model_cards.get(name)
            if not wdg:
                continue
            if is_active:
                wdg["status"].config(text="✓ Active", fg=GRN)
                wdg["use_btn"].config(state="disabled", bg=MUTED)
                wdg["dl_btn"].config(state="disabled" if present else "normal")
            elif present:
                wdg["status"].config(text="Downloaded — not active", fg=MUTED)
                wdg["use_btn"].config(state="normal", bg=GRN)
                wdg["dl_btn"].config(state="disabled")
            else:
                wdg["status"].config(text="Not downloaded", fg=MUTED)
                wdg["use_btn"].config(state="disabled", bg=MUTED)
                wdg["dl_btn"].config(state="normal", bg=ACC)

    def _select_model(self, name: str):
        import user_config as _uc
        exe_dir   = pathlib.Path(_uc.get_model_path()).parent
        model_dir = exe_dir / name
        if not model_dir.is_dir():
            messagebox.showerror("Not downloaded",
                                 f"{name} is not downloaded yet.",
                                 parent=self.winfo_toplevel())
            return
        _uc.set_model_path(str(model_dir))
        self._refresh_model_statuses()
        self._flash(f"✓  Switched to {name} — restart engine to apply.")

    def _download_model(self, m: dict, progress_lbl, status_lbl, on_done=None):
        """Download and extract a Vosk model in a background thread."""
        import zipfile, urllib.request, ssl, io

        name    = m["name"]
        url     = m["url"]
        wdg     = self._model_cards.get(name, {})
        dl_btn  = wdg.get("dl_btn")
        use_btn = wdg.get("use_btn")

        try:
            import user_config as _uc
            dest_dir = pathlib.Path(_uc.get_model_path()).parent
        except Exception:
            dest_dir = pathlib.Path(".")

        if dl_btn:
            dl_btn.config(state="disabled", text="Downloading…")
        status_lbl.config(text="Starting download…", fg=AMBER)
        progress_lbl.config(text="")

        def _run():
            try:
                # Build SSL context
                try:
                    import certifi
                    ctx = ssl.create_default_context(cafile=certifi.where())
                except Exception:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE

                req  = urllib.request.Request(
                    url, headers={"User-Agent": "Echo/1.0"})
                with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                    total      = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    buf        = io.BytesIO()
                    chunk_size = 65536
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        buf.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total * 100
                            mb  = downloaded / 1_048_576
                            self.after(0, lambda p=pct, d=mb: progress_lbl.config(
                                text=f"{p:.0f}%  ({d:.0f} MB)"))

                self.after(0, lambda: status_lbl.config(
                    text="Extracting…", fg=AMBER))
                self.after(0, lambda: progress_lbl.config(text=""))

                buf.seek(0)
                with zipfile.ZipFile(buf) as zf:
                    zf.extractall(dest_dir)

                self.after(0, on_done if on_done else self._refresh_model_statuses)
                self.after(0, lambda: status_lbl.config(
                    text="✓ Downloaded", fg=GRN))
                self.after(0, lambda: self._flash(
                    f"✓  {name} downloaded — click ✓ Use This Model to activate."))
            except Exception as exc:
                self.after(0, lambda e=str(exc): status_lbl.config(
                    text=f"Error: {e}", fg=RED))
                self.after(0, lambda: self._flash(
                    f"Download failed: {exc}", RED))
            finally:
                if dl_btn:
                    self.after(0, lambda: dl_btn.config(
                        state="normal", text="⬇  Download"))

        threading.Thread(target=_run, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_save_btn(self, parent, cmd):
        tk.Button(parent, text="💾  Save", command=cmd,
                  bg=ACC, fg="#fff", activebackground=ACC,
                  activeforeground="#fff", relief="flat",
                  font=("Segoe UI Semibold", 10),
                  padx=14, pady=6, cursor="hand2").pack(
            anchor="e", padx=14, pady=(12, 4))

    def _flash(self, msg, color=GRN):
        self._status.config(text=msg, fg=color)
        self.after(5000, lambda: self._status.config(text=""))

    def _set_spin(self, widget, value):
        widget.config(state="normal")
        widget.delete(0, "end")
        widget.insert(0, str(value))

    def _load(self):
        try:
            self._overlay_enabled.set(user_config.get_overlay_enabled())
            self._overlay_pos.set(user_config.get_overlay_position())
            self._set_spin(self._conf_spin,
                           int(user_config.get_confidence_threshold() * 100))
            self._on_conf_change()
            self._set_spin(self._cooldown_spin, user_config.get_cooldown())
            self._set_spin(self._response_spin, int(round(user_config.get_response_delay() * 1000)))
            self._set_spin(self._delay_spin, user_config.get_close_delay())
            v_steps = user_config.get_volume_steps()
            for word, sp in self._vol_spins.items():
                self._set_spin(sp, v_steps.get(
                    word, user_config.DEFAULT_VOLUME_STEPS.get(word, 5)))
            words = user_config.get_command_words()
            for key, entry in self._cmd_entries.items():
                val = words.get(key, user_config.DEFAULT_COMMAND_WORDS.get(key, ""))
                entry.config(state="normal")
                entry.delete(0, "end")
                entry.insert(0, val)
                entry.xview_moveto(0)
            delays = user_config.get_word_delays()
            for key, sp in self._cmd_delay_entries.items():
                self._set_spin(sp, int(delays.get(key, 0)))
            self._reload_context_list()
            self._reload_groups_list()
            self._refresh_model_statuses()
        except Exception as exc:
            import traceback
            self._flash(f"⚠ Settings load error: {exc}", RED)
            traceback.print_exc()


# Backward-compat alias
SettingsWindow = SettingsWidget


# ── Standalone ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Settings")
    root.configure(bg=BG)
    root.geometry("900x740")
    SettingsWidget(root).pack(fill="both", expand=True)
    root.mainloop()
