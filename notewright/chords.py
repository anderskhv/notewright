"""Chord names from the transcribed notes.

Per stretch of the beat grid (half a bar in 4/4, a bar in 3/4), the pitch classes that
sound are weighed (chordal parts count most, melody least) and matched against chord
shapes. The bass decides the root when it can: jazz pianists often leave the root out
of their voicings for the bassist to play.
"""

import numpy as np

FLAT_NAMES = ["C", "D♭", "D", "E♭", "E", "F", "G♭", "G", "A♭", "A", "B♭", "B"]
# suffix, intervals; earlier entries win ties (simpler names first)
SHAPES = [
    ("", (0, 4, 7)), ("m", (0, 3, 7)), ("7", (0, 4, 7, 10)), ("maj7", (0, 4, 7, 11)), ("m7", (0, 3, 7, 10)),
    ("m7♭5", (0, 3, 6, 10)), ("°7", (0, 3, 6, 9)), ("6", (0, 4, 7, 9)), ("m6", (0, 3, 7, 9)),
    ("sus4", (0, 5, 7)), ("7sus4", (0, 5, 7, 10)), ("+", (0, 4, 8)), ("°", (0, 3, 6)),
]
WEIGHT = {"piano": 1.0, "guitar": 1.0, "other": 0.7, "bass": 0.8}  # anything else (melody): 0.25


def _profile(tracks, a: float, b: float):
    pcs, bass = np.zeros(12), np.zeros(12)
    for t in tracks:
        if t["id"] == "drums":
            continue
        w = WEIGHT.get(t["id"], 0.25)
        for start, end, pitch, *_ in t["notes"]:
            overlap = min(end, b) - max(start, a)
            if overlap > 0:
                pcs[pitch % 12] += w * overlap
                if t["id"] == "bass":
                    bass[pitch % 12] += overlap
    return pcs, bass


def name_chord(pcs: np.ndarray, bass: np.ndarray) -> str | None:
    total = pcs.sum()
    if total <= 0:
        return None
    p = pcs / np.linalg.norm(pcs)
    b = bass / bass.sum() if bass.sum() > 0 else None
    best, best_name = 0.5, None  # below this match, call it nothing
    for root in range(12):
        for suffix, shape in SHAPES:
            template = np.zeros(12)
            template[[(root + i) % 12 for i in shape]] = 1.0
            score = float(p @ template) / np.sqrt(len(shape))  # cosine similarity
            if b is not None:
                score += 0.25 * b[root]  # the bass plays the root
            if score > best + 1e-9:
                best, best_name = score, FLAT_NAMES[root] + suffix
    return best_name


def detect(tracks: list[dict], beats: list[float], beats_per_bar: int) -> list[list]:
    """[[start, end, name], ...] on the beat grid, consecutive repeats merged."""
    step = beats_per_bar if beats_per_bar == 3 else 2
    out: list[list] = []
    for i in range(0, len(beats) - step, step):
        a, b = beats[i], beats[i + step]
        name = name_chord(*_profile(tracks, a, b))
        if name is None:
            continue
        if out and out[-1][2] == name and abs(out[-1][1] - a) < 1e-6:
            out[-1][1] = b
        else:
            out.append([round(a, 3), round(b, 3), name])
    return out
