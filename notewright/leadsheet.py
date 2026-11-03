"""Chord symbols from lead sheets.

The note reader (homr) reads the notes and ignores text, so a lead sheet came in as a bare
melody. A PDF exported from notation software (MuseScore, Dorico, Sibelius, iReal Pro...)
keeps its chord symbols as text, with their place on the page; this module reads them,
finds the staves and bar lines in the page image, and puts each chord in its bar, on the
note it stands above.

Bars are matched line by line (homr marks where each line of music starts), so a bar the
note reader missed moves the chords of that line only.
"""

import re
from pathlib import Path

import numpy as np

# A chord symbol: root, optional accidental, quality and extensions, optional slash bass.
_ACC = "(?:♭|♯|b|#)"
_CHORD = re.compile(rf"^[A-G]{_ACC}?(?:maj|ma|min|mi|m|dim|aug|sus|add|alt|ø|°|o|\+|-|M|Δ|\d|\(|\)|,|{_ACC})*"
                    rf"(?:/[A-G]{_ACC}?)?$")
NOTEHEADS = range(0xE0A0, 0xE100)  # SMuFL noteheads
RESTS = range(0xE4E0, 0xE500)      # SMuFL rests
SCALE = 3                          # page image pixels per PDF point (the note reader's resolution too)


def _staves(gray: np.ndarray) -> list[tuple[float, float]]:
    """(top, bottom) of every five-line staff, in pixels."""
    dark = gray < 140
    width = dark.shape[1]
    rows = np.where(dark.sum(axis=1) > 0.45 * width)[0]
    lines, run = [], []
    for r in rows:
        if run and r - run[-1] > 1:
            lines.append(float(np.mean(run)))
            run = []
        run.append(r)
    if run:
        lines.append(float(np.mean(run)))
    staves, i = [], 0
    while i + 4 < len(lines):
        gaps = np.diff(lines[i:i + 5])
        if gaps.max() < 1.3 * gaps.min() and gaps.min() > 3:
            staves.append((lines[i], lines[i + 4]))
            i += 5
        else:
            i += 1
    return staves


def _barlines(gray: np.ndarray, top: float, bottom: float) -> list[float]:
    """x of each bar line of one staff: thin lines from the top staff line to the bottom one
    that, unlike note stems, stop there."""
    dark = gray < 140
    a, b = int(round(top)) - 1, int(round(bottom)) + 2
    space = (bottom - top) / 4
    full = dark[a:b].mean(axis=0) >= 0.96
    above = dark[max(0, a - int(space)):a - 2].mean(axis=0) if a - 2 > 0 else np.zeros(dark.shape[1])
    below = dark[b + 2:b + int(space)].mean(axis=0)
    cols = np.where(full & (above < 0.3) & (below < 0.3))[0]
    groups, run = [], []
    for c in cols:
        if run and c - run[-1] > 1:
            groups.append(run)
            run = []
        run.append(c)
    if run:
        groups.append(run)
    xs = [float(np.mean(g)) for g in groups if len(g) <= 14]
    merged = []
    for x in xs:  # a double or final bar line is one bar line
        if merged and x - merged[-1] < 3 * space:
            merged[-1] = x
        else:
            merged.append(x)
    return merged


def _tokens(page, height_pt: float) -> list[dict]:
    """Words on the page with their boxes in page-image pixels, in reading order of the file."""
    tp = page.get_textpage()
    out, cur = [], None
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if not ch or ch.isspace():
            cur = None
            continue
        left, bottom, right, top = tp.get_charbox(i)
        box = [left * SCALE, (height_pt - top) * SCALE, right * SCALE, (height_pt - bottom) * SCALE]
        if cur and box[0] - cur["box"][2] < 4 * SCALE and abs((box[1] + box[3]) - (cur["box"][1] + cur["box"][3])) < 24 * SCALE:
            cur["text"] += ch
            cur["box"] = [min(cur["box"][0], box[0]), min(cur["box"][1], box[1]), max(cur["box"][2], box[2]), max(cur["box"][3], box[3])]
        else:
            cur = {"text": ch, "box": box}
            out.append(cur)
    return out


