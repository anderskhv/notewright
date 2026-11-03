"""Sheet music in: pictures, PDF pages, MusicXML or MIDI.

Pictures (a screenshot, a snip pasted from a web page, a photo of printed music) are
read by homr, an open-source optical music recognition program (AGPL-3.0, run as a
separate program). Everything ends up as MusicXML, an open format MuseScore and every
notation program can open. Several pictures pasted in one go become one piece, in order.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
NOTATION = {".musicxml", ".xml", ".mxl", ".mid", ".midi"}
DEFAULT_BPM = 100.0


def _homr() -> str:
    beside = Path(sys.executable).with_name("homr")
    found = str(beside) if beside.exists() else shutil.which("homr")
    if not found:
        raise RuntimeError("Reading sheet music needs homr: run  .venv/bin/pip install -r requirements.txt")
    return found


def _as_png(src: Path, dst: Path) -> Path:
    """Any picture to PNG, enlarged when small: homr reads ~2000-pixel-wide pages best."""
    from PIL import Image, ImageOps

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if im.width < 1800:
            factor = 1800 / im.width
            im = im.resize((round(im.width * factor), round(im.height * factor)), Image.LANCZOS)
        im.save(dst)
    return dst


def _pdf_pages(src: Path, work: Path) -> list[Path]:
    import pypdfium2 as pdfium

    pages = []
    pdf = pdfium.PdfDocument(str(src))
    for i in range(len(pdf)):
        out = work / f"{src.stem}-page{i + 1}.png"
        pdf[i].render(scale=3).to_pil().convert("RGB").save(out)  # about 216 dpi
        pages.append(out)
    return pages


def recognise(files: list[Path], work: Path, progress) -> list[Path]:
    """MusicXML (or MIDI) files, one per picture or page, in the order given."""
    work.mkdir(parents=True, exist_ok=True)
    items: list[tuple[str, Path]] = []
    from_pdf: dict[Path, tuple[Path, int]] = {}  # page picture -> (its PDF, page number)
    for f in files:
        ext = f.suffix.lower()
        if ext == ".pdf":
            pages = _pdf_pages(f, work)
            items += [("picture", p) for p in pages]
            from_pdf.update({p: (f, i) for i, p in enumerate(pages)})
        elif ext in IMAGES:
            items.append(("picture", _as_png(f, work / f"{f.stem}.png")))
        elif ext in NOTATION:
            items.append(("notation", f))
        else:
            raise RuntimeError(f"{f.name}: not a picture, PDF, MusicXML or MIDI file")
    pictures = sum(kind == "picture" for kind, _ in items)
    out, done = [], 0
    for kind, path in items:
        if kind == "notation":
            out.append(path)
            continue
        progress(done / pictures)
        run = subprocess.run([_homr(), path.name], cwd=work, capture_output=True, text=True)
        xml = path.with_suffix(".musicxml")
        if run.returncode or not xml.exists():
            last = (run.stderr or run.stdout).strip().splitlines()[-1:] or ["no music found"]
            raise RuntimeError(f"Could not read the notes in picture {done + 1}: {last[0]}")
        if path in from_pdf:
            # The note reader skips text: a lead sheet's chord symbols come from the PDF itself.
            from . import leadsheet

            try:
                pdf, page = from_pdf[path]
                title = leadsheet.pdf_title(pdf) if page == 0 else None
                leadsheet.add_chords(xml, leadsheet.chords_on_page(pdf, page), title)
            except Exception as err:  # noqa: BLE001 - chords are extra; never lose the notes over them
                print(f"Notewright: could not read chord symbols on {path.name}: {err}", flush=True)
        out.append(xml)
        done += 1
    progress(1.0)
    return out


def combine(paths: list[Path]):
    """One music21 score: the pieces one after the other, staff by staff."""
    from music21 import converter, note, stream

    scores = [converter.parse(str(p)) for p in paths]
    if len(scores) == 1:
        return scores[0]
    staves = max(len(s.parts) for s in scores)
    out = stream.Score()
    parts = [stream.Part() for _ in range(staves)]
    for s in scores:
        length = s.highestTime
        for i in range(staves):
            if i < len(s.parts):
                for m in s.parts[i].getElementsByClass("Measure"):
                    parts[i].append(m)
            else:  # a single-line snip after a two-staff one: rest in the missing staff
                parts[i].append(note.Rest(quarterLength=length))
    for p in parts:
        out.insert(0, p)
    if scores[0].metadata:
        out.metadata = scores[0].metadata
    return out


def read(score) -> dict:
    """Notes to play, the beat grid, and the facts the player shows."""
    from music21 import meter, tempo

    try:
        score = score.expandRepeats()
    except Exception:  # noqa: BLE001 - unbalanced repeat signs from recognition: play as written
        pass
    marks = list(score.recurse().getElementsByClass(tempo.MetronomeMark))
    bpm = float(marks[0].getQuarterBPM()) if marks and marks[0].getQuarterBPM() else DEFAULT_BPM
    spq = 60.0 / bpm  # seconds per quarter note
    sigs = list(score.recurse().getElementsByClass(meter.TimeSignature))
    ts = sigs[0] if sigs else meter.TimeSignature("4/4")
    from music21 import harmony

    notes, symbols = [], []
    for part in score.parts:
        flat = part.stripTies().flatten()
        for cs in flat.getElementsByClass(harmony.ChordSymbol):
            symbols.append((float(cs.offset) * spq, cs))
        for n in flat.notes:
            if isinstance(n, harmony.ChordSymbol):
                continue
            start, end = float(n.offset) * spq, float(n.offset + n.quarterLength) * spq
            for p in n.pitches:
                notes.append([round(start, 4), round(max(end, start + 0.05), 4), int(p.midi), 80])
    notes.sort()
    length = float(score.highestTime)
    beats = [round(i * spq, 4) for i in range(int(length) + 8)]
    title = (score.metadata.title if score.metadata and score.metadata.title else "") or ""
    return {
        "score": score,
        "notes": notes,
        "bpm": bpm,
        "beats": beats,
        "duration": round(length * spq + 1.0, 3),
        "timeSignature": ts.ratioString,
        "beatsPerBar": max(1, int(round(ts.barDuration.quarterLength))),
        "title": _title(title),
        "staves": len(score.parts),
        "symbols": sorted(symbols, key=lambda x: x[0]),
        "spq": spq,
    }


def display_name(cs) -> str:
    """music21's "E-maj7/B-" as a reader writes it: "E♭maj7/B♭"."""
    fig = cs.figure
    fig = re.sub(r"^([A-G])-", r"\1♭", fig)
    fig = re.sub(r"^([A-G])#", r"\1♯", fig)
    fig = re.sub(r"/([A-G])-", r"/\1♭", fig)
    return re.sub(r"o(?=\d|$|/)", "dim", fig)        # music21 writes a diminished chord with an o


