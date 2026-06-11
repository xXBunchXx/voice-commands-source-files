"""
Voice template recording and matching for hard-to-pronounce spoken names.

Records MFCC feature matrices from the user's voice, stores them on disk,
and during recognition compares incoming audio against those templates using
DTW (Dynamic Time Warping).  No external dependencies beyond numpy.

Usage
-----
  # Training (one sample at a time)
  ok = save_template("ace sprite", raw_pcm_bytes, index=0)

  # Matching (called with the raw audio of each utterance)
  result = match(raw_pcm_bytes)
  if result:
      spoken_name, distance = result
      # use spoken_name instead of what Vosk returned
"""

import os
import pathlib
import numpy as np

# ── Storage ───────────────────────────────────────────────────────────────────
_TEMPLATES_DIR = pathlib.Path(os.getenv("APPDATA", "~")) / "Echo" / "templates"

# ── Audio / feature parameters ────────────────────────────────────────────────
_SR        = 16000
_N_MFCC    = 13
_N_MEL     = 26
_FRAME_LEN = 400    # 25 ms at 16 kHz
_FRAME_HOP = 160    # 10 ms at 16 kHz
_N_FFT     = 512
_MAX_FRAMES = 120   # cap for DTW speed (~1.2 s of speech)

# How close a DTW match has to be to count (lower = stricter)
DEFAULT_THRESHOLD  = 2.8
# Adaptive threshold: accept if query distance ≤ mean-pairwise-spread × factor
THRESHOLD_FACTOR   = 1.2
# Minimum RMS amplitude (0.0–1.0 normalised) for template matching to run.
# Real speech is typically 0.05–0.3; PC fan / background noise is < 0.02.
# Raise this if you get false matches from ambient noise; lower it if your
# mic is quiet and real commands get rejected.
_MIN_SPEECH_RMS    = 0.03

# ── In-memory cache ────────────────────────────────────────────────────────────
_templates: dict[str, list[np.ndarray]] = {}   # spoken_name → [mfcc, ...]
_fbank_cache: np.ndarray | None = None
_loaded = False


# ── Public API ────────────────────────────────────────────────────────────────

def reload() -> None:
    """Load all saved templates from disk into memory."""
    global _templates, _loaded
    _templates = {}
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(_TEMPLATES_DIR.glob("*.npy")):
        try:
            arr = np.load(str(f), allow_pickle=False)
            # filename: "<spoken_name_with_underscores>_<index>.npy"
            stem  = f.stem                        # e.g. "ace_sprite_0"
            parts = stem.rsplit("_", 1)
            if len(parts) != 2 or not parts[1].isdigit():
                continue
            key = parts[0].replace("_", " ")      # "ace sprite"
            _templates.setdefault(key, []).append(arr)
        except Exception:
            pass
    _loaded = True


def save_template(spoken_name: str, audio_bytes: bytes, index: int) -> bool:
    """Extract MFCC from *audio_bytes* and save as template *index* for *spoken_name*.
    Returns True on success."""
    mfcc = _compute_mfcc(audio_bytes)
    if mfcc is None or len(mfcc) < 5:
        return False
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(spoken_name)
    path = _TEMPLATES_DIR / f"{safe}_{index}.npy"
    np.save(str(path), mfcc)
    # Update cache
    lst = _templates.setdefault(spoken_name.lower(), [])
    while len(lst) <= index:
        lst.append(None)
    lst[index] = mfcc
    return True


def delete_templates(spoken_name: str) -> None:
    """Remove every saved template for *spoken_name*."""
    safe = _safe_name(spoken_name)
    for f in _TEMPLATES_DIR.glob(f"{safe}_*.npy"):
        try:
            f.unlink()
        except Exception:
            pass
    _templates.pop(spoken_name.lower(), None)


def template_count(spoken_name: str) -> int:
    """Return how many templates are saved for *spoken_name*."""
    if not _loaded:
        reload()
    return len([t for t in _templates.get(spoken_name.lower(), []) if t is not None])


def match(audio_bytes: bytes,
          threshold: float | None = None) -> tuple[str, float] | None:
    """Compare *audio_bytes* against all stored templates.

    The threshold is adaptive: for each spoken name we compute the average
    pairwise DTW distance between its own training samples (their natural
    spread), then multiply by THRESHOLD_FACTOR to set the acceptance cutoff.
    This means the threshold self-calibrates to how consistent the user's
    voice samples were — tight samples → tight threshold, variable samples
    → looser threshold.

    Returns (spoken_name, distance) for the best match, or None.
    """
    if not _loaded:
        reload()
    if not _templates:
        return None
    query = _compute_mfcc(audio_bytes)
    if query is None:
        return None

    best_name: str | None = None
    best_dist = float("inf")
    best_thresh = DEFAULT_THRESHOLD

    for name, tmpl_list in _templates.items():
        valid = [t for t in tmpl_list if t is not None]
        if not valid:
            continue

        # Adaptive threshold: mean pairwise distance between training samples
        # × THRESHOLD_FACTOR.  Fall back to DEFAULT_THRESHOLD if only 1 sample.
        if len(valid) >= 2:
            pairs = []
            for i in range(len(valid)):
                for j in range(i + 1, len(valid)):
                    pairs.append(_dtw_distance(valid[i], valid[j]))
            spread  = sum(pairs) / len(pairs)
            cutoff  = spread * THRESHOLD_FACTOR
        else:
            cutoff = DEFAULT_THRESHOLD

        for tmpl in valid:
            dist = _dtw_distance(query, tmpl)
            if dist < best_dist:
                best_dist  = dist
                best_name  = name
                best_thresh = cutoff

    if threshold is not None:
        best_thresh = threshold   # caller override

    if best_name is not None and best_dist <= best_thresh:
        return best_name, best_dist
    return None


