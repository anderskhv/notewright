"""Audio to notes.

Every transcriber returns notes as [start, end, pitch, velocity] with times in
seconds, pitch as a MIDI number and velocity 1-127.

- Polyphonic instruments (piano, guitar, "other") use the ByteDance high-resolution
  piano transcription model (Kong et al. 2020). It is trained on piano but does a
  reasonable job on other plucked and struck instruments.
- Monophonic lines (bass, vocals) use probabilistic YIN pitch tracking.
- Drums use onset detection plus a three-band energy split (kick, snare, hi-hat).
"""

import urllib.request
from pathlib import Path
from typing import Callable

import librosa
import numpy as np

from .config import MODEL_DIR, PIANO_CHECKPOINT_URL

Notes = list[list[float]]
Progress = Callable[[float], None]

_piano_model = None


def _piano_checkpoint() -> Path:
    path = MODEL_DIR / "piano_transcription.pth"
    if not path.exists() or path.stat().st_size < 1.6e8:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        urllib.request.urlretrieve(PIANO_CHECKPOINT_URL, partial)
        partial.rename(path)
    return path


def _load_piano_model(device: str):
    global _piano_model
    if _piano_model is None:
        import torch
        from piano_transcription_inference import PianoTranscription

        checkpoint = _piano_checkpoint()
        # The 2020 checkpoint holds plain tensors plus bookkeeping ints; it predates
        # torch's weights_only default, so load it the way it was saved.
        original_load = torch.load
        torch.load = lambda *a, **k: original_load(*a, **{**k, "weights_only": False})
        try:
            _piano_model = PianoTranscription(device=torch.device(device), checkpoint_path=str(checkpoint))
        finally:
            torch.load = original_load
    return _piano_model


def polyphonic(path: Path, device: str, progress: Progress, onset_threshold: float = 0.3) -> tuple[Notes, list]:
    """Transcribe with the piano model. Returns (notes, pedal [start, end] spans)."""
    import torch
    from piano_transcription_inference.utilities import RegressionPostProcessor

    model = _load_piano_model(device)
    audio, _ = librosa.load(str(path), sr=16000, mono=True)
    seg = model.segment_samples
    audio_len = len(audio)
    padded = np.concatenate([audio, np.zeros(int(np.ceil(audio_len / seg)) * seg - audio_len)])[None, :]
    segments = model.enframe(padded, seg)

    # Same as PianoTranscription.transcribe, but batched by hand so we can report progress.
    outputs: dict[str, list] = {}
    net = model.model
    net.eval()
    dev = next(net.parameters()).device
    batch = 4
    for i in range(0, len(segments), batch):
        x = torch.as_tensor(segments[i : i + batch], dtype=torch.float32, device=dev)
        with torch.no_grad():
            out = net(x)
        for key, value in out.items():
            outputs.setdefault(key, []).append(value.cpu().numpy())
        progress(min(1.0, (i + batch) / len(segments)))
    output_dict = {k: model.deframe(np.concatenate(v, axis=0))[:audio_len] for k, v in outputs.items()}

    post = RegressionPostProcessor(
        model.frames_per_second,
        classes_num=model.classes_num,
        onset_threshold=onset_threshold,
        offset_threshold=model.offset_threshod,
        frame_threshold=model.frame_threshold,
        pedal_offset_threshold=model.pedal_offset_threshold,
    )
    note_events, pedal_events = post.output_dict_to_midi_events(output_dict)
    notes = [
        [round(float(e["onset_time"]), 3), round(float(e["offset_time"]), 3), int(e["midi_note"]), int(e["velocity"])]
        for e in note_events
        if e["offset_time"] > e["onset_time"]
    ]
    pedals = [[round(float(e["onset_time"]), 3), round(float(e["offset_time"]), 3)] for e in (pedal_events or [])]
    return sorted(notes), pedals


def monophonic(path: Path, fmin: float, fmax: float, progress: Progress) -> Notes:
    """Pitch-track a single melodic line and cut it into notes."""
    sr, hop = 16000, 256
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    progress(0.1)
    f0, voiced, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=2048, hop_length=hop)
    progress(0.8)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    rms = np.pad(rms, (0, max(0, len(f0) - len(rms))))[: len(f0)]
    loud = rms > max(1e-4, np.percentile(rms[rms > 0], 20) if np.any(rms > 0) else 0)
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop)
    onsets = set(onset_frames.tolist())
    near_onset = np.zeros(len(f0) + 1, dtype=bool)
    for o in onset_frames:
        near_onset[max(0, o - 2) : o + 6] = True

    frame_t = hop / sr
    midi = np.where(voiced & loud & ~np.isnan(f0), np.round(librosa.hz_to_midi(np.nan_to_num(f0, nan=1.0))), -1)
    peak = rms.max() or 1.0
    notes: Notes = []
    start, current = None, -1
    for i in range(len(midi) + 1):
        m = midi[i] if i < len(midi) else -1
        # A new note begins on a pitch change, or on a re-attack of the same pitch.
        boundary = m != current or (i in onsets and current != -1)
        if boundary:
            if current != -1 and start is not None and (i - start) * frame_t >= 0.06:
                vel = int(np.clip(40 + 87 * np.sqrt(rms[start:i].max() / peak), 1, 127))
                notes.append([round(start * frame_t, 3), round(i * frame_t, 3), int(current), vel, bool(near_onset[start])])
            start, current = i, m
    progress(1.0)
    return _merge_glitches(notes)


