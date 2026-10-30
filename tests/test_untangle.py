"""Separating piano from the horns with the notes (no models needed)."""

import numpy as np

from notewright import untangle

SR = 22050


def tone(midi, dur, harmonics, decay=0.0):
    f = 440 * 2 ** ((midi - 69) / 12)
    t = np.arange(int(dur * SR)) / SR
    return sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(harmonics)) * np.exp(-decay * t)


def test_octave_slip_is_moved_up():
    # A note with only even harmonics of C4 is really C5.
    y = tone(72, 1.0, [1, 0.6, 0.4, 0.3])[None, :]
    fixed = untangle.fix_octaves([[0.0, 1.0, 60, 80]], y, SR)
    assert fixed[0][2] == 72
    # A real C4 stays.
    y = tone(60, 1.0, [1, 0.6, 0.4, 0.3])[None, :]
    assert untangle.fix_octaves([[0.0, 1.0, 60, 80]], y, SR)[0][2] == 60


def test_struck_decaying_note_is_piano_held_note_is_horn():
    y = np.concatenate([tone(64, 1.0, [1, 0.5, 0.3], decay=4.0), tone(64, 1.0, [1, 0.5, 0.3])])[None, :]
    notes = [[0.0, 1.0, 64, 80], [1.0, 2.0, 64, 80]]
    onsets = np.array([[0.0, 64]])
    assert untangle.piano_like(notes, y, SR, onsets) == [True, False]


def test_piano_doubling_a_horn_is_the_horn():
    piano = [[0.0, 1.0, 64, 80], [0.0, 1.0, 60, 80]]
    horns = [[0.05, 1.2, 64, 90]]
    assert untangle.drop_doubled(piano, horns) == [[0.0, 1.0, 60, 80]]


def test_isolated_blips_are_dropped_phrases_kept():
    y = np.zeros((1, SR * 6))
    line = [[0.5, 0.8, 67, 80], [0.8, 1.1, 69, 80], [1.1, 1.3, 71, 80], [4.0, 4.15, 50, 60]]
    cleaned = untangle.clean_line(line, y, SR, np.zeros((0, 2)))
    assert [n[2] for n in cleaned] == [67, 69, 71]


def test_separation_moves_piano_out_and_keeps_the_horn():
    horn = tone(70, 2.0, [1, 0.7, 0.5, 0.4, 0.3])
    piano = tone(55, 2.0, [1, 0.6, 0.4, 0.3], decay=1.5)
    mix = (horn + piano)[None, :]
    p_out, h_out = untangle.separate(np.zeros_like(mix), mix, SR, [[0.0, 2.0, 55, 80]], [[0.0, 2.0, 70, 90]])
    resid_piano = np.corrcoef(h_out[0], piano)[0, 1]
    assert resid_piano < 0.3  # little piano left in the horn part
    assert np.corrcoef(h_out[0], horn)[0, 1] > 0.9  # the horn is intact
    assert np.allclose(p_out + h_out, mix, atol=1e-3)  # nothing lost: the parts still add up
