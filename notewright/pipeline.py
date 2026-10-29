"""The job pipeline: fetch, separate, transcribe, notate. One job runs at a time."""

import json
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio, notation, transcribe
from .config import DATA_DIR, device

# id, display name, transcriber, sampler voice in the browser
TRACKS = [
    ("piano", "Piano", "poly", "piano"),
    ("vocals", "Vocals (melody)", "mono", "voice_oohs"),
    ("guitar", "Guitar", "poly", "acoustic_guitar_steel"),
    ("bass", "Bass", "mono", "electric_bass_finger"),
    ("drums", "Drums", "drums", "drums"),
    ("other", "Other (keys, strings, synths)", "poly", "string_ensemble_1"),
]
MONO_RANGE = {"bass": (30.0, 400.0), "vocals": (80.0, 1100.0)}
# Default note sensitivity per polyphonic track (0.5 is the piano model's own default).
SENSITIVITY = {"piano": 0.5, "guitar": 0.4, "other": 0.25}
SILENT_STEM = 0.06  # stems quieter than this fraction of the mix (RMS) are skipped


def jobs_dir() -> Path:
    d = DATA_DIR / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_job(job_id: str) -> dict | None:
    path = jobs_dir() / job_id / "job.json"
    return json.loads(path.read_text()) if path.exists() else None


def list_jobs() -> list[dict]:
    jobs = [read_job(p.name) for p in jobs_dir().iterdir() if p.is_dir()]
    return sorted((j for j in jobs if j), key=lambda j: j["created"], reverse=True)


def delete_job(job_id: str) -> None:
    d = jobs_dir() / job_id
    if d.is_dir() and d.parent == jobs_dir():
        shutil.rmtree(d)


class Job:
    def __init__(self, record: dict):
        self.record = record
        self.dir = jobs_dir() / record["id"]

    def save(self, **changes) -> None:
        self.record.update(changes)
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps(self.record))
        tmp.replace(self.dir / "job.json")

    def stage(self, name: str, start: float, end: float):
        """Progress callback mapping a stage's 0..1 onto the job's start..end."""
        self.save(stage=name, progress=round(start, 3))
        last = [0.0]

        def report(fraction: float) -> None:
            if fraction - last[0] >= 0.02 or fraction >= 1.0:
                last[0] = fraction
                self.save(progress=round(start + (end - start) * fraction, 3))

        return report


_queue: "queue.Queue[str]" = queue.Queue()


def submit(mode: str, url: str | None = None, upload: tuple[str, bytes] | None = None) -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = jobs_dir() / job_id
    d.mkdir()
    title = url or (upload[0] if upload else "Untitled")
    if upload:
        (d / ("upload" + Path(upload[0]).suffix.lower())).write_bytes(upload[1])
    record = {
        "id": job_id,
        "title": title,
        "source": url,
        "mode": mode,
        "status": "queued",
        "stage": "Waiting",
        "progress": 0.0,
        "error": None,
        "created": time.time(),
    }
    Job(record).save()
    _queue.put(job_id)
    return record


def resume_pending() -> None:
    """Re-queue jobs that were interrupted by a restart."""
    for j in reversed(list_jobs()):
        if j["status"] in ("queued", "running"):
            _queue.put(j["id"])


def worker() -> None:
    upgrade_old_jobs()
    while True:
        job_id = _queue.get()
        record = read_job(job_id)
        if not record:
            continue
        job = Job(record)
        try:
            job.save(status="running", error=None)
            action = record.get("action") or {}
            if action.get("type") == "retune":
                retune(job, action["track"], action["sensitivity"])
            else:
                run(job)
            job.save(status="done", stage="Done", progress=1.0, action=None)
        except Exception as exc:  # noqa: BLE001 - any failure is reported to the UI
            traceback.print_exc()
            error = f"{type(exc).__name__}: {exc}"
            if (job.dir / "result.json").exists():
                # A later step (reading notes again, separating) failed: the song itself still plays.
                mode = job.record.get("mode_before") or job.record["mode"]
                job.save(status="done", stage="Done", progress=1.0, error=error, action=None, mode=mode)
            else:
                job.save(status="error", error=error, action=None)