def chord_part(symbols: list, duration: float, bar_seconds: float) -> tuple[list, list]:
    """The chord symbols as [start, end, name], and a simple piano part that plays them.

    Voicing: the root (or the slash bass) low, the other chord tones in the middle of the
    keyboard; struck when the chord starts and again at every new bar while it lasts.
    """
    names, notes = [], []
    for i, (start, cs) in enumerate(symbols):
        end = symbols[i + 1][0] if i + 1 < len(symbols) else duration
        if end <= start:
            continue
        names.append([round(start, 3), round(end, 3), display_name(cs)])
        try:
            bass = (cs.bass() or cs.root()).pitchClass
            tones = [p.pitchClass for p in cs.pitches if p.pitchClass != cs.root().pitchClass]
        except Exception:  # noqa: BLE001
            continue
        low = 36 + (bass - 36) % 12                       # C2..B2
        mid = sorted({52 + (pc - 52) % 12 for pc in tones})[:4]   # E3..D#4
        t = start
        while t < end - 0.05:
            nxt = min(end, t + bar_seconds)
            for m in [low] + mid:
                notes.append([round(t, 4), round(nxt, 4), m, 62 if m == low else 54])
            t = nxt
    return names, sorted(notes)


def _title(title: str) -> str:
    """The piece's title as read from the page, or "" when it is not one.

    "1 Flat Foot Floogie": the exercise number goes. A chord symbol is not a title (the note
    reader takes the first text it sees, which on a lead sheet can be the first chord).
    """
    from .leadsheet import _CHORD

    title = re.sub(r"^\d+[.)]?\s+", "", title.strip())
    return "" if _CHORD.match(title.replace("-", "♭")) or _CHORD.match(title) else title
