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
    for f in files:
        ext = f.suffix.lower()
        if ext == ".pdf":
            items += [("picture", p) for p in _pdf_pages(f, work)]
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
    notes = []
    for part in score.parts:
        for n in part.stripTies().flatten().notes:
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
        "title": re.sub(r"^\d+[.)]?\s+", "", title.strip()),  # "1 Flat Foot Floogie": drop the exercise number
        "staves": len(score.parts),
    }