def requeue(job_id: str, **changes) -> dict:
    record = read_job(job_id)
    if not record:
        raise KeyError(job_id)
    if record["status"] in ("queued", "running"):
        return record
    job = Job(record)
    job.save(status="queued", stage="Waiting", progress=0.0, error=None, mode_before=record["mode"], **changes)
    _queue.put(job_id)
    return job.record


def upgrade_old_jobs() -> None:
    """Jobs made before 0.2 have MP3 browser copies; re-encode them once (see audio.py)."""
    for rec in list_jobs():
        d = jobs_dir() / rec["id"]
        result_path = d / "result.json"
        if rec["status"] != "done" or not result_path.exists():
            continue
        result = json.loads(result_path.read_text())
        if not result.get("audio", "").endswith(".mp3"):
            continue
        try:
            for mp3 in [d / result["audio"]] + [d / t["stem"] for t in result["tracks"] if t.get("stem")]:
                m4a = mp3.with_suffix(audio.BROWSER_EXT)
                if mp3.exists() and not m4a.exists():
                    audio.to_browser(mp3, m4a)
            result["audio"] = "audio" + audio.BROWSER_EXT
            for t in result["tracks"]:
                if t.get("stem"):
                    t["stem"] = str(Path(t["stem"]).with_suffix(audio.BROWSER_EXT))
            result_path.write_text(json.dumps(result))
            for mp3 in d.glob("**/*.mp3"):
                mp3.unlink()
        except Exception:  # noqa: BLE001 - an old job that cannot be upgraded still plays as before
            traceback.print_exc()


def start_worker() -> None:
    threading.Thread(target=worker, daemon=True, name="notewright-worker").start()


def _rms(path: Path) -> float:
    y, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return float(np.sqrt(np.mean(y**2))) if y.size else 0.0


def run(job: Job) -> None:
    d, rec, dev = job.dir, job.record, device()
    band = rec["mode"] == "band"

    job.stage("Fetching audio", 0.0, 0.05)
    existing = d / f"audio{audio.BROWSER_EXT}"
    if existing.exists():
        src = existing  # a re-run, e.g. separating instruments later: no new download
    elif rec["source"]:
        src, title = audio.download(rec["source"], d)
        job.save(title=title)
    else:
        src = next(d.glob("upload.*"))
    wav = audio.normalise(src, d)
    duration = sf.info(str(wav)).duration

    job.stage("Finding the beat", 0.05, 0.08)
    bpm, beats, per_bar, compound = transcribe.beat_grid(wav, duration, dev)
    (d / "grid.json").write_text(json.dumps({"bpm": bpm, "beats": beats, "perBar": per_bar, "compound": compound}))

    tracks = []
    if band:
        from .separate import separate

        stems = separate(wav, d / "stems", dev, job.stage("Separating instruments", 0.08, 0.45))
        mix_rms = _rms(wav) or 1.0
        audio_stems = {}
        for name, path in stems.items():
            audio.to_browser(path, d / "stems" / f"{name}{audio.BROWSER_EXT}")
            audio_stems[name] = f"stems/{name}{audio.BROWSER_EXT}"
        active = [t for t in TRACKS if _rms(stems[t[0]]) / mix_rms >= SILENT_STEM]
        span = 0.5 / max(1, len(active))
        for i, (tid, name, kind, voice) in enumerate(active):
            report = job.stage(f"Transcribing {name.split(' ')[0].lower()}", 0.45 + i * span, 0.45 + (i + 1) * span)
            tracks.append(_transcribe(d, tid, name, kind, voice, stems[tid], dev, report))
        for tid, name, _, voice in TRACKS:
            if tid not in {t["id"] for t in tracks}:
                tracks.append({"id": tid, "name": name, "voice": voice, "notes": [], "silent": True})
        for t in tracks:
            t["stem"] = audio_stems.get(t["id"])
    else:
        report = job.stage("Transcribing piano", 0.08, 0.95)
        tracks.append(_transcribe(d, "piano", "Piano", "poly", "piano", wav, dev, report))

    job.stage("Writing sheet music", 0.95, 1.0)
    _write_result(job, tracks, duration)
    # The wav copies are only needed for analysis.
    wav.unlink(missing_ok=True)
    for f in list(d.glob("source.*")) + list(d.glob("upload.*")) + list((d / "stems").glob("*.wav")):
        f.unlink(missing_ok=True)


