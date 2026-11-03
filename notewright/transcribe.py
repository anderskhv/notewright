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

from .config import MODEL_DIR, PIANO_CHECKPOINT_URL, gpu_or_cpu

Notes = list[list[float]]
Progress = Callable[[float], None]

_piano_models: dict = {}  # one loaded model per device


def _piano_checkpoint() -> Path:
    path = MODEL_DIR / "piano_transcription.pth"
    if not path.exists() or path.stat().st_size < 1.6e8:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        urllib.request.urlretrieve(PIANO_CHECKPOINT_URL, partial)
        partial.rename(path)
    return path


def _load_piano_model(device: str):
    if device not in _piano_models:
        import torch
        from piano_transcription_inference import PianoTranscription

        # The checkpoint is plain tensors, so torch's default weights-only loading reads it.
        _piano_models[device] = PianoTranscription(device=torch.device(device), checkpoint_path=str(_piano_checkpoint()))
    return _piano_models[device]


def sensitivity_thresholds(sensitivity: float) -> tuple[float, float]:
    """Note sensitivity 0..1 to the model's onset and frame thresholds (0.5 = its defaults)."""
    s = min(1.0, max(0.0, sensitivity))
    return 0.5 - 0.4 * s, 0.18 - 0.16 * s


EVEN_OUT = True


