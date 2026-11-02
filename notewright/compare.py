"""Compare two transcriptions of the same song, note by note.

    python -m notewright.compare "Old Folks"

takes the two most recent finished songs whose title contains the text and prints, per
part, how many notes agree (same pitch, starting within 50 ms), plus how long each took.
Used to check that a faster setting (such as the Apple GPU) doesn't change the notes.
"""

import json
import sys

from .pipeline import jobs_dir, list_jobs

TOLERANCE = 0.05  # seconds


def agreement(a: list, b: list) -> tuple[int, int, int]:
    """Matched notes, notes only in a, notes only in b."""
    by_pitch: dict[int, list[float]] = {}
    for start, _, pitch, *_ in b:
        by_pitch.setdefault(pitch, []).append(start)
    matched = 0
    for start, _, pitch, *_ in a:
        starts = by_pitch.get(pitch, [])
        best = min(range(len(starts)), key=lambda i: abs(starts[i] - start), default=None)
        if best is not None and abs(starts[best] - start) <= TOLERANCE:
            starts.pop(best)
            matched += 1
    return matched, len(a) - matched, len(b) - matched


def main(text: str) -> None:
    found = [j for j in list_jobs() if text.lower() in j["title"].lower() and j["status"] == "done"][:2]
    if len(found) < 2:
        sys.exit(f'Need two finished songs with "{text}" in the title; found {len(found)}.')
    new, old = found
    results = [json.loads((jobs_dir() / j["id"] / "result.json").read_text()) for j in (old, new)]
    print(f'Older: {old["title"]} ({old["id"]})\nNewer: {new["title"]} ({new["id"]})\n')
    tracks_old = {t["id"]: t["notes"] for t in results[0]["tracks"]}
    tracks_new = {t["id"]: t["notes"] for t in results[1]["tracks"]}
    print(f"{'Part':12s} {'older':>6s} {'newer':>6s} {'agree':>7s}")
    for tid in sorted(set(tracks_old) | set(tracks_new)):
        a, b = tracks_old.get(tid, []), tracks_new.get(tid, [])
        matched, _, _ = agreement(a, b)
        share = 2 * matched / (len(a) + len(b)) if a or b else 1.0
        print(f"{tid:12s} {len(a):6d} {len(b):6d} {share:7.1%}")
    for label, j in (("Older", old), ("Newer", new)):
        if j.get("timings"):
            total = sum(s for _, s in j["timings"])
            print(f"\n{label} took {total:.0f} s: " + " · ".join(f"{n} {s:.0f} s" for n, s in j["timings"]))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
