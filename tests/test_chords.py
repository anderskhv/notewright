from notewright import chords

BEATS = [i * 0.5 for i in range(17)]


def bar(*pitches, bass=None, start=0.0, end=1.0):
    tracks = [{"id": "piano", "notes": [[start, end, p, 70] for p in pitches]}]
    if bass is not None:
        tracks.append({"id": "bass", "notes": [[start, end, bass, 80]]})
    return tracks


def names(tracks):
    return [c[2] for c in chords.detect(tracks, BEATS, 4)]


def test_triads_and_sevenths():
    assert names(bar(60, 64, 67)) == ["C"]
    assert names(bar(62, 65, 69, 72, bass=38)) == ["Dm7"]
    assert names(bar(55, 59, 62, 65, bass=43)) == ["G7"]
    assert names(bar(59, 62, 65, 69, bass=47)) == ["Bm7♭5"]
    assert names(bar(65, 69, 72, 76)) == ["Fmaj7"]


def test_rootless_voicing_takes_the_root_from_the_bass():
    # F A C E over a D in the bass: Dm9 without its root, named by its family
    assert names(bar(65, 69, 72, 76, bass=38)) == ["Dm7"]


def test_changes_are_merged_and_silence_skipped():
    tracks = bar(60, 64, 67, start=0.0, end=2.0) + bar(55, 59, 62, 65, start=2.0, end=3.0)
    tracks = [{"id": "piano", "notes": tracks[0]["notes"] + tracks[1]["notes"]}]
    out = chords.detect(tracks, BEATS, 4)
    assert [c[2] for c in out] == ["C", "G7"]
    assert out[0][:2] == [0.0, 2.0]
