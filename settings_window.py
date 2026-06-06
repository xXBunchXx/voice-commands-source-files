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


class SettingsWindow(tk.Toplevel):

    def __init__(self, master):
        super().__init__(master)
        self.title("Voice Commands — Settings")
        self.configure(bg=BG)
        self.resizable(True, True)
        self._build_ui()
        self._load()

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
        self._conf_var = tk.IntVar()
        conf_spin = _spin(card, 1, 100, self._conf_var)
        conf_spin.grid(row=1, column=0, sticky="w")
        _lbl(card, "%", fg=MUTED).grid(row=1, column=1, sticky="w", padx=(4, 20))
        self._conf_note = _lbl(card, "", fg=MUTED)
        self._conf_note.grid(row=1, column=2, sticky="w")
        self._conf_var.trace_add("write", self._on_conf_change)

        # Cooldown
        _lbl(card, "Cooldown  (ignore repeated command within this window)").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self._cooldown_var = tk.DoubleVar()
        _spin(card, 0.0, 10.0, self._cooldown_var).grid(row=3, column=0, sticky="w")
        _lbl(card, "seconds", fg=MUTED).grid(row=3, column=1, sticky="w", padx=(4, 0))

        # Close delay
        sec2 = _section(frame, "Close-App Undo Window")
        sec2.pack(fill="x", padx=2, pady=(14, 0))
        card2 = _card(sec2)
        card2.pack(fill="x")
        _lbl(card2, "How long to wait before actually closing an app  (say 'undo' to cancel)").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self._delay_var = tk.IntVar()
        _spin(card2, 1, 60, self._delay_var).grid(row=1, column=0, sticky="w")
        _lbl(card2, "seconds", fg=MUTED).grid(row=1, column=1, sticky="w", padx=(4, 0))

        # Save button
        self._make_save_btn(frame, self._save_engine)

    def _on_conf_change(self, *_):
        try:
            v = self._conf_var.get()
            if v >= 80:   note = "very strict — may miss commands"
            elif v >= 60: note = "recommended"
            elif v >= 40: note = "lenient — may trigger accidentally"
            else:         note = "very lenient"
            self._conf_note.config(text=f"({note})")
        except Exception:
            pass

    def _save_engine(self):
        try:
            user_config.set_confidence_threshold(self._conf_var.get() / 100.0)
            user_config.set_cooldown(self._cooldown_var.get())
            user_config.set_close_delay(self._delay_var.get())
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

        self._vol_vars: dict[str, tk.IntVar] = {}
        step_words = list(user_config.DEFAULT_VOLUME_STEPS.keys())
        for i, word in enumerate(step_words):
            _lbl(card, f'"{word}"').grid(row=i+1, column=0, sticky="w", pady=3)
            v = tk.IntVar()
            self._vol_vars[word] = v
            _spin(card, 1, 100, v, width=5).grid(row=i+1, column=1, sticky="w", padx=(8, 4))
            _lbl(card, "%", fg=MUTED).grid(row=i+1, column=2, sticky="w")

        self._make_save_btn(frame, self._save_volume)

    def _save_volume(self):
        try:
            steps = {w: v.get() for w, v in self._vol_vars.items()}
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
            ("skip",           "Skip track"),
            ("previous",       "Restart current track"),
            ("rewind",         "Previous track"),
            ("play_pause",     "Play / Pause"),
            ("mute",           "Toggle mute"),
            ("copy",           "Copy  (Ctrl+C)"),
            ("paste",          "Paste  (Ctrl+V)"),
            ("save",           "Save  (Ctrl+S)"),
            ("enter",          "Press Enter"),
            ("undo",           "Undo close"),
            ("diagnose",       "Run diagnostic"),
            ("minimise_all",   "Minimise all windows"),
            ("open_all",       "Show all windows"),
            ("stop_engine",    "Stop voice commands"),
            ("restart_engine", "Restart voice commands"),
        ]

        self._cmd_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(ACTIONS):
            row, col = divmod(i, 2)
            cell = tk.Frame(card, bg=CARD)
            cell.grid(row=row, column=col, sticky="ew", padx=(0, 16), pady=3)
            card.columnconfigure(col, weight=1)
            _lbl(cell, label, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            v = tk.StringVar()
            self._cmd_vars[key] = v
            _inp(cell, width=22, textvariable=v).pack(anchor="w")

        self._make_save_btn(frame, self._save_commands)

    def _save_commands(self):
        try:
            words = {k: v.get().strip().lower() for k, v in self._cmd_vars.items()}
            # Warn about empty fields
            empty = [k for k, w in words.items() if not w]
            if empty:
                messagebox.showwarning("Empty fields",
                                       f"These actions have no trigger word:\n" +
                                       "\n".join(f"  • {k}" for k in empty),
                                       parent=self)
                return
            user_config.set_command_words(words)
            self._flash("✓  Command words saved — restart engine to apply.")
        except Exception as e:
            self._flash(f"Error: {e}", RED)

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

    def _load(self):
        """Populate all fields from current config."""
        self._conf_var.set(int(user_config.get_confidence_threshold() * 100))
        self._cooldown_var.set(user_config.get_cooldown())
        self._delay_var.set(user_config.get_close_delay())

        steps = user_config.get_volume_steps()
        for word, var in self._vol_vars.items():
            var.set(steps.get(word, user_config.DEFAULT_VOLUME_STEPS.get(word, 5)))

        words = user_config.get_command_words()
        for key, var in self._cmd_vars.items():
            var.set(words.get(key, user_config.DEFAULT_COMMAND_WORDS.get(key, "")))


# ── Standalone ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    win = SettingsWindow(root)
    win.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
