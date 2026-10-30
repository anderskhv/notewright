"""Untangling piano from the horns, using the notes themselves.

On jazz records Demucs leaves much of the piano in its "other" stem, which becomes
the horns part. Two things follow: the piano part misses voicings, and the horn
pitch tracker follows the comping whenever the horns rest. This module fixes both:

1. The piano is transcribed from piano and horn parts together, so voicings that
   Demucs filed under "other" are still heard by the piano model.
2. Notes in the horn line that start with a piano chord and die away like a struck
   string are piano, not horn, and are dropped from the horn line. Piano notes that
   double a horn note (same pitch, same time) are the horn, and are dropped from the
   piano.
3. Score-informed separation: for every remaining piano note, its harmonics are
   filtered out of the horn audio for as long as it sounds, and moved into the piano
   audio. Harmonics a horn is playing at that moment are left alone. The horn part
   that remains is pitch-tracked again, now without the comping underneath.
"""

import numpy as np

N_FFT = 4096
HOP = 1024


def _stft(y: np.ndarray) -> np.ndarray:
    import librosa

    return np.stack([librosa.stft(ch, n_fft=N_FFT, hop_length=HOP) for ch in y])


def _istft(spec: np.ndarray, length: int) -> np.ndarray:
    import librosa

    return np.stack([librosa.istft(ch, hop_length=HOP, n_fft=N_FFT, length=length) for ch in spec])


def _harmonic_bins(midi: float, sr: int, n_bins: int, max_hz: float = 8000.0) -> list[slice]:
    f0 = 440.0 * 2 ** ((midi - 69) / 12)
    out = []
    k = 1
    while k * f0 < max_hz:
        f = k * f0
        width = max(1.5, f * 0.03 * N_FFT / sr)  # about half a semitone, at least 1.5 bins
        centre = f * N_FFT / sr
        lo, hi = int(max(0, centre - width)), int(min(n_bins, centre + width + 1))
        if lo < hi:
            out.append(slice(lo, hi))
        k += 1
    return out


