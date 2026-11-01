from music21 import meter, note, stream, tempo

from notewright import score


def _piece(pitches, staves=1, bpm=None):
    s = stream.Score()
    for _ in range(staves):
        part = stream.Part()
        m = stream.Measure(number=1)
        m.append(meter.TimeSignature("3/4"))
        if bpm:
            m.insert(0, tempo.MetronomeMark(number=bpm))
        for p in pitches:
            m.append(note.Note(p, quarterLength=1))
        part.append(m)
        s.insert(0, part)
    return s


def test_read_times_notes_from_the_tempo_mark():
    piece = score.read(_piece(["C4", "E4", "G4"], bpm=120))
    assert piece["bpm"] == 120
    assert [n[:3] for n in piece["notes"]] == [[0.0, 0.5, 60], [0.5, 1.0, 64], [1.0, 1.5, 67]]
    assert piece["timeSignature"] == "3/4" and piece["beatsPerBar"] == 3


def test_read_defaults_to_100_bpm():
    assert score.read(_piece(["C4"]))["bpm"] == score.DEFAULT_BPM


def test_combine_fills_a_missing_staff_with_rest(tmp_path):
    a, b = tmp_path / "a.musicxml", tmp_path / "b.musicxml"
    _piece(["C4", "D4", "E4"], staves=2).write("musicxml", fp=str(a))
    _piece(["G4", "A4", "B4"], staves=1).write("musicxml", fp=str(b))
    piece = score.read(score.combine([a, b]))
    assert piece["staves"] == 2
    top = [n for n in piece["notes"] if n[2] in (67, 69, 71)]
    assert top[0][0] == 1.8  # after the first bar (3 beats at 100 bpm)