# ── MFCC computation (pure numpy, no scipy) ───────────────────────────────────

def _compute_mfcc(audio_bytes: bytes) -> np.ndarray | None:
    """Return an (n_frames, _N_MFCC) float32 array, or None on failure."""
    if not audio_bytes:
        return None
    try:
        audio = (np.frombuffer(audio_bytes, dtype=np.int16)
                   .astype(np.float32) / 32768.0)
        if len(audio) < _FRAME_LEN:
            return None

        # Trim leading/trailing near-silence (|amp| < 0.01)
        active = np.where(np.abs(audio) > 0.01)[0]
        if len(active) == 0:
            return None
        audio = audio[active[0]: active[-1] + 1]
        if len(audio) < _FRAME_LEN:
            return None

        # Energy gate: skip template matching if the signal is too quiet
        # to be real speech.  Fan / PC background noise typically has RMS
        # well below 0.02 (2 % of full scale); voiced speech is usually
        # 0.05 – 0.3.  This prevents the matcher from firing on ambient noise.
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < _MIN_SPEECH_RMS:
            return None

        # Pre-emphasis
        audio = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])

        # Framing
        n_frames = max(1, 1 + (len(audio) - _FRAME_LEN) // _FRAME_HOP)
        idx = (np.tile(np.arange(_FRAME_LEN), (n_frames, 1)) +
               np.tile(np.arange(n_frames) * _FRAME_HOP, (_FRAME_LEN, 1)).T)
        idx = np.clip(idx, 0, len(audio) - 1)
        frames = audio[idx] * np.hamming(_FRAME_LEN)

        # Power spectrum
        mag   = np.abs(np.fft.rfft(frames, n=_N_FFT))
        power = (1.0 / _N_FFT) * mag ** 2

        # Mel filterbank → log
        fbank   = _mel_fbank()
        mel_pow = np.dot(power, fbank.T)
        mel_pow = np.maximum(mel_pow, 1e-10)
        log_mel = np.log(mel_pow)

        # DCT-II (no scipy needed)
        n     = log_mel.shape[1]
        dct_m = np.cos(np.pi / n * np.outer(np.arange(_N_MFCC),
                                             np.arange(n) + 0.5))
        mfcc = np.dot(log_mel, dct_m.T).astype(np.float32)

        # Cepstral mean normalisation
        mfcc -= mfcc.mean(axis=0, keepdims=True)

        # Cap length for DTW performance
        if len(mfcc) > _MAX_FRAMES:
            step = len(mfcc) // _MAX_FRAMES
            mfcc = mfcc[::step]

        return mfcc
    except Exception:
        return None


def _mel_fbank() -> np.ndarray:
    global _fbank_cache
    if _fbank_cache is not None:
        return _fbank_cache

    def hz2mel(f): return 2595.0 * np.log10(1.0 + f / 700.0)
    def mel2hz(m): return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    lo  = hz2mel(80.0)
    hi  = hz2mel(_SR / 2.0)
    pts = mel2hz(np.linspace(lo, hi, _N_MEL + 2))
    bin_pts = np.floor((_N_FFT + 1) * pts / _SR).astype(int)

    fbank = np.zeros((_N_MEL, _N_FFT // 2 + 1), dtype=np.float32)
    for m in range(1, _N_MEL + 1):
        lo_b, ctr, hi_b = bin_pts[m-1], bin_pts[m], bin_pts[m+1]
        for k in range(lo_b, ctr):
            if ctr != lo_b:
                fbank[m-1, k] = (k - lo_b) / (ctr - lo_b)
        for k in range(ctr, hi_b):
            if hi_b != ctr:
                fbank[m-1, k] = (hi_b - k) / (hi_b - ctr)

    _fbank_cache = fbank
    return fbank


# ── DTW ───────────────────────────────────────────────────────────────────────

def _dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised DTW distance between two MFCC matrices."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")

    # Frame-level Euclidean costs (vectorised)
    diff = a[:, np.newaxis, :] - b[np.newaxis, :, :]   # (n, m, d)
    cost = np.sqrt(np.sum(diff ** 2, axis=2))           # (n, m)

    # Accumulation (Python loop is fine — frames are short after capping)
    dtw = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    dtw[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dtw[i, j] = float(cost[i-1, j-1]) + min(
                dtw[i-1, j], dtw[i, j-1], dtw[i-1, j-1])

    return dtw[n, m] / (n + m)


# ── Internal ──────────────────────────────────────────────────────────────────

def _safe_name(spoken: str) -> str:
    """Convert a spoken name to a safe filename stem."""
    return spoken.lower().strip().replace(" ", "_")
