"""Notes to sheet music (MusicXML) and MIDI.

Sheet music is written on the detected beat grid, so rubato and tempo drift in the
recording do not push bar lines out of place. Beat k of the score (k = 0 is the first
downbeat) sounds at beats[k] seconds; the player uses the same grid for its cursor.

Rhythm is quantised one beat at a time: each beat picks the subdivision that fits its
notes best (straight 16ths or triplets), so swung and triplet passages read as such.
Songs whose beats are mostly split in three are written in compound time (6/8, 9/8, 12/8).
"""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np


@dataclass
class Meter:
    beats_per_bar: int
    compound: bool
    swing: bool = False  # swung eighths, written straight as jazz charts do
    subdivision: int = 4  # finest straight note per beat: 4 = sixteenths, 2 = eighths

    @property
    def beat_ql(self) -> Fraction:
        """Length of one beat in quarter notes."""
        return Fraction(3, 2) if self.compound else Fraction(1)

    @property
    def signature(self) -> str:
        return f"{self.beats_per_bar * 3}/8" if self.compound else f"{self.beats_per_bar}/4"

    @property
    def bar_ql(self) -> Fraction:
        return self.beat_ql * self.beats_per_bar


def seconds_to_beats(times, beats: list[float]) -> np.ndarray:
    idx = np.arange(len(beats), dtype=float)
    return np.interp(times, beats, idx)


def _fractions(notes, beats) -> np.ndarray:
    x = seconds_to_beats([n[0] for n in notes], beats)
    return x - np.floor(x)


def detect_meter(notes, beats: list[float], beats_per_bar: int, compound: bool = False, bpm: float = 100) -> Meter:
    # A slow tune counted in two is a ballad in four whose downbeats were found every
    # other bar line: real 2/4 at under 90 bpm is rare.
    if beats_per_bar == 2 and not compound and bpm < 90:
        beats_per_bar = 4
    meter = _meter(notes, beats, beats_per_bar, compound)
    # Fast tunes read better in eighths: sixteenths at 200 bpm are mostly jitter.
    if not meter.compound and (bpm >= 200 or (meter.swing and bpm >= 130)):
        meter.subdivision = 2
    return meter


def _meter(notes, beats: list[float], beats_per_bar: int, compound: bool) -> Meter:
    """Simple, compound or swung time, from where off-beat onsets land inside the beat.

    The beat tracker may already know a meter is compound (it counted eighth notes in
    threes). Otherwise: onsets on both 1/3 and 2/3 of the beat throughout mean compound
    time; a long-short pair (the off-beat late, around 0.6-0.75 of the beat) with few
    onsets on 1/3 means swing, which jazz writes as plain eighth notes.
    """
    if beats_per_bar not in (2, 3, 4):
        beats_per_bar = 4
    if compound or not notes:
        return Meter(beats_per_bar, compound)
    f = _fractions(notes, beats)
    count = lambda lo, hi: int(np.sum((f > lo) & (f < hi)))  # noqa: E731
    one_third = count(0.28, 0.39)
    swung = count(0.58, 0.76)
    half = count(0.45, 0.55)
    quarters = count(0.2, 0.28) + count(0.76, 0.82)
    if one_third > 0.4 * swung and one_third + swung > 3 * (half + quarters) and one_third + swung > 0.25 * len(f):
        return Meter(beats_per_bar, True)
    swing = swung > 1.5 * half and swung > 2 * one_third and swung >= 0.08 * len(f)
    return Meter(beats_per_bar, False, bool(swing))


def _unswing(fr: float) -> float:
    """Map a swung off-beat (about 2/3 of the beat) back to the written half beat."""
    return fr * 0.75 if fr <= 2 / 3 else 0.5 + (fr - 2 / 3) * 1.5


def _beat_grids(x: np.ndarray, meter: Meter) -> dict[int, int]:
    """Subdivisions per beat, chosen per beat from the onsets that fall in it."""
    if meter.compound:
        return {}
    grids: dict[int, list] = {}
    for v in x:
        grids.setdefault(int(np.floor(v)), []).append(v - np.floor(v))
    out = {}
    for b, fracs in grids.items():
        fr = np.array(fracs)
        cost = lambda g: np.abs(fr * g - np.round(fr * g)).sum() / g  # noqa: E731
        # In swing a late off-beat is normal; only an onset on the first third makes a triplet.
        triplet_hint = np.any(np.abs(fr - 1 / 3) < 0.08) or (not meter.swing and np.any(np.abs(fr - 2 / 3) < 0.08))
        if triplet_hint:
            out[b] = 3 if cost(3) < 0.7 * cost(meter.subdivision) else meter.subdivision
    return out