def retune(job: Job, track_id: str, sensitivity: float) -> None:
    """Read one polyphonic track's notes again at a new sensitivity, from the saved model output."""
    d = job.dir
    result = json.loads((d / "result.json").read_text())
    track = next(t for t in result["tracks"] if t["id"] == track_id)
    raw = d / f"model-{track_id}.npz"
    if not raw.exists():  # made before model output was kept: run the model once more
        source = d / track["stem"] if track.get("stem") else d / result["audio"]
        wav = d / "retune.wav"
        audio.ffmpeg("-i", str(source), "-ac", "1", "-ar", "16000", str(wav))  # librosa cannot read m4a
        try:
            output = transcribe.model_output(wav, device(), job.stage("Listening again", 0.0, 0.8))
        finally:
            wav.unlink(missing_ok=True)
        transcribe.save_output(output, raw)
    job.stage("Reading notes", 0.8, 0.95)
    track["notes"], track["pedals"] = transcribe.notes_from_output(transcribe.load_output(raw), sensitivity)
    track["sensitivity"] = sensitivity
    job.stage("Writing sheet music", 0.95, 1.0)
    _write_result(job, result["tracks"], result["duration"])


def _write_result(job: Job, tracks: list[dict], duration: float) -> None:
    d, rec = job.dir, job.record
    grid_path = d / "grid.json"
    if grid_path.exists():
        grid = json.loads(grid_path.read_text())
    else:  # made before 0.2: rebuild the grid from the (trimmed) one in the result
        old = json.loads((d / "result.json").read_text())
        grid = {"bpm": old["bpm"], "beats": old["beats"], "perBar": old["beatsPerBar"], "compound": old["beatQuarters"] > 1}
    bpm, beats = grid["bpm"], grid["beats"]

    pitched = [n for t in tracks if t["id"] != "drums" for n in t["notes"]]
    meter = notation.detect_meter(pitched, beats, grid["perBar"], grid["compound"])
    if pitched:
        # Start the score at the bar where the music starts, not with empty bars.
        first = notation.seconds_to_beats([min(n[0] for n in pitched)], beats)[0]
        # (+0.25: a note played a touch early still belongs to the next bar.)
        beats = beats[int((first + 0.25) // meter.beats_per_bar) * meter.beats_per_bar :]
    for t in tracks:
        t["sheet"] = None
        if t["notes"] and t["id"] != "drums":
            fname = f"sheet-{t['id']}.musicxml"
            notation.write_musicxml(
                t["notes"], beats, meter, bpm, rec["title"], t["name"], d / fname, grand_staff=t["id"] == "piano"
            )
            t["sheet"] = fname
    notation.write_midi([t for t in tracks if t["notes"]], bpm, meter, d / "transcription.mid")

    result = {
        "id": rec["id"],
        "title": rec["title"],
        "mode": rec["mode"],
        "duration": round(duration, 3),
        "bpm": bpm,
        "beats": beats,
        "timeSignature": meter.signature,
        "beatsPerBar": meter.beats_per_bar,
        # Quarter notes per beat: the player maps score positions to beats with it.
        "beatQuarters": float(meter.beat_ql),
        "audio": f"audio{audio.BROWSER_EXT}",
        "midi": "transcription.mid",
        "tracks": tracks,
        "version": int(time.time()),
    }
    tmp = d / "result.json.tmp"
    tmp.write_text(json.dumps(result))
    tmp.replace(d / "result.json")


def _transcribe(d, tid, name, kind, voice, path, dev, report) -> dict:
    pedals, extra = [], {}
    if kind == "poly":
        output = transcribe.model_output(path, dev, report)
        transcribe.save_output(output, d / f"model-{tid}.npz")
        sensitivity = SENSITIVITY.get(tid, 0.5)
        notes, pedals = transcribe.notes_from_output(output, sensitivity)
        extra["sensitivity"] = sensitivity
    elif kind == "mono":
        notes = transcribe.monophonic(path, *MONO_RANGE[tid], report)
    else:
        notes = transcribe.drums(path, report)
    return {"id": tid, "name": name, "voice": voice, "notes": notes, "pedals": pedals, **extra}
