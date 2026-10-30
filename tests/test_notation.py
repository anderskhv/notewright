"""Sheet-music writing, checked on hand-made note lists (no models needed)."""

from fractions import Fraction

from notewright import notation
from notewright.notation import Meter


def grid(n, spb=0.5):
    return [i * spb for i in range(n)]


def test_straight_sixteenths_stay_straight():
    beats = grid(16)
    notes = [[i * 0.125, i * 0.125 + 0.12, 60, 80] for i in range(8)]
    q = notation.quantise(notes, beats, Meter(4, False))
    assert [on for on, _, _ in q] == [Fraction(i, 4) for i in range(8)]


def test_triplets_are_detected_per_beat():
    beats = grid(16)
    # Beat 0 in straight eighths, beat 1 in triplets.
    notes = [[0, 0.24, 60, 80], [0.25, 0.49, 62, 80]]
    notes += [[0.5 + i / 6, 0.5 + i / 6 + 0.15, 64, 80] for i in range(3)]
    q = notation.quantise(notes, beats, Meter(4, False))
    assert [on for on, _, _ in q] == [0, Fraction(1, 2), 1, Fraction(4, 3), Fraction(5, 3)]


def test_compound_time_from_three_way_split():
    beats = grid(16)
    notes = [[b * 0.5 + i / 6, b * 0.5 + i / 6 + 0.15, 60, 80] for b in range(8) for i in range(3)]
    meter = notation.detect_meter(notes, beats, 2)
    assert meter.signature == "6/8"
    assert notation.detect_meter(notes[:3] + [[1.25, 1.4, 60, 80], [1.75, 1.9, 60, 80]] * 3, beats, 4).signature == "4/4"


def test_pieces_are_all_notatable():
    beat = Fraction(1)
    for on, dur in [(Fraction(1, 3), Fraction(11, 12) + 1), (Fraction(1, 4), Fraction(5, 4)), (Fraction(0), Fraction(5, 3))]:
        pieces = notation._pieces(on, dur, beat)
        assert sum(pieces) == dur
        assert all(notation._expressible(p) for p in pieces)


def test_hands_follow_the_music():
    q = [(Fraction(0), Fraction(1), p) for p in (36, 43, 48, 64, 67, 72)]
    right, left = notation.split_hands(q)
    assert sorted(n[2] for n in left) == [36, 43, 48]
    assert sorted(n[2] for n in right) == [64, 67, 72]


def test_writes_musicxml_in_each_meter(tmp_path):
    beats = grid(40)
    notes = [[0, 0.5, 48, 80], [0, 0.4, 64, 80], [0.5, 0.66, 67, 80], [0.66, 0.83, 69, 80], [0.83, 1.0, 71, 80], [1.0, 3.1, 72, 80]]
    for meter, sig in [(Meter(4, False), "4"), (Meter(3, False), "3"), (Meter(2, True), "6")]:
        path = tmp_path / f"{meter.beats_per_bar}{meter.compound}.musicxml"
        notation.write_musicxml(notes, beats, meter, 100, "Test", "Piano", path, grand_staff=True)
        xml = path.read_text()
        assert f"<beats>{sig}</beats>" in xml
        assert "<staves>2</staves>" in xml or 'number="2"' in xml


def test_swing_is_written_as_straight_eighths():
    beats = grid(32)
    # Long-short pairs: on the beat and at 2/3 of it, the way swing is played.
    notes = [[b * 0.5 + off, b * 0.5 + off + 0.1, 60 + b % 5, 80] for b in range(16) for off in (0, 0.5 * 0.66)]
    meter = notation.detect_meter(notes, beats, 4)
    assert meter.swing and not meter.compound
    q = notation.quantise(notes, beats, meter)
    assert [on for on, _, _ in q[:4]] == [0, Fraction(1, 2), 1, Fraction(3, 2)]


def test_real_triplets_survive_in_swing():
    beats = grid(32)
    notes = [[b * 0.5 + off, b * 0.5 + off + 0.1, 60, 80] for b in range(16) for off in (0, 0.5 * 0.66)]
    notes += [[8.0 + i / 6, 8.0 + i / 6 + 0.1, 64, 80] for i in range(3)]  # beat 16 in triplets
    meter = notation.detect_meter(notes, beats, 4)
    q = notation.quantise(sorted(notes), beats, meter)
    assert Fraction(16) + Fraction(1, 3) in [on for on, _, _ in q]


def test_ballad_slowing_down_gets_no_invented_beats():
    from notewright.transcribe import _fill_gaps
    import numpy as np

    iv = np.array([0.7] * 30 + [1.3] * 12)  # an out-chorus taken much slower
    beats = np.concatenate([[0], np.cumsum(iv)])
    assert len(_fill_gaps(beats)) == len(beats)
    gapped = np.delete(beats, 20)  # one beat really missing
    assert len(_fill_gaps(gapped)) == len(beats)


def test_chord_symbols_are_written(tmp_path):
    beats = grid(40)
    notes = [[0, 1.0, 60, 80], [0, 1.0, 64, 80], [1.0, 2.0, 62, 80], [1.0, 2.0, 65, 80]]
    path = tmp_path / "c.musicxml"
    notation.write_musicxml(notes, beats, Meter(4, False), 100, "T", "Piano", path, grand_staff=True,
                            chords=[[0.0, 0.5, "C"], [0.5, 1.0, "Dm7"], [1.0, 1.5, "B♭maj7"], [1.5, 2.0, "Bm7♭5"]])
    xml = path.read_text()
    assert xml.count("<harmony") == 4
    assert "<root-alter>-1</root-alter>" in xml and "half-diminished" in xml