def _merge_glitches(notes: list) -> Notes:
    """Fold pitch flickers (A, tiny B, A) back into one A, unless the second A was re-attacked."""
    out: Notes = []
    for *n, attacked in notes:
        prev = out[-1] if out else None
        if prev and n[2] == prev[2] and n[0] - prev[1] < 0.1 and (not attacked or prev[1] - prev[0] < 0.12):
            # Same pitch straight after: a flicker, or a sliver cut off just before the attack.
            prev[1] = n[1]
            if attacked:
                prev[3] = max(prev[3], n[3])
        else:
            out.append(n)
    return out


# General MIDI percussion keys.
KICK, SNARE, HIHAT = 36, 38, 42


def drums(path: Path, progress: Progress) -> Notes:
    sr, hop = 22050, 256
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    onsets = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop, backtrack=False, units="samples")
    progress(0.4)
    if len(onsets) == 0:
        return []
    win = int(0.05 * sr)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    bands = [(20, 150), (150, 4000), (6000, 11000)]
    energy = np.zeros((len(onsets), 3))
    for i, s in enumerate(onsets):
        frame = y[s : s + win]
        if len(frame) < win:
            frame = np.pad(frame, (0, win - len(frame)))
        spec = np.abs(np.fft.rfft(frame * np.hanning(win))) ** 2
        for b, (lo, hi) in enumerate(bands):
            energy[i, b] = spec[(freqs >= lo) & (freqs < hi)].sum()
    progress(0.8)
    # Normalise each band against its own loud hits, so the split is relative to the track.
    ref = np.percentile(energy, 90, axis=0) + 1e-9
    rel = energy / ref
    total = energy.sum(axis=1)
    peak = np.percentile(total, 95) + 1e-9
    notes: Notes = []
    for i, s in enumerate(onsets):
        t = round(s / sr, 3)
        vel = int(np.clip(30 + 97 * np.sqrt(total[i] / peak), 1, 127))
        kick, snare, hat = rel[i]
        hits = []
        if kick > 0.35 and kick >= 0.6 * max(snare, hat):
            hits.append(KICK)
        if snare > 0.35 and snare >= 0.8 * kick:
            hits.append(SNARE)
        if hat > 0.3 and not hits:
            hits.append(HIHAT)
        if not hits:
            hits.append(HIHAT if hat >= max(kick, snare) else (KICK if kick > snare else SNARE))
        notes.extend([t, round(t + 0.1, 3), h, vel] for h in hits)
    progress(1.0)
    return notes


_beat_model = None


def _fill_gaps(beats: np.ndarray) -> np.ndarray:
    """Put back beats the tracker skipped (an interval of about two or more beats)."""
    if len(beats) < 3:
        return beats
    spb = float(np.median(np.diff(beats)))
    out = [beats[0]]
    for b in beats[1:]:
        missing = int(round((b - out[-1]) / spb)) - 1
        if missing >= 1 and (b - out[-1]) > 1.6 * spb:
            out += list(np.linspace(out[-1], b, missing + 2)[1:-1])
        out.append(b)
    return np.array(out)


def beat_grid(path: Path, duration: float, device: str) -> tuple[float, list[float], int, bool]:
    """Tempo, beat times, beats per bar and compound time, from the Beat This! tracker (Foscarin et al. 2024).

    The returned grid is extrapolated at both ends and starts on a downbeat, so beat
    index 0 is the start of bar 1 (possibly before the music, at negative time).
    """
    global _beat_model
    from beat_this.inference import Audio2Beats

    if _beat_model is None:
        _beat_model = Audio2Beats(checkpoint_path="final0", device=device, dbn=False)
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    beats, downbeats = (np.asarray(a, dtype=float) for a in _beat_model(y, sr))
    if len(beats) < 4:
        tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0]) or 120.0
        beats, downbeats = np.arange(0, duration, 60.0 / tempo), np.array([0.0])

    beats = _fill_gaps(beats)
    # Beats per bar: the most common number of beats between consecutive downbeats.
    idx = np.searchsorted(beats, downbeats - 0.03)
    counts = np.diff(idx)
    counts = counts[(counts >= 2) & (counts <= 12)]
    per_bar = int(np.bincount(counts).argmax()) if len(counts) else 4
    first_down = int(idx[0]) if len(idx) else 0
    compound = per_bar in (6, 9, 12)
    if compound:
        # The tracker followed eighth notes in 6/8, 9/8 or 12/8: keep every third one,
        # so a beat is a dotted quarter, the way those meters are counted.
        beats = beats[first_down % 3 :: 3]
        first_down //= 3
        per_bar //= 3

    spb = float(np.median(np.diff(beats)))
    grid = list(beats)
    # Extend backwards until the grid starts on a downbeat at or before time zero.
    while grid[0] > 0.02 or first_down % per_bar:
        grid.insert(0, grid[0] - spb)
        first_down += 1
    while grid[-1] < duration + spb:
        grid.append(grid[-1] + spb)
    return round(60.0 / spb, 1), [round(float(b), 4) for b in grid], per_bar, compound
