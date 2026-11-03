from music21 import harmony

from notewright import leadsheet, score


def test_chord_symbols_are_recognised():
    for name in ["E♭maj7", "D♭7", "C7", "Fm", "Adim7", "Gm7/B♭", "E♭6", "Bø7", "C7(♭9)", "F#m7"]:
        assert leadsheet._CHORD.match(name), name
    for text in ["Pno.", "Ernie Burnett", "My Melancholy Baby", "14", "Swing"]:
        assert not leadsheet._CHORD.match(text), text


def test_music21_spelling_and_back():
    for name, back in [("E♭maj7", "E♭maj7"), ("Gm7/B♭", "Gm7/B♭"), ("Adim7", "Adim7"), ("B♭7", "B♭7")]:
        assert score.display_name(harmony.ChordSymbol(leadsheet.music21_figure(name))) == back


def test_a_chord_name_is_not_a_title():
    assert score._title("Ebmaj7") == ""
    assert score._title("1 Flat Foot Floogie") == "Flat Foot Floogie"


def test_chord_part_voices_root_low_and_restrikes_each_bar():
    cs = harmony.ChordSymbol("E-maj7")
    names, notes = score.chord_part([(0.0, cs)], duration=4.8, bar_seconds=2.4)
    assert names == [[0.0, 4.8, "E♭maj7"]]
    first_bar = [n for n in notes if n[0] == 0.0]
    assert min(n[2] for n in first_bar) == 39               # E♭2 in the bass
    assert sorted(n[2] % 12 for n in first_bar if n[2] > 48) == [2, 7, 10]   # G, B♭, D in the middle
    assert {n[0] for n in notes} == {0.0, 2.4}              # struck again in the second bar


def test_diminished_after_a_round_trip_reads_dim():
    cs = harmony.ChordSymbol("Ao7")    # how music21 reads <kind>diminished-seventh</kind> back
    assert score.display_name(cs) == "Adim7"