def piano_like(notes: list, y: np.ndarray, sr: int, piano_onsets: np.ndarray) -> list[bool]:
    """For each note of a horn line: does it behave like a piano note?

    Piano: struck together with a piano chord (an onset of the piano transcription at
    the same pitch within 50 ms) and decaying over the note. A horn holds or swells.
    """
    mono = y.mean(axis=0) if y.ndim == 2 else y
    spec = np.abs(_stft(mono[None, :]))[0]
    frames = spec.shape[1]
    out = []
    for start, end, pitch, _ in notes:
        a, b = int(start * sr / HOP), min(frames, int(end * sr / HOP) + 1)
        # The tracker locks on a little after the attack, so allow 120 ms.
        struck = bool(np.any(np.abs(piano_onsets[piano_onsets[:, 1] == pitch, 0] - start) < 0.12)) if len(piano_onsets) else False
        if b - a < 6:  # under ~0.15 s: too short to judge the decay, go by the onset alone
            out.append(struck)
            continue
        bins = _harmonic_bins(pitch, sr, spec.shape[0], max_hz=3000)
        env = np.sum([spec[s, a:b].sum(axis=0) for s in bins[:4]], axis=0)
        head = env[: max(1, len(env) // 4)].max()
        tail = env[int(len(env) * 0.6) :].mean() if len(env) > 3 else head
        decays = tail < 0.4 * head
        out.append(decays and (struck or tail < 0.25 * head))
    return out


def drop_doubled(piano_notes: list, horn_notes: list) -> list:
    """Piano notes that are really a horn note (same pitch, mostly the same time)."""
    by_pitch: dict[int, list] = {}
    for n in horn_notes:
        by_pitch.setdefault(n[2], []).append(n)
    kept = []
    for n in piano_notes:
        overlap = 0.0
        for h in by_pitch.get(n[2], []):
            overlap = max(overlap, min(n[1], h[1]) - max(n[0], h[0]))
        if overlap < 0.5 * (n[1] - n[0]):
            kept.append(n)
    return kept


def separate(piano: np.ndarray, horns: np.ndarray, sr: int, piano_notes: list, horn_notes: list,
             pedals: list | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Move each piano note's harmonics out of the horn audio and into the piano audio."""
    spec = _stft(horns)
    n_bins, frames = spec.shape[1], spec.shape[2]
    keep = np.ones((n_bins, frames), dtype=np.float32)
    # Harmonics the horns play stay with the horns.
    protect = np.zeros((n_bins, frames), dtype=bool)
    for start, end, pitch, _ in horn_notes:
        a, b = max(0, int(start * sr / HOP) - 1), min(frames, int(end * sr / HOP) + 2)
        for s in _harmonic_bins(pitch, sr, n_bins):
            protect[s, a:b] = True
    pedal_ends = np.array([p[1] for p in pedals or []])
    for start, end, pitch, _ in piano_notes:
        # A piano string rings past its key release while the pedal is down.
        ring = end + 0.3
        if len(pedal_ends):
            later = pedal_ends[pedal_ends >= end]
            if len(later):
                ring = max(ring, min(later.min(), end + 3.0))
        a, b = max(0, int((start - 0.03) * sr / HOP)), min(frames, int(ring * sr / HOP) + 1)
        for s in _harmonic_bins(pitch, sr, n_bins):
            keep[s, a:b] = np.minimum(keep[s, a:b], 0.03)
    keep[protect] = 1.0
    # Soften the edges of the mask a little so the cuts do not sound metallic.
    from scipy.ndimage import uniform_filter

    keep = uniform_filter(keep, size=(1, 3))
    cleaned = _istft(spec * keep[None, :, :], horns.shape[-1])
    moved = horns - cleaned
    return piano + moved, cleaned


def fix_octaves(notes: list, y: np.ndarray, sr: int) -> list:
    """Move notes the pitch tracker put an octave too low back up.

    For a note on pitch p, harmonics 2, 4, 6 are also harmonics of p + 12; only the odd
    ones (1, 3, 5) are p's alone. If the odd ones are nearly silent, the note is p + 12.
    """
    mono = y.mean(axis=0) if y.ndim == 2 else y
    spec = np.abs(_stft(mono[None, :]))[0]
    n_bins, frames = spec.shape
    out = []
    for start, end, pitch, vel in notes:
        a, b = int(start * sr / HOP), max(int(start * sr / HOP) + 1, min(frames, int(end * sr / HOP)))
        bins = _harmonic_bins(pitch, sr, n_bins, max_hz=6000)[:6]
        if len(bins) < 4:
            out.append([start, end, pitch, vel])
            continue
        e = [float(spec[s, a:b].sum()) for s in bins]
        odd, even = e[0] + e[2] + (e[4] if len(e) > 4 else 0), e[1] + e[3] + (e[5] if len(e) > 5 else 0)
        out.append([start, end, pitch + 12 if odd < 0.2 * even else pitch, vel])
    return out


def clean_line(notes: list, y: np.ndarray, sr: int, piano_onsets: np.ndarray) -> list:
    """Tidy a horn line: octave slips, piano notes, and isolated blips."""
    if not notes:
        return notes
    notes = fix_octaves(notes, y, sr)
    merged: list = []
    for n in notes:  # an octave fix can leave two halves of one note side by side
        if merged and n[2] == merged[-1][2] and n[0] - merged[-1][1] < 0.06:
            merged[-1][1] = n[1]
        else:
            merged.append(list(n))
    flags = piano_like(merged, y, sr, piano_onsets)
    kept = [n for n, piano in zip(merged, flags) if not piano]
    out = []
    for i, n in enumerate(kept):
        if n[1] - n[0] < 0.25:
            gap_before = n[0] - kept[i - 1][1] if i else 99
            gap_after = kept[i + 1][0] - n[1] if i + 1 < len(kept) else 99
            if min(gap_before, gap_after) > 0.4:
                continue  # a blip on its own is leakage, not a phrase
            struck = len(piano_onsets) and np.any(np.abs(piano_onsets[:, 0] - n[0]) < 0.12)
            if struck and gap_before > 0.04 and gap_after > 0.3:
                continue  # a short note on a piano chord just as a phrase ends: the chord ringing
        out.append(n)
    return out
