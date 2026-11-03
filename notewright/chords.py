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


# Common form lengths in bars: blues (12), 16-bar tunes, 32-bar standards (AABA, ABAC),
# and doubles of them.
FORMS = (12, 16, 24, 32)


def _form(profiles: np.ndarray, steps_per_bar: int) -> int | None:
    """The length of the chorus in steps, if the harmony clearly repeats.

    A form's sections repeat inside it (on Autumn Leaves bars 8 apart are as alike as bars
    32 apart), so the longest common form length about as alike as the best one is taken.
    """
    n = len(profiles)
    unit = profiles / (np.linalg.norm(profiles, axis=1, keepdims=True) + 1e-9)
    def alike(lag):
        return float(np.mean(np.sum(unit[:-lag] * unit[lag:], axis=1))) if lag < n else 0.0
    scores = {bars: alike(bars * steps_per_bar) for bars in FORMS if 2 * bars * steps_per_bar <= n}
    if not scores:
        return None
    baseline = np.mean([alike(lag * steps_per_bar) for lag in (5, 7, 9, 11, 13) if lag * steps_per_bar < n])
    best = max(scores.values())
    if best < baseline + 0.02:
        return None                                    # no clear repetition: name each step alone
    bars = max(b for b, v in scores.items() if v >= best - 0.01)
    return bars * steps_per_bar


def detect(tracks: list[dict], beats: list[float], beats_per_bar: int) -> list[list]:
    """[[start, end, name], ...] on the beat grid, consecutive repeats merged.

    Where the harmony repeats chorus after chorus, each step is named from the same place
    in every chorus together: one chorus's notes are thin evidence (a missed note, a
    passing tone), all of them together are the tune's changes. On a Ron Carter Autumn
    Leaves the chord root matched the standard changes in 62% of bars, against 23%.
    """
    step = beats_per_bar if beats_per_bar == 3 else 2
    edges = [(beats[i], beats[i + step]) for i in range(0, len(beats) - step, step)]
    if not edges:
        return []
    pcs, bass = zip(*(_profile(tracks, a, b) for a, b in edges))
    pcs, bass = np.array(pcs), np.array(bass)
    period = _form(pcs, beats_per_bar // step)
    if period:
        own = 0.1                                      # a little of the step itself, for what differs
        for pos in range(period):
            idx = np.arange(pos, len(edges), period)
            mp, mb = pcs[idx].mean(axis=0), bass[idx].mean(axis=0)
            pcs[idx] = own * pcs[idx] + (1 - own) * mp
            bass[idx] = own * bass[idx] + (1 - own) * mb
    out: list[list] = []
    for (a, b), p, bs in zip(edges, pcs, bass):
        name = name_chord(p, bs)
        if name is None:
            continue
        if out and out[-1][2] == name and abs(out[-1][1] - a) < 1e-6:
            out[-1][1] = b
        else:
            out.append([round(a, 3), round(b, 3), name])
    return out