def chords_on_page(pdf_path: Path, index: int) -> list[dict]:
    """Chord symbols on one PDF page: {"name", "system", "bar", "event", "events", "frac"}.

    system and bar count from 0 (bar within its line of music); event is the note or rest the
    chord stands above, out of events in that bar; frac is its place across the bar.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[index]
    _, height_pt = page.get_size()
    gray = np.asarray(page.render(scale=SCALE).to_pil().convert("L"), dtype=np.uint8)
    staves = _staves(gray)
    if not staves:
        return []
    bars = [_barlines(gray, top, bottom) for top, bottom in staves]
    tokens = _tokens(page, height_pt)
    # Glyphs of noteheads and rests (exported PDFs draw music with a music font), as events.
    glyphs = [t for t in tokens if any(ord(c) in NOTEHEADS or ord(c) in RESTS for c in t["text"])]
    words = [t for t in tokens if not any(0xE000 <= ord(c) <= 0xF8FF for c in t["text"])]
    # A slash bass written apart ("Gm7" then "/B♭") joins its chord.
    joined = []
    for t in words:
        if joined and t["text"].startswith("/") and t["box"][0] - joined[-1]["box"][2] < 8 * SCALE:
            joined[-1] = {"text": joined[-1]["text"] + t["text"],
                          "box": [joined[-1]["box"][0], min(joined[-1]["box"][1], t["box"][1]), t["box"][2], max(joined[-1]["box"][3], t["box"][3])]}
        else:
            joined.append(dict(t))
    found = []
    for t in joined:
        name = t["text"].strip()
        if not _CHORD.match(name):
            continue
        x, y = t["box"][0], t["box"][3]
        below = [(top - y, s) for s, (top, bottom) in enumerate(staves) if top > y - 2]
        if not below:
            continue
        gap, s = min(below)
        top, bottom = staves[s]
        space = (bottom - top) / 4
        if gap > 8 * space or len(bars[s]) < 2:
            continue
        lines = bars[s]
        j = int(np.searchsorted(lines, x) - 1)
        if not 0 <= j < len(lines) - 1:
            continue
        a, b = lines[j], lines[j + 1]
        ev = sorted({round((g["box"][0] + g["box"][2]) / 2) for g in glyphs
                     if a < (g["box"][0] + g["box"][2]) / 2 < b and top - 3 * space < (g["box"][1] + g["box"][3]) / 2 < bottom + 3 * space})
        dedup = []
        for e in ev:  # notes of one chord, or a notehead and its dot, share a place
            if not dedup or e - dedup[-1] > space * 0.8:
                dedup.append(e)
        event = int(np.argmin([abs(e - x) for e in dedup])) if dedup else 0
        found.append({"name": name, "system": s, "bar": j, "event": event, "events": len(dedup), "frac": (x - a) / (b - a)})
    return found


def music21_figure(name: str) -> str:
    """"E♭maj7" -> "E-maj7", the spelling music21's ChordSymbol reads."""
    root = re.sub(r"^([A-G])(♭|b)", r"\1-", name)
    root = re.sub(r"^([A-G])(♯|#)", r"\1#", root)
    root = root.replace("♭", "b").replace("♯", "#")
    return re.sub(r"/([A-G])(♭|b)", r"/\1-", root)


def pdf_title(pdf_path: Path) -> str | None:
    """The first line of text on the first page that is not a chord, a number or a part name."""
    import pypdfium2 as pdfium

    text = pdfium.PdfDocument(str(pdf_path))[0].get_textpage().get_text_range()
    for line in text.splitlines():
        line = "".join(c for c in line if not 0xE000 <= ord(c) <= 0xF8FF).strip()
        if len(line) >= 3 and not _CHORD.match(line) and not line.isdigit() and not line.endswith("."):
            return line
    return None


def add_chords(xml: Path, chords: list[dict], title: str | None = None) -> int:
    """Write chord symbols (and the title) into the note reader's MusicXML for that page."""
    from music21 import converter, harmony, layout, metadata

    if not chords and not title:
        return 0
    score = converter.parse(str(xml))
    if title:
        score.metadata = score.metadata or metadata.Metadata()
        score.metadata.title = title
    part = score.parts[0]
    systems: list[list] = []
    for m in part.getElementsByClass("Measure"):
        if not systems or any(getattr(sl, "isNew", False) for sl in m.getElementsByClass(layout.SystemLayout)):
            systems.append([])
        systems[-1].append(m)
    placed = 0
    for c in chords:
        if c["system"] >= len(systems) or c["bar"] >= len(systems[c["system"]]):
            continue
        m = systems[c["system"]][c["bar"]]
        events = [n for n in m.notesAndRests if not n.duration.isGrace]
        bar = m.barDuration.quarterLength
        if len(events) == c["events"] and events:
            offset = float(events[c["event"]].offset)
        else:  # the note reader saw the bar differently: go by the place across the bar
            offset = round(c["frac"] * bar / (bar / 2)) * (bar / 2) if c["frac"] > 0.35 else 0.0
            offset = min(offset, bar - 1)
        try:
            cs = harmony.ChordSymbol(music21_figure(c["name"]))
        except Exception:  # noqa: BLE001 - a symbol music21 cannot read: leave it out
            continue
        cs.writeAsChord = False
        m.insert(offset, cs)
        placed += 1
    score.write("musicxml", fp=str(xml))
    return placed
