"""
Settings window — lets each user customise engine behaviour, volume steps,
and command trigger words without editing any files.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import user_config

BG       = "#1e1e2e"
CARD     = "#2a2a3e"
ACC      = "#7c6af7"
FG       = "#cdd6f4"
ENTRY_BG = "#313244"
MUTED    = "#585b70"
GRN      = "#a6e3a1"
RED      = "#f38ba8"


def _lbl(parent, text, fg=FG, font=("Segoe UI", 9), **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=fg, font=font, **kw)

def _inp(parent, width=16, **kw):
    return tk.Entry(parent, width=width, bg=ENTRY_BG, fg=FG,
                    insertbackground=FG, relief="flat",
                    font=("Segoe UI", 10), bd=4, **kw)

def _spin(parent, from_, to, var, width=6):
    return tk.Spinbox(parent, from_=from_, to=to, textvariable=var,
                      width=width, bg=ENTRY_BG, fg=FG,
                      buttonbackground=CARD, insertbackground=FG,
                      relief="flat", font=("Segoe UI", 10))

def _section(parent, title):
    f = tk.Frame(parent, bg=BG)
    tk.Label(f, text=title, bg=BG, fg=ACC,
             font=("Segoe UI Semibold", 10)).pack(anchor="w")
    tk.Frame(f, bg=ACC, height=1).pack(fill="x", pady=(2, 8))
    return f

def _card(parent):
    return tk.Frame(parent, bg=CARD, padx=14, pady=10)


class _ContextCmdDialog(tk.Toplevel):
    """Dialog for adding a new context command rule."""
    def __init__(self, master):
        super().__init__(master)
        self.title("Add Context Command")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.result = None

        tk.Label(self, text="🖱  Add Context Command", bg=BG, fg=ACC,
                 font=("Segoe UI Semibold", 11)).pack(padx=20, pady=(14, 8))

        fields = tk.Frame(self, bg=BG)
        fields.pack(fill="x", padx=20, pady=4)

        def row(label, widget):
            f = tk.Frame(fields, bg=BG)
            f.pack(fill="x", pady=4)
            _lbl(f, label).pack(anchor="w")
            widget(f).pack(fill="x")

        self._phrase = tk.StringVar()
        self._context = tk.StringVar(value="browser")
        self._shortcut = tk.StringVar()

        row("Voice phrase  (what you say)", lambda f: tk.Entry(
            f, textvariable=self._phrase, bg=ENTRY_BG, fg=FG,
            insertbackground=FG, relief="flat", font=("Segoe UI", 10), bd=4))

        def ctx_row(f):
            style = ttk.Style(f); style.theme_use("clam")
            style.configure("TCombobox", fieldbackground=ENTRY_BG, foreground=FG,
                            background=CARD, arrowcolor=FG)
            cb = ttk.Combobox(f, textvariable=self._context, state="readonly",
                              values=["browser", "explorer", "editor", "any"],
                              font=("Segoe UI", 10))
            return cb
        row("Context  (when does it work?)", ctx_row)

        _lbl(fields, "  browser = Chrome/Firefox/Edge    explorer = File Explorer\n"
             "  editor = Notepad/VS Code etc.      any = always",
             fg=MUTED, font=("Segoe UI", 8), justify="left").pack(anchor="w")

        row("Keyboard shortcut  (e.g. ctrl+w  or  f5  or  windows+l)",
            lambda f: tk.Entry(f, textvariable=self._shortcut, bg=ENTRY_BG, fg=FG,
                               insertbackground=FG, relief="flat",
                               font=("Consolas", 10), bd=4))

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=20, pady=(4, 14))

        def _ok():
            phrase   = self._phrase.get().strip().lower()
            context  = self._context.get().strip()
            shortcut = self._shortcut.get().strip().lower()
            if not phrase or not shortcut:
                return
            self.result = (phrase, context, shortcut)
            self.destroy()

        tk.Button(btn_row, text="Add", command=_ok,
                  bg=ACC, fg="#fff", activebackground=ACC, activeforeground="#fff",
                  relief="flat", font=("Segoe UI Semibold", 9),
                  padx=14, pady=5, cursor="hand2").pack(side="right")
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg=MUTED, fg="#fff", activebackground=MUTED, activeforeground="#fff",
                  relief="flat", font=("Segoe UI Semibold", 9),
                  padx=14, pady=5, cursor="hand2").pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda _: _ok())
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()


class SettingsWindow(tk.Toplevel):

    def __init__(self, master):
        super().__init__(master)
        self.title("Voice Commands — Settings")
        self.configure(bg=BG)
        self.resizable(True, True)
        self._build_ui()
        self.after(100, self._load)   # defer so widgets are fully rendered first

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Notebook tabs
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook",       background=BG, borderwidth=0)
        style.configure("TNotebook.Tab",   background=CARD, foreground=FG,
                        padding=[14, 6], font=("Segoe UI Semibold", 9))
        style.map("TNotebook.Tab",
                  background=[("selected", ACC)],
                  foreground=[("selected", "#ffffff")])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=12)

        self._tab_engine(nb)
        self._tab_volume(nb)
        self._tab_commands(nb)
        self._tab_context(nb)

        # Status bar
        self._status = tk.Label(self, text="", bg=BG, fg=GRN,
                                font=("Segoe UI", 9), anchor="w")
        self._status.pack(fill="x", padx=14, pady=(0, 10))

    # ── Engine tab ────────────────────────────────────────────────────────────

    def _tab_engine(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="⚙  Engine")

        sec = _section(frame, "Recognition")
        sec.pack(fill="x", padx=2, pady=(8, 0))
        card = _card(sec)
        card.pack(fill="x")

        # Confidence threshold
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

        # Cooldown
        _lbl(card, "Cooldown  (ignore repeated command within this window)").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self._cooldown_spin = _spin(card, 0.0, 10.0, tk.DoubleVar(), width=7)
        self._cooldown_spin.grid(row=3, column=0, sticky="w")
        _lbl(card, "seconds", fg=MUTED).grid(row=3, column=1, sticky="w", padx=(4, 0))

        # Close delay
        sec2 = _section(frame, "Close-App Undo Window")
        sec2.pack(fill="x", padx=2, pady=(14, 0))
        card2 = _card(sec2)
        card2.pack(fill="x")
        _lbl(card2, "How long to wait before actually closing an app  (say 'undo' to cancel)").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self._delay_spin = _spin(card2, 1, 60, tk.IntVar())
        self._delay_spin.grid(row=1, column=0, sticky="w")
        _lbl(card2, "seconds", fg=MUTED).grid(row=1, column=1, sticky="w", padx=(4, 0))

        # Save button
        self._make_save_btn(frame, self._save_engine)

    def _on_conf_change(self, *_):
        try:
            v = int(self._conf_spin.get())
            if v >= 80:   note = "very strict — may miss commands"
            elif v >= 60: note = "recommended"
            elif v >= 40: note = "lenient — may trigger accidentally"
            else:         note = "very lenient"
            self._conf_note.config(text=f"({note})")
        except Exception:
            pass

    def _save_engine(self):
        try:
            user_config.set_confidence_threshold(int(self._conf_spin.get()) / 100.0)
            user_config.set_cooldown(float(self._cooldown_spin.get()))
            user_config.set_close_delay(int(self._delay_spin.get()))
            self._flash("✓  Engine settings saved — restart engine to apply.")
        except Exception as e:
            self._flash(f"Error: {e}", RED)

    # ── Volume tab ────────────────────────────────────────────────────────────

    def _tab_volume(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="🔊  Volume")

        sec = _section(frame, "Volume Step Sizes")
        sec.pack(fill="x", padx=2, pady=(8, 0))
        card = _card(sec)
        card.pack(fill="x")

        _lbl(card, 'Say "volume up one" / "volume down three" etc.',
             fg=MUTED).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        self._vol_spins: dict[str, tk.Spinbox] = {}
        step_words = list(user_config.DEFAULT_VOLUME_STEPS.keys())
        for i, word in enumerate(step_words):
            _lbl(card, f'"{word}"').grid(row=i+1, column=0, sticky="w", pady=3)
            sp = _spin(card, 1, 100, tk.IntVar(), width=5)
            sp.grid(row=i+1, column=1, sticky="w", padx=(8, 4))
            self._vol_spins[word] = sp
            _lbl(card, "%", fg=MUTED).grid(row=i+1, column=2, sticky="w")

        self._make_save_btn(frame, self._save_volume)

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
        nb.add(frame, text="🎙  Commands")

        _lbl(frame,
             "Change the words you say to trigger each action.\n"
             "Restart the engine after saving for changes to take effect.",
             fg=MUTED, font=("Segoe UI", 8), justify="left").pack(
            anchor="w", padx=2, pady=(8, 4))

        sec = _section(frame, "Trigger Words")
        sec.pack(fill="x", padx=2)
        card = _card(sec)
        card.pack(fill="x")

        ACTIONS = [
            # ── App control prefixes ──────────────────────────────────────────
            ("open",           "Prefix: open app  (e.g. 'open firefox')"),
            ("close",          "Prefix: close app  (e.g. 'close firefox')"),
            ("minimise",       "Prefix: minimise app"),
            ("maximise",       "Prefix: maximise app"),
            ("move",           "Prefix: snap/move app  (e.g. 'move firefox left')"),
            ("merge",          "Merge Explorer windows into tabs"),
            ("minimise_all",   "Minimise all windows  (uses open/minimise prefix)"),
            ("open_all",       "Show all windows  (uses open prefix)"),
            # ── Media ─────────────────────────────────────────────────────────
            ("skip",           "Skip track"),
            ("previous",       "Previous track"),
            ("rewind",         "Restart current track"),
            ("play_pause",     "Play / Pause"),
            ("mute",           "Toggle mute"),
            # ── Keyboard ─────────────────────────────────────────────────────
            ("copy",           "Copy  (Ctrl+C)"),
            ("paste",          "Paste  (Ctrl+V)"),
            ("save",           "Save  (Ctrl+S)"),
            ("enter",          "Press Enter"),
            # ── Engine ───────────────────────────────────────────────────────
            ("undo",           "Undo close"),
            ("diagnose",       "Run diagnostic"),
            ("stop_engine",    "Stop voice commands"),
            ("restart_engine", "Restart voice commands"),
        ]

        self._cmd_entries: dict[str, tk.Entry] = {}
        for i, (key, label) in enumerate(ACTIONS):
            row, col = divmod(i, 2)
            cell = tk.Frame(card, bg=CARD)
            cell.grid(row=row, column=col, sticky="ew", padx=(0, 16), pady=3)
            card.columnconfigure(col, weight=1)
            _lbl(cell, label, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            e = _inp(cell, width=22)
            e.pack(anchor="w")
            self._cmd_entries[key] = e

        self._make_save_btn(frame, self._save_commands)

    def _save_commands(self):
        try:
            words = {}
            for k, e in self._cmd_entries.items():
                val = e.get().strip().lower()
                # Fall back to the built-in default if the user left the field blank
                words[k] = val if val else user_config.DEFAULT_COMMAND_WORDS.get(k, "")
            user_config.set_command_words(words)
            self._flash("✓  Command words saved — restart engine to apply.")
        except Exception as e:
            self._flash(f"Error: {e}", RED)

    # ── Context commands tab ──────────────────────────────────────────────────

    def _tab_context(self, nb):
        frame = tk.Frame(nb, bg=BG)
        nb.add(frame, text="🖱  Context")

        _lbl(frame,
             "These commands only fire when the right app is focused.\n"
             "Contexts: browser · explorer · editor · any (always works)",
             fg=MUTED, font=("Segoe UI", 8), justify="left").pack(
            anchor="w", padx=4, pady=(8, 4))

        # List frame
        list_frame = tk.Frame(frame, bg=CARD)
        list_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Header row
        hdr = tk.Frame(list_frame, bg=CARD)
        hdr.pack(fill="x", padx=6, pady=(6, 2))
        for text, w in [("Voice phrase", 22), ("Context", 10), ("Shortcut", 16)]:
            _lbl(hdr, text, fg=ACC, font=("Segoe UI Semibold", 8)).pack(
                side="left", width=w*7)

        # Scrollable list
        canvas = tk.Canvas(list_frame, bg=CARD, highlightthickness=0, height=260)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._ctx_inner = tk.Frame(canvas, bg=CARD)
        _cwin = canvas.create_window((0, 0), window=self._ctx_inner, anchor="nw")
        self._ctx_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfig(_cwin, width=e.width))
        # Bind scroll only to the canvas itself (not bind_all, which breaks spinboxes)
        canvas.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        self._ctx_inner.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self._ctx_canvas = canvas
        # _reload_context_list() is called from _load() after window is rendered

        # Add / Delete row
        bot = tk.Frame(frame, bg=BG)
        bot.pack(fill="x", padx=4, pady=(0, 4))

        tk.Button(bot, text="➕  Add Command", command=self._add_context_cmd,
                  bg=ACC, fg="#fff", activebackground=ACC, activeforeground="#fff",
                  relief="flat", font=("Segoe UI Semibold", 9),
                  padx=10, pady=5, cursor="hand2").pack(side="left")
        tk.Button(bot, text="🗑  Delete Selected", command=self._del_context_cmd,
                  bg=RED, fg="#fff", activebackground=RED, activeforeground="#fff",
                  relief="flat", font=("Segoe UI Semibold", 9),
                  padx=10, pady=5, cursor="hand2").pack(side="left", padx=(8, 0))
        tk.Button(bot, text="↺  Reset Defaults", command=self._reset_context_cmds,
                  bg=MUTED, fg="#fff", activebackground=MUTED, activeforeground="#fff",
                  relief="flat", font=("Segoe UI Semibold", 9),
                  padx=10, pady=5, cursor="hand2").pack(side="right")

    def _reload_context_list(self):
        for w in self._ctx_inner.winfo_children():
            w.destroy()
        self._ctx_row_vars = []
        cmds = user_config.get_context_commands()
        canvas = self._ctx_canvas
        def _scroll(e):
            canvas.yview_scroll(-1 * (e.delta // 120), "units")
        for phrase, contexts in sorted(cmds.items()):
            for context, shortcut in contexts.items():
                var = tk.BooleanVar(value=False)
                row = tk.Frame(self._ctx_inner, bg=CARD)
                row.pack(fill="x", padx=4, pady=1)
                cb = tk.Checkbutton(row, variable=var, bg=CARD,
                               activebackground=CARD, selectcolor=ENTRY_BG)
                cb.pack(side="left")
                l1 = tk.Label(row, text=phrase, bg=CARD, fg=FG,
                         font=("Segoe UI", 9), width=22, anchor="w")
                l1.pack(side="left")
                ctx_color = {"browser": "#89b4fa", "explorer": "#a6e3a1",
                             "editor": "#f9e2af", "any": "#cba6f7"}.get(context, FG)
                l2 = tk.Label(row, text=context, bg=CARD, fg=ctx_color,
                         font=("Segoe UI", 8), width=10, anchor="w")
                l2.pack(side="left")
                l3 = tk.Label(row, text=shortcut, bg=CARD, fg=MUTED,
                         font=("Consolas", 8), width=16, anchor="w")
                l3.pack(side="left")
                # Propagate scroll events from row widgets to the canvas
                for w in (row, cb, l1, l2, l3):
                    w.bind("<MouseWheel>", _scroll)
                self._ctx_row_vars.append((var, phrase, context))

    def _add_context_cmd(self):
        dlg = _ContextCmdDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        phrase, context, shortcut = dlg.result
        cmds = user_config.get_context_commands()
        if phrase not in cmds:
            cmds[phrase] = {}
        cmds[phrase][context] = shortcut
        user_config.set_context_commands(cmds)
        self._reload_context_list()
        self._flash(f'✓  Added "{phrase}" [{context}] → {shortcut}')

    def _del_context_cmd(self):
        to_delete = [(p, c) for v, p, c in self._ctx_row_vars if v.get()]
        if not to_delete:
            self._flash("Select rows to delete first.", GRN)
            return
        cmds = user_config.get_context_commands()
        for phrase, context in to_delete:
            if phrase in cmds and context in cmds[phrase]:
                del cmds[phrase][context]
                if not cmds[phrase]:
                    del cmds[phrase]
        user_config.set_context_commands(cmds)
        self._reload_context_list()
        self._flash(f"✓  Deleted {len(to_delete)} rule(s).")

    def _reset_context_cmds(self):
        from tkinter import messagebox
        if messagebox.askyesno("Reset?",
                               "Reset context commands to defaults?\n"
                               "Your custom additions will be lost.",
                               parent=self):
            user_config.set_context_commands({})
            self._reload_context_list()
            self._flash("✓  Reset to defaults.")

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
        """Directly set a Spinbox value, bypassing textvariable caching issues."""
        widget.config(state="normal")
        widget.delete(0, "end")
        widget.insert(0, str(value))

    def _load(self):
        """Populate all fields from current config."""
        try:
            self._set_spin(self._conf_spin,
                           int(user_config.get_confidence_threshold() * 100))
            self._on_conf_change()

            self._set_spin(self._cooldown_spin, user_config.get_cooldown())
            self._set_spin(self._delay_spin,    user_config.get_close_delay())

            steps = user_config.get_volume_steps()
            for word, sp in self._vol_spins.items():
                self._set_spin(sp, steps.get(word,
                               user_config.DEFAULT_VOLUME_STEPS.get(word, 5)))

            words = user_config.get_command_words()
            for key, entry in self._cmd_entries.items():
                entry.delete(0, "end")
                entry.insert(0, words.get(key,
                             user_config.DEFAULT_COMMAND_WORDS.get(key, "")))

            self._reload_context_list()
        except Exception as exc:
            import traceback
            self._flash(f"⚠ Settings load error: {exc}", RED)
            traceback.print_exc()


# ── Standalone ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    win = SettingsWindow(root)
    win.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
