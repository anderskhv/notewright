"""Measure a transcription, so a change can be checked against numbers instead of by ear.

    python -m notewright.scorecard "Song title"

For the most recent finished song whose title contains the text, it prints:

- bass: how many plucks in the bass part have no note (missed) and how many notes
  have no pluck (phantom), the range, and how many notes lie above G3 and C4;
- piano in the bass part: how much louder the bass part gets at the pitch of each high
  piano note (x1.0 means no leak);
- beat grid: how steady the tempo is, minute by minute (a jump to about half or double
  speed moves every later bar line).

Run it before and after a change on the same songs; the numbers should not get worse.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .pipeline import jobs_dir, list_jobs


def _load(path: Path, sr: int = 22050) -> np.ndarray:
    import librosa

    with tempfile.TemporaryDirectory() as tmp:  # m4a: decode with ffmpeg first
        wav = Path(tmp) / "a.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1",
                        "-ar", str(sr), str(wav)], check=True)
        y, _ = librosa.load(str(wav), sr=sr)
    return y


def bass(notes: list, y: np.ndarray, sr: int) -> dict:
    import librosa

    from .transcribe import attacks

    plucks = attacks(y, sr)
    starts = np.array(sorted(n[0] for n in notes))
    pitches = np.array([n[2] for n in notes])
    if not len(starts) or not len(plucks):
        return {}
    near = lambda a, b: np.array([np.min(np.abs(b - x)) for x in a])  # noqa: E731
    return {
        "notes": len(notes),
        "missed": float(np.mean(near(plucks, starts) > 0.06)),
        "phantom": float(np.mean(near(starts, plucks) > 0.06)),
        "range": f"{librosa.midi_to_note(int(pitches.min()))}-{librosa.midi_to_note(int(pitches.max()))}",
        "above G3": int(np.sum(pitches > 55)),
        "above C4": int(np.sum(pitches > 60)),
    }


def leak(piano_notes: list, y: np.ndarray, sr: int) -> dict:
    import librosa

    spec = np.abs(librosa.stft(y, n_fft=4096, hop_length=512))
    f = librosa.fft_frequencies(sr=sr, n_fft=4096)
    t = librosa.frames_to_time(np.arange(spec.shape[1]), sr=sr, hop_length=512)
    ratios = []
    for s, e, m, *_ in piano_notes:
        if m < 64 or e - s < 0.15:
            continue
        hz = librosa.midi_to_hz(m)
        band = (f > hz * 0.97) & (f < hz * 1.03)
        during, before = (t >= s + 0.02) & (t <= min(e, s + 0.3)), (t >= s - 0.5) & (t <= s - 0.25)
        if during.any() and before.any():
            ratios.append(spec[band][:, during].mean() / (spec[band][:, before].mean() + 1e-9))
    if not ratios:
        return {}
    r = np.array(ratios)
    return {"median": float(np.median(r)), "doubled": float(np.mean(r > 2))}


def grid(beats: list) -> dict:
    b = np.array(beats)
    b = b[b >= 0]
    iv, t = np.diff(b), b[:-1]
    per_min = [float(60 / np.median(iv[(t >= a) & (t < a + 60)])) for a in range(0, int(t[-1]), 60)
               if np.sum((t >= a) & (t < a + 60)) > 4]
    typical = float(np.median(per_min)) if per_min else 0.0
    flips = sum(1 for x in per_min if not 0.75 < x / typical < 1.33) if typical else 0
    return {"bpm by minute": [round(x) for x in per_min], "minutes at another tempo level": flips}


def main(text: str) -> None:
    found = [j for j in list_jobs() if text.lower() in j["title"].lower() and j["status"] == "done"]
    if not found:
        sys.exit(f'No finished song with "{text}" in the title.')
    d = jobs_dir() / found[0]["id"]
    r = json.loads((d / "result.json").read_text())
    tracks = {t["id"]: t for t in r["tracks"]}
    print(f'{r["title"]} ({found[0]["id"]})')
    sr = 22050
    if "bass" in tracks and (d / tracks["bass"].get("stem", "")).is_file():
        yb = _load(d / tracks["bass"]["stem"], sr)
        b = bass(tracks["bass"]["notes"], yb, sr)
        if b:
            print(f'  bass: {b["notes"]} notes ({tracks["bass"].get("source", "pitch tracking")}), '
                  f'missed {b["missed"]:.0%}, phantom {b["phantom"]:.0%}, range {b["range"]}, '
                  f'{b["above G3"]} above G3, {b["above C4"]} above C4')
        if "piano" in tracks:
            lk = leak(tracks["piano"]["notes"], yb, sr)
            if lk:
                print(f'  piano in the bass part: x{lk["median"]:.1f} at high piano notes, '
                      f'{lk["doubled"]:.0%} of them doubled')
    g = grid(r["beats"])
    print(f'  beat grid: {g["minutes at another tempo level"]} minute(s) at another tempo level; '
          f'bpm by minute {g["bpm by minute"]}')


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