def quantise(notes, beats: list[float], meter: Meter) -> list[tuple[Fraction, Fraction, int]]:
    """[(onset, offset, pitch)] in quarter notes from the first downbeat."""
    if not notes:
        return []
    arr = np.array(notes, dtype=float)
    on_x = seconds_to_beats(arr[:, 0], beats)
    off_x = seconds_to_beats(arr[:, 1], beats)
    default = 6 if meter.compound else meter.subdivision
    grids = _beat_grids(on_x, meter)

    def snap(v: float) -> Fraction:
        b = int(np.floor(v))
        g = grids.get(b, default)
        fr = v - b
        if meter.swing and g in (2, 4):
            fr = _unswing(fr)
        return (b + Fraction(round(fr * g), g)) * meter.beat_ql

    out = []
    for a, b, p in zip(on_x, off_x, arr[:, 2]):
        on, off = snap(a), snap(b)
        if off <= on:
            off = on + meter.beat_ql / grids.get(int(np.floor(a)), default)
        out.append((max(Fraction(0), on), off, int(p)))
    return out


def split_hands(qnotes):
    """Assign each note to the right or left hand, following where the hands are."""
    by_onset: dict[Fraction, list] = {}
    for n in qnotes:
        by_onset.setdefault(n[0], []).append(n)
    right_c, left_c = 72.0, 48.0
    right, left = [], []
    for on in sorted(by_onset):
        group = sorted(by_onset[on], key=lambda n: n[2])
        pitches = [n[2] for n in group]
        best, best_k = None, 0
        for k in range(len(group) + 1):
            lo, hi = pitches[:k], pitches[k:]
            cost = sum(abs(p - left_c) for p in lo) + sum(abs(p - right_c) for p in hi)
            # A hand spans about a tenth; anything wider has to go to the other hand.
            for part in (lo, hi):
                if part and part[-1] - part[0] > 15:
                    cost += 100 * (part[-1] - part[0] - 15)
            if best is None or cost < best:
                best, best_k = cost, k
        lo, hi = group[:best_k], group[best_k:]
        left += lo
        right += hi
        if lo:
            left_c = 0.7 * left_c + 0.3 * np.mean([n[2] for n in lo])
        if hi:
            right_c = 0.7 * right_c + 0.3 * np.mean([n[2] for n in hi])
        # Keep the hands from drifting into each other.
        if right_c - left_c < 12:
            mid = (right_c + left_c) / 2
            right_c, left_c = mid + 6, mid - 6
    return right, left


def _staff_events(qnotes, bar_ql: Fraction, beat_ql: Fraction = Fraction(1)):
    """Collapse overlapping notes into one chord sequence a player can read.

    A gap shorter than a beat before the next chord is absorbed into the chord,
    as a copyist would: detached playing is shown with fewer, longer notes rather
    than a rest after every one.
    """
    by_onset: dict[Fraction, list] = {}
    for on, off, pitch in qnotes:
        by_onset.setdefault(on, []).append((off - on, pitch))
    onsets = sorted(by_onset)
    events = []
    for i, on in enumerate(onsets):
        group = by_onset[on]
        longest = max(d for d, _ in group)
        gap = onsets[i + 1] - on if i + 1 < len(onsets) else longest
        dur = gap if gap <= beat_ql else max(longest, min(gap, beat_ql))
        events.append((on, min(dur, gap, bar_ql), sorted({p for _, p in group})))
    return events


def _expressible(ql: Fraction) -> bool:
    from music21 import duration

    return duration.Duration(ql).type not in ("complex", "inexpressible")


