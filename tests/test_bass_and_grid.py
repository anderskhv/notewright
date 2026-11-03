import numpy as np

from notewright import transcribe, yourmt3


def test_snap_moves_starts_to_the_nearest_attack_within_the_window():
    notes = [[1.030, 1.5, 40, 80], [2.000, 2.4, 43, 80], [3.200, 3.6, 45, 80]]
    attacks = np.array([1.0, 1.98, 3.0])
    out = transcribe.snap_to_attacks(notes, attacks, window=0.035)
    assert [n[0] for n in out] == [1.0, 1.98, 3.2]   # the last attack is 200 ms away: left alone
    assert [n[1:] for n in out] == [n[1:] for n in notes]


def test_yourmt3_bass_notes_and_kind():
    notes = [[0.0, 0.5, 40, 80, 32, False]] * 50 + [[0.0, 0.5, 60, 80, 0, False], [0.0, 0.1, 36, 90, 128, True]]
    assert len(yourmt3.bass_notes(notes)) == 50
    assert yourmt3.bass_kind(notes) == "double"
    electric = [[0.0, 0.5, 40, 80, 33, False]] * 50
    assert yourmt3.bass_kind(electric) == "electric"
    assert yourmt3.bass_kind(notes[:10]) is None  # too few to tell


def test_half_and_double_time_passages_are_counted_at_the_songs_tempo():
    beat = 0.32  # 188 bpm
    steady = list(np.arange(0, 40) * beat)
    half = list(steady[-1] + np.arange(1, 17) * 2 * beat)            # tracker counting at 94 bpm
    after_half = list(half[-1] + np.arange(1, 41) * beat)
    double = list(after_half[-1] + np.arange(1, 33) * beat / 2)       # tracker counting eighth notes
    tail = list(double[-1] + np.arange(1, 41) * beat)
    fixed = transcribe._fix_tempo_octave(np.array(steady + half + after_half + double + tail))
    iv = np.diff(fixed)
    assert np.all(np.abs(iv / beat - 1) < 0.05)


def test_a_missing_beat_moves_only_one_bar():
    beat, per_bar = 0.5, 4
    beats = list(np.arange(0, 41) * beat)
    del beats[22]                                   # the tracker skipped a beat in bar 6
    beats = np.array(beats)
    downbeats = np.arange(0, 21, 2.0)               # true downbeats every 2 s
    fixed, first = transcribe._anchor_bars(beats, downbeats, per_bar)
    bars = fixed[first::per_bar]
    # Every true downbeat lies on a bar line again.
    assert all(np.min(np.abs(bars - d)) < 0.01 for d in downbeats if d <= fixed[-1])


def test_a_lone_wrong_downbeat_is_ignored():
    beat, per_bar = 0.5, 4
    beats = np.arange(0, 41) * beat
    downbeats = np.array([0, 2, 4, 6.5, 8, 10, 12, 14, 16])   # 6.5 is the tracker's mistake
    fixed, first = transcribe._anchor_bars(beats, downbeats, per_bar)
    assert np.allclose(fixed, beats)


def test_combined_bass_line_prefers_yourmt3_and_fills_its_gaps():
    from notewright.pipeline import combine_bass

    heard = [[1.00, 1.30, 40, 1], [2.00, 2.30, 43, 1]]
    tracked = [[1.02, 1.35, 28, 70],              # the same note: YourMT3+ has it
               [1.50, 1.80, 29, 70],              # missed by YourMT3+, on a pluck, octave off: its low note's pitch
               [1.70, 1.75, 50, 70],              # a flicker: dropped
               [2.20, 2.60, 45, 70],              # on a pluck: kept; overlap trimmed to one note at a time
               [2.90, 3.20, 47, 70]]              # no pluck there: dropped
    plucks = np.array([1.0, 1.5, 1.7, 2.0, 2.2])
    low = [[1.51, 1.8, 41, 1, 0, False]]          # YourMT3+ filed this one under piano
    line = combine_bass(heard, tracked, plucks, low)
    assert [n[:3] for n in line] == [[1.00, 1.30, 40], [1.50, 1.80, 41], [2.00, 2.20, 43], [2.20, 2.60, 45]]


def test_loudness_comes_from_the_audio_for_every_note():
    sr = 22050
    t = np.arange(sr * 3) / sr
    y = np.where(t < 1.5, 0.05, 0.5) * np.sin(2 * np.pi * 110 * t)   # quiet, then loud
    notes = [[0.5, 0.9, 45, 1], [2.0, 2.4, 45, 1]]
    soft, loud = transcribe.loudness(notes, y.astype(np.float32), sr)
    assert soft[3] < loud[3] and loud[3] > 100 and soft[3] > 40