def even_out(y: np.ndarray, sr: int, max_gain: float = 6.0) -> np.ndarray:
    """Raise quiet passages towards the level of the loud ones (slow, ~2 s, up to +15 dB).

    The piano model was trained on concert recordings at healthy levels; soft comping
    in a band recording falls under its thresholds. Only the model's input is changed.
    """
    hop = sr // 10
    frames = len(y) // hop
    if frames < 10:
        return y
    rms = np.sqrt((y[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1)) + 1e-6
    smooth = np.convolve(rms, np.ones(20) / 20, mode="same")  # two seconds
    target = np.percentile(smooth, 90)
    gain = np.clip(target / smooth, 1.0, max_gain)
    gain = np.interp(np.arange(len(y)), np.arange(frames) * hop + hop / 2, gain)
    return (y * gain).astype(np.float32)


def model_output(path: Path, device: str, progress: Progress) -> dict[str, np.ndarray]:
    """Run the piano model; returns its per-frame probabilities for every key."""
    return gpu_or_cpu(device, lambda dev: _model_output(path, dev, progress), "The piano model")


def _model_output(path: Path, device: str, progress: Progress) -> dict[str, np.ndarray]:
    import torch

    model = _load_piano_model(device)
    audio, _ = librosa.load(str(path), sr=16000, mono=True)
    if EVEN_OUT:
        audio = even_out(audio, 16000)
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
    return {k: model.deframe(np.concatenate(v, axis=0))[:audio_len] for k, v in outputs.items()}


def save_output(output: dict[str, np.ndarray], path: Path) -> None:
    # float16 keeps a five-minute song to a few tens of MB.
    np.savez_compressed(path, **{k: v.astype(np.float16) for k, v in output.items()})


def load_output(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as data:
        return {k: data[k].astype(np.float32) for k in data.files}


def notes_from_output(output: dict[str, np.ndarray], sensitivity: float) -> tuple[Notes, list]:
    """Read notes out of the model's probabilities. Cheap, so sensitivity can be changed later."""
    from piano_transcription_inference import config
    from piano_transcription_inference.utilities import RegressionPostProcessor

    onset, frame = sensitivity_thresholds(sensitivity)
    post = RegressionPostProcessor(
        config.frames_per_second,
        classes_num=config.classes_num,
        onset_threshold=onset,
        offset_threshold=0.3,
        frame_threshold=frame,
        pedal_offset_threshold=0.2,
    )
    note_events, pedal_events = post.output_dict_to_midi_events(output)
    notes = [
        [round(float(e["onset_time"]), 3), round(float(e["offset_time"]), 3), int(e["midi_note"]), int(e["velocity"])]
        for e in note_events
        if e["offset_time"] > e["onset_time"]
    ]
    pedals = [[round(float(e["onset_time"]), 3), round(float(e["offset_time"]), 3)] for e in (pedal_events or [])]
    return sorted(notes), pedals


def monophonic(path: Path, fmin: float, fmax: float, progress: Progress, gate: float = 0.0) -> Notes:
    """Pitch-track a single melodic line and cut it into notes.

    gate: ignore frames quieter than this fraction of the line's loud passages (its
    90th-percentile level), for parts that are mostly leakage while others solo.
    """
    sr, hop = 16000, 256
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    progress(0.1)
    f0, voiced, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=2048, hop_length=hop)
    progress(0.8)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    rms = np.pad(rms, (0, max(0, len(f0) - len(rms))))[: len(f0)]
    loud = rms > max(1e-4, np.percentile(rms[rms > 0], 20) if np.any(rms > 0) else 0)
    if gate and np.any(rms > 0):
        # Judge loudness over half a second, so the gaps inside a phrase do not count.
        level = np.convolve(rms, np.ones(31) / 31, mode="same")
        loud &= level > gate * np.percentile(rms, 90)
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


def attacks(y: np.ndarray, sr: int) -> np.ndarray:
    """Times where notes are struck or plucked: onsets, moved back to where the energy starts rising."""
    mono = y.mean(axis=0) if y.ndim == 2 else y
    return librosa.onset.onset_detect(y=np.asarray(mono, dtype=np.float32), sr=sr, hop_length=128,
                                      backtrack=True, units="time")


def snap_to_attacks(notes: list, times: np.ndarray, window: float) -> list:
    """Move each note's start to the nearest attack within the window, keeping its end.

    Pitch trackers and note models place a start a few frames off the pluck; for a bass
    line that blurs the time feel (swing lives in 10-20 ms). The attack in the part's own
    audio is where the note really begins.
    """
    if not len(times):
        return notes
    out = []
    for n in notes:
        i = int(np.clip(np.searchsorted(times, n[0]), 1, len(times) - 1))
        near = times[i - 1] if abs(times[i - 1] - n[0]) <= abs(times[i] - n[0]) else times[i]
        start = float(near) if abs(near - n[0]) <= window and near < n[1] - 0.02 else n[0]
        out.append([round(start, 4), *n[1:]])
    return out


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


_beat_models: dict = {}


def _fill_gaps(beats: np.ndarray) -> np.ndarray:
    """Put back beats the tracker skipped (an interval of about two or more local beats).

    "Local" matters: a ballad that slows down has long beats late on, and comparing them
    with the song's overall median would invent beats that are not there.
    """
    if len(beats) < 3:
        return beats
    iv = np.diff(beats)
    out = [beats[0]]
    for i, b in enumerate(beats[1:]):
        lo, hi = max(0, i - 8), min(len(iv), i + 9)
        neighbours = np.delete(iv[lo:hi], i - lo)  # the interval itself does not vote
        local = float(np.median(neighbours)) if len(neighbours) else float(iv[i])
        missing = int(round((b - out[-1]) / local)) - 1
        if missing >= 1 and (b - out[-1]) > 1.6 * local:
            out += list(np.linspace(out[-1], b, missing + 2)[1:-1])
        out.append(b)
    return np.array(out)


def _local_beat(iv: np.ndarray, i: int, reach: int = 8) -> float:
    lo, hi = max(0, i - reach), min(len(iv), i + reach + 1)
    return float(np.median(iv[lo:hi]))


def _fix_tempo_octave(beats: np.ndarray) -> np.ndarray:
    """Count the whole song at one tempo level.

    Beat trackers switch between half and double time inside a song (on a Ron Carter
    trio: 188 bpm, then 94, then 188 again). Every switch moves the bar lines, so a chord
    chart or the form can no longer line up. Passages where the local beat is about
    twice the song's typical beat get a beat in between; passages at about half of it
    lose every other beat (keeping the ones that continue the beat before).
    """
    if len(beats) < 16:
        return beats
    iv = np.diff(beats)
    local = np.array([_local_beat(iv, i) for i in range(len(iv))])
    typical = float(np.median(local))
    ratio = local / typical
    out = [beats[0]]
    i = 0
    while i < len(iv):
        if 1.7 <= ratio[i] <= 2.35:          # counted at half speed here: add the missing beat
            out += [beats[i] + iv[i] / 2, beats[i + 1]]
            i += 1
        elif 0.42 <= ratio[i] <= 0.6:        # counted at double speed: a run of short intervals
            j = i
            while j < len(iv) and 0.42 <= ratio[j] <= 0.6:
                j += 1
            run = beats[i:j + 1]
            # Keep every other beat, starting where a beat at the typical spacing lands.
            phase = 0 if len(out) < 2 or abs((run[0] - out[-2]) - typical) <= abs((run[1] - out[-2]) - typical) else 1
            kept = list(run[phase::2])
            if kept and abs(kept[0] - out[-1]) < 1e-9:
                kept = kept[1:]
            out += kept
            if j < len(beats) - 1 and (not kept or abs(kept[-1] - beats[j]) > 1e-9):
                out.append(beats[j])
            i = j
        else:
            out.append(beats[i + 1])
            i += 1
    return np.array(sorted(set(np.round(out, 4))))


def _anchor_bars(beats: np.ndarray, downbeats: np.ndarray, per_bar: int) -> tuple[np.ndarray, int]:
    """Keep the bar lines on the tracker's downbeats through the whole song.

    The grid counts bars every per_bar beats from the first downbeat, so one extra or
    missing beat anywhere moved every later bar line. Here the downbeats are followed:
    where a steady run of them says the bar line has moved, the bar before it gets
    exactly per_bar evenly spaced beats, so the error stays inside that one bar. A lone
    downbeat that disagrees with its neighbours is the tracker's mistake and is ignored.
    Returns the beats and the index of the first downbeat.
    """
    if len(downbeats) < 3 or len(beats) < per_bar * 3:
        idx = np.searchsorted(beats, downbeats - 0.03) if len(downbeats) else np.array([0])
        return beats, int(idx[0]) if len(idx) else 0
    near = []
    for t in downbeats:
        k = int(np.argmin(np.abs(beats - t)))
        if abs(beats[k] - t) <= 0.1:
            near.append(k)
    near = sorted(set(near))
    if len(near) < 3:
        return beats, near[0] if near else 0
    out = list(beats[: near[0] + 1])
    anchor = near[0]
    for j in range(1, len(near)):
        k = near[j] - anchor
        steady = j + 1 < len(near) and (near[j + 1] - near[j]) % per_bar == 0
        if k % per_bar == 0:
            out += list(beats[anchor + 1: near[j] + 1])
        elif steady and k >= per_bar // 2:
            bars = max(1, round(k / per_bar))
            keep = (bars - 1) * per_bar
            out += list(beats[anchor + 1: anchor + keep + 1])
            a, b = beats[anchor + keep], beats[near[j]]
            out += list(np.linspace(a, b, per_bar + 1)[1:])
        else:
            continue                          # a lone odd downbeat: not an anchor
        anchor = near[j]
    out += list(beats[anchor + 1:])
    return np.array(out), near[0]


def beat_grid(path: Path, duration: float, device: str) -> tuple[float, list[float], int, bool]:
    """Tempo, beat times, beats per bar and compound time, from the Beat This! tracker (Foscarin et al. 2024).

    The returned grid is extrapolated at both ends and starts on a downbeat, so beat
    index 0 is the start of bar 1 (possibly before the music, at negative time).
    """
    from beat_this.inference import Audio2Beats

    def track(dev: str):
        if dev not in _beat_models:
            _beat_models[dev] = Audio2Beats(checkpoint_path="final0", device=dev, dbn=False)
        return _beat_models[dev](y, sr)

    y, sr = librosa.load(str(path), sr=22050, mono=True)
    beats, downbeats = (np.asarray(a, dtype=float) for a in gpu_or_cpu(device, track, "Finding the beat"))
    if len(beats) < 4:
        tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0]) or 120.0
        beats, downbeats = np.arange(0, duration, 60.0 / tempo), np.array([0.0])

    beats = _fix_tempo_octave(_fill_gaps(beats))
    # Beats per bar: the most common number of beats between consecutive downbeats.
    idx = np.searchsorted(beats, downbeats - 0.03)
    counts = np.diff(idx)
    counts = counts[(counts >= 2) & (counts <= 12)]
    per_bar = int(np.bincount(counts).argmax()) if len(counts) else 4
    if per_bar not in (6, 9, 12):
        beats, first_down = _anchor_bars(beats, downbeats, per_bar)
    else:
        first_down = int(idx[0]) if len(idx) else 0
    compound = per_bar in (6, 9, 12)
    if compound:
        # The tracker followed eighth notes in 6/8, 9/8 or 12/8: keep every third one,
        # so a beat is a dotted quarter, the way those meters are counted.
        beats = beats[first_down % 3 :: 3]
        first_down //= 3
        per_bar //= 3

    spb = float(np.median(np.diff(beats)))
    # Extrapolate at the tempo the music has at each end, not the song's average.
    head = float(np.median(np.diff(beats[:9]))) if len(beats) > 2 else spb
    tail = float(np.median(np.diff(beats[-9:]))) if len(beats) > 2 else spb
    grid = list(beats)
    # Extend backwards until the grid starts on a downbeat at or before time zero.
    while grid[0] > 0.02 or first_down % per_bar:
        grid.insert(0, grid[0] - head)
        first_down += 1
    while grid[-1] < duration + tail:
        grid.append(grid[-1] + tail)
    return round(60.0 / spb, 1), [round(float(b), 4) for b in grid], per_bar, compound