def _pieces(on: Fraction, dur: Fraction, beat: Fraction) -> list[Fraction]:
    """Split a duration into notatable pieces, breaking at beats the way engravers do."""
    pos = on % beat
    if _expressible(dur) and (pos == 0 or pos + dur <= beat):
        return [dur]
    if pos:
        first = min(dur, beat - pos)
        return [first] + (_pieces(on + first, dur - first, beat) if dur > first else [])
    whole = (dur // beat) * beat
    if whole and _expressible(whole):
        first = whole
    elif whole:
        first = beat
    else:
        first = dur
        if not _expressible(dur):  # last resort: walk down the 32nd-triplet grid
            first = Fraction(int(dur * 24), 24) or dur
    rest = dur - first
    return [first] + (_pieces(on + first, rest, beat) if rest > 0 else [])


SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"]


def _pitch(midi: int, flats: bool):
    """Spell black keys to suit the key signature, and leave naturals implicit."""
    from music21 import pitch

    name = (FLAT_NAMES if flats else SHARP_NAMES)[midi % 12]
    p = pitch.Pitch(f"{name}{midi // 12 - 1}")
    if p.accidental is not None and p.accidental.name == "natural":
        p.accidental = None
    return p


def _fill_part(part, events, end: Fraction, meter: Meter, clef, flats: bool) -> None:
    from music21 import chord, meter as m21meter, note, tie

    part.insert(0, clef)
    part.insert(0, m21meter.TimeSignature(meter.signature))
    beat = meter.beat_ql

    def place(on, dur, pitches):
        pieces = _pieces(on, dur, beat)
        for i, ql in enumerate(pieces):
            if pitches is None:
                el = note.Rest()
            elif len(pitches) == 1:
                el = note.Note(_pitch(pitches[0], flats))
            else:
                el = chord.Chord([_pitch(p, flats) for p in pitches])
            el.quarterLength = ql
            if pitches is not None and len(pieces) > 1:
                el.tie = tie.Tie("start" if i == 0 else "stop" if i == len(pieces) - 1 else "continue")
            part.insert(on, el)
            on += ql

    cursor = Fraction(0)
    for on, dur, pitches in events:
        if on > cursor:
            place(cursor, on - cursor, None)
        place(on, dur, pitches)
        cursor = on + dur
    if end > cursor:
        place(cursor, end - cursor, None)


_KINDS = {
    "": "major", "m": "minor", "7": "dominant-seventh", "maj7": "major-seventh", "m7": "minor-seventh",
    "m7♭5": "half-diminished-seventh", "°7": "diminished-seventh", "6": "major-sixth", "m6": "minor-sixth",
    "sus4": "suspended-fourth", "7sus4": "suspended-fourth-seventh", "+": "augmented", "°": "diminished",
}


def _chord_symbol(name: str):
    from music21 import harmony

    root = name[:2] if len(name) > 1 and name[1] == "♭" else name[:1]
    return harmony.ChordSymbol(root=root.replace("♭", "-"), kind=_KINDS[name[len(root):]])


def write_musicxml(notes, beats, meter: Meter, bpm, title, instrument, path: Path, grand_staff: bool,
                   chords: list | None = None) -> None:
    from music21 import clef, instrument as m21inst, key, layout, metadata, note, stream, tempo

    q = quantise(notes, beats, meter)
    last = max((off for _, off, _ in q), default=meter.bar_ql)
    end = -(-last // meter.bar_ql) * meter.bar_ql  # round up to a whole bar

    sharps = 0
    if q:
        sample = stream.Stream([note.Note(p, quarterLength=1) for _, _, p in q])
        sharps = sample.analyze("key").sharps
    flats = sharps < 0

    score = stream.Score()
    score.metadata = metadata.Metadata(title=title, composer="Transcribed by Notewright")
    if grand_staff:
        right, left = split_hands(q)
        upper, lower = stream.PartStaff(id="upper"), stream.PartStaff(id="lower")
        _fill_part(upper, _staff_events(right, meter.bar_ql, meter.beat_ql), end, meter, clef.TrebleClef(), flats)
        _fill_part(lower, _staff_events(left, meter.bar_ql, meter.beat_ql), end, meter, clef.BassClef(), flats)
        upper.insert(0, m21inst.Piano())
        parts = [upper, lower]
    else:
        part = stream.Part(id=instrument)
        median = np.median([n[2] for n in q]) if q else 60
        # Bass clef for low parts; the octave-down treble clef (as for guitar or tenor
        # voice) for the tenor register, so a tenor sax is not all ledger lines.
        staff_clef = clef.BassClef() if median < 50 else clef.Treble8vbClef() if median < 62 else clef.TrebleClef()
        _fill_part(part, _staff_events(q, meter.bar_ql, meter.beat_ql), end, meter, staff_clef, flats)
        part.partName = instrument
        parts = [part]

    from music21 import duration

    parts[0].insert(0, tempo.MetronomeMark(number=round(bpm), referent=duration.Duration(meter.beat_ql)))
    if meter.swing:
        from music21 import expressions

        parts[0].insert(0, expressions.TextExpression("Swing"))
    for p in parts:
        p.insert(0, key.KeySignature(sharps))

    for start, _, name in chords or []:
        offset = Fraction(round(float(seconds_to_beats([start], beats)[0]))) * meter.beat_ql
        if 0 <= offset < end:
            try:
                parts[0].insert(offset, _chord_symbol(name))
            except Exception:  # noqa: BLE001 - a symbol music21 cannot spell is left out
                pass
    for p in parts:
        score.insert(0, p)
    if grand_staff:
        score.insert(0, layout.StaffGroup(parts, symbol="brace", barTogether=True))
    score.makeNotation(inPlace=True)
    score.write("musicxml", fp=str(path))


GM_PROGRAMS = {"piano": 0, "guitar": 25, "bass": 33, "vocals": 52, "other": 48}


def write_midi(tracks: list[dict], bpm: float, meter: Meter, path: Path) -> None:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm * (3 if meter.compound else 1) / (2 if meter.compound else 1))
    pm.time_signature_changes.append(
        pretty_midi.TimeSignature(meter.beats_per_bar * (3 if meter.compound else 1), 8 if meter.compound else 4, 0)
    )
    for t in tracks:
        drum = t["id"] == "drums"
        inst = pretty_midi.Instrument(program=GM_PROGRAMS.get(t["id"], 0), is_drum=drum, name=t["name"])
        for start, end, pitch, vel in t["notes"]:
            inst.notes.append(pretty_midi.Note(velocity=int(vel), pitch=int(pitch), start=float(start), end=float(end)))
        pm.instruments.append(inst)
    pm.write(str(path))
