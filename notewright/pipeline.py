"""The job pipeline: fetch, separate, transcribe, notate. One job runs at a time."""

import json
import os
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio, chords, instruments, notation, score, transcribe, untangle, yourmt3
from .config import DATA_DIR, JobDeleted, device

# Per family: transcriber and sampler voice in the browser. Horns get one track per horn.
FAMILY_TRACKS = {
    "piano": ("poly", "piano"),
    "horns": ("mono", None),
    "guitar": ("poly", "acoustic_guitar_steel"),
    "vocals": ("mono", "voice_oohs"),
    "bass": ("mono", None),
    "drums": ("drums", "drums"),
    "other": ("poly", "string_ensemble_1"),
}
HORN_VOICES = {"trumpet": "trumpet", "trombone": "trombone", "clarinet": "clarinet", "flute": "flute"}
# Bass stops at middle C: walking lines live below it, and a higher limit invites octave errors.
# Harmonics of a bass note that stay in the bass part when piano is filtered out of it:
# the first six keep its sound; above them a low note's harmonics crowd so close together
# that protecting them would protect the piano too (tested on a Ron Carter trio).
BASS_PROTECTED_PARTIALS = 6
MONO_RANGE = {"bass": (30.0, 262.0), "vocals": (80.0, 1100.0), "horns": (80.0, 1400.0)}
# Default note sensitivity per polyphonic track (0.5 is the piano model's own default).
SENSITIVITY = {"piano": 0.5, "guitar": 0.4, "other": 0.25}


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


_deleted: set[str] = set()
_running: str | None = None


def delete_job(job_id: str) -> None:
    if job_id == _running:
        _deleted.add(job_id)  # the worker stops at its next progress report
        audio.cancel_download()
        yourmt3.cancel()
    d = jobs_dir() / job_id
    if d.is_dir() and d.parent == jobs_dir():
        shutil.rmtree(d)


_save_lock = threading.Lock()  # two steps can report progress at once (separating and YourMT3+)


class Job:
    def __init__(self, record: dict):
        self.record = record
        self.dir = jobs_dir() / record["id"]
        self.timings: list[list] = []  # [stage, seconds], for the speed summary in the terminal
        self._lap = None

    def save(self, **changes) -> None:
        if self.record["id"] in _deleted:
            raise JobDeleted(self.record["id"])
        with _save_lock:
            self.record.update(changes)
            tmp = self.dir / "job.json.tmp"
            tmp.write_text(json.dumps(self.record))
            tmp.replace(self.dir / "job.json")

    def _time(self, name: str | None) -> None:
        now = time.monotonic()
        if self._lap:
            self.timings.append([self._lap[0], round(now - self._lap[1], 1)])
        self._lap = (name, now) if name else None

    def summary(self) -> str:
        self._time(None)
        total = sum(s for _, s in self.timings)
        steps = " · ".join(f"{name} {s:.0f} s" for name, s in self.timings)
        return f"Notewright: \"{self.record['title']}\" took {total:.0f} s on {device()}: {steps}"

    def stage(self, name: str, start: float, end: float):
        """Progress callback mapping a stage's 0..1 onto the job's start..end."""
        self._time(name)
        self.save(stage=name, progress=round(start, 3))
        last = [0.0]

        def report(fraction: float) -> None:
            if fraction - last[0] >= 0.02 or fraction >= 1.0:
                last[0] = fraction
                self.save(progress=round(start + (end - start) * fraction, 3))

        return report


_queue: "queue.Queue[str]" = queue.Queue()


def submit(mode: str, url: str | None = None, upload: tuple[str, bytes] | None = None,
           uploads: list[tuple[str, bytes]] | None = None) -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = jobs_dir() / job_id
    d.mkdir()
    title = url or (upload[0] if upload else "Untitled")
    if upload:
        (d / ("upload" + Path(upload[0]).suffix.lower())).write_bytes(upload[1])
    for i, (name, data) in enumerate(uploads or []):  # sheet music: pictures and pages, in order
        (d / f"sheet-{i:03d}{Path(name).suffix.lower() or '.png'}").write_bytes(data)
    if uploads:
        title = Path(uploads[0][0]).stem if len(uploads) == 1 and uploads[0][0] not in ("image.png", "") else "Sheet music"
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
    global _running
    while True:
        job_id = _queue.get()
        _running = job_id
        try:
            _work(job_id)
        except JobDeleted:
            shutil.rmtree(jobs_dir() / job_id, ignore_errors=True)  # anything written after the delete
        except Exception:  # noqa: BLE001 - the worker must outlive any one song
            traceback.print_exc()
        finally:
            _running = None
            _deleted.discard(job_id)


def _work(job_id: str) -> None:
    record = read_job(job_id)
    if not record:
        return
    job = Job(record)
    try:
        job.save(status="running", error=None)
        action = record.get("action") or {}
        if action.get("type") == "retune":
            retune(job, action["track"], action["sensitivity"])
        elif action.get("type") == "lineup":
            relineup(job, action["lineup"])
        else:
            run(job)
        if job.timings or job._lap:
            print(job.summary(), flush=True)
            job.save(timings=job.timings)
        job.save(status="done", stage="Done", progress=1.0, action=None)
    except Exception as exc:  # noqa: BLE001 - any failure is reported to the UI
        if job_id in _deleted:
            raise JobDeleted(job_id) from exc
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


def run(job: Job) -> None:
    d, rec, dev = job.dir, job.record, device()
    if rec["mode"] == "score":
        return run_score(job)
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

    extra = {}
    if band:
        from .separate import separate

        has_ymt3 = yourmt3.available()
        if has_ymt3 and dev != "cpu" and _memory_gb() >= 15:  # a "16 GB" machine reports a little less
            # Separation runs on the GPU and YourMT3+ on the CPU, so they can work at the same time
            # (with room for both in memory; with less, one after the other avoids swapping).
            stems, notes = _separate_and_ymt3(job, wav, dev)
        else:
            stems = separate(wav, d / "stems", dev, job.stage("Separating instruments", 0.08, 0.3 if has_ymt3 else 0.4))
            if has_ymt3:
                notes = yourmt3.transcribe(wav, job.stage("Transcribing with YourMT3+", 0.3, 0.45), dev)
        if has_ymt3:
            (d / "yourmt3.json").write_text(json.dumps(notes))
        # Keep Demucs' own stems, so the line-up can be changed later without separating again.
        raw = d / "stems" / "raw"
        raw.mkdir(exist_ok=True)
        for name, path in stems.items():
            audio.to_browser(path, raw / f"{name}{audio.BROWSER_EXT}")
        job.stage("Recognising instruments", 0.45 if has_ymt3 else 0.4, 0.47 if has_ymt3 else 0.45)
        try:
            lineup = instruments.normalise_lineup(instruments.detect(wav, stems, dev))
            lineup["source"] = "detected"
        except Exception:  # noqa: BLE001 - recognition is a suggestion; never lose the song over it
            traceback.print_exc()
            lineup = instruments.normalise_lineup(instruments.lineup_by_level(wav, stems))
            lineup["source"] = "loudness"
        if has_ymt3 and "bass" in lineup["families"]:
            # Which bass: YourMT3+ names the instrument note by note, and on a Ron Carter
            # trio it heard the upright that the sound-event model called electric.
            lineup["bassKind"] = yourmt3.bass_kind(notes) or lineup.get("bassKind")
        tracks = _band(job, {n: _read(p) for n, p in stems.items()}, lineup, 0.47 if has_ymt3 else 0.45)
        extra["lineup"] = lineup
    else:
        report = job.stage("Transcribing piano", 0.08, 0.95)
        tracks = [_transcribe(d, "piano", "Piano", "poly", "piano", wav, dev, report)]

    job.stage("Writing sheet music", 0.95, 1.0)
    _write_result(job, tracks, duration, **extra)
    # The wav copies are only needed for analysis.
    wav.unlink(missing_ok=True)
    for f in list(d.glob("source.*")) + list(d.glob("upload.*")) + list((d / "stems").glob("*.wav")):
        f.unlink(missing_ok=True)


def envelope(d: Path) -> Path:
    """Cache and return envelope.json for a finished job (see server.envelope)."""
    path = d / "envelope.json"
    result = json.loads((d / "result.json").read_text())
    if path.exists() and json.loads(path.read_text()).get("version") == result.get("version"):
        return path
    wav = d / "envelope.wav"
    audio.ffmpeg("-i", str(d / result["audio"]), "-ac", "1", "-ar", "16000", str(wav))
    y, sr = sf.read(str(wav), dtype="float32")
    wav.unlink()
    hop = sr // 100
    frames = len(y) // hop
    energy = (y[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1)
    log = np.log10(energy + 1e-10)
    path.write_text(json.dumps({"version": result.get("version"), "fps": 100, "log": [round(float(v), 2) for v in log]}))
    return path


def run_score(job: Job) -> None:
    """Sheet music to a playable piece: recognise, combine, and write the usual result."""
    d = job.dir
    files = sorted(d.glob("sheet-[0-9][0-9][0-9].*"))
    xmls = score.recognise(files, d / "pages", job.stage("Reading the sheet music", 0.0, 0.85))
    job.stage("Preparing playback", 0.85, 1.0)
    piece = score.read(score.combine(xmls))
    if piece["title"]:
        job.save(title=piece["title"])
    piece["score"].write("musicxml", fp=str(d / "sheet-piano.musicxml"))
    # No recording: a silent track keeps the player's clock (speed, loops, cursor) as it is.
    audio.ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(piece["duration"]),
                 "-c:a", "aac", "-b:a", "32k", str(d / f"audio{audio.BROWSER_EXT}"))
    track = {"id": "piano", "name": "Piano" if piece["staves"] > 1 else "Melody", "voice": "piano",
             "notes": piece["notes"], "pedals": [], "sheet": "sheet-piano.musicxml", "source": "sheet"}
    # A single line has no harmony to name; a two-staff piano part does.
    chord_list = chords.detect([track], piece["beats"], piece["beatsPerBar"]) if piece["staves"] > 1 else []
    notation.write_midi([track], piece["bpm"], notation.Meter(4, False), d / "transcription.mid")
    result = {
        "id": job.record["id"],
        "kind": "score",
        "title": job.record["title"],
        "mode": "score",
        "duration": piece["duration"],
        "bpm": piece["bpm"],
        "beats": piece["beats"],
        "timeSignature": piece["timeSignature"],
        "swing": False,
        "beatsPerBar": piece["beatsPerBar"],
        "beatQuarters": 1.0,
        "audio": f"audio{audio.BROWSER_EXT}",
        "midi": "transcription.mid",
        "tracks": [track],
        "chords": chord_list,
        "lineup": None,
        "version": int(time.time()),
    }
    (d / "result.json").write_text(json.dumps(result))


def _memory_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return 0.0


def _separate_and_ymt3(job: Job, wav: Path, dev: str) -> tuple[dict, list]:
    from concurrent.futures import ThreadPoolExecutor

    from .separate import separate

    report = job.stage("Separating instruments and transcribing with YourMT3+", 0.08, 0.45)
    done = {"separate": 0.0, "yourmt3": 0.0}

    def part(key):
        def update(fraction: float) -> None:
            done[key] = fraction
            report((done["separate"] + done["yourmt3"]) / 2)
        return update

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(yourmt3.transcribe, wav, part("yourmt3"), dev)
        try:
            stems = separate(wav, job.dir / "stems", dev, part("separate"))
        except BaseException:
            yourmt3.cancel()  # don't wait minutes for a result nobody will use
            raise
        return stems, future.result()


def _read(path: Path) -> tuple[np.ndarray, int]:
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return y.T, sr


def _band(job: Job, stems: dict[str, tuple[np.ndarray, int]], lineup: dict, start: float) -> list[dict]:
    """Regroup the stems into the line-up, write one browser stem per family, transcribe."""
    d, dev = job.dir, device()
    sr = next(iter(stems.values()))[1]
    job.stage("Regrouping instruments", start, start + 0.05)
    parts = instruments.arrange({n: y for n, (y, _) in stems.items()}, sr, lineup)
    for old in (d / "stems").glob(f"*{audio.BROWSER_EXT}"):
        old.unlink()
    shutil.rmtree(d / "slow", ignore_errors=True)  # slowed copies of the old parts
    for f in list(d.glob("model-*.npz")):
        f.unlink()  # parts changed, so saved model output no longer matches
    paths = {}
    for family, y in parts.items():
        wav = d / "stems" / f"{family}.wav"
        sf.write(str(wav), y.T, sr)
        audio.to_browser(wav, d / "stems" / f"{family}{audio.BROWSER_EXT}")
        paths[family] = wav

    tracks = []
    families = lineup["families"]
    span = (0.95 - start - 0.05) / max(1, len(families))
    piano_done = None
    ymt3_all = _yourmt3_all(d)
    ymt3 = yourmt3.piano_notes(ymt3_all, "guitar" in lineup["families"]) if ymt3_all is not None else None
    piano_notes = None
    if "piano" in families and "horns" in families:
        lo = start + 0.05
        piano_done = _untangle(job, parts, paths, sr, lo, lo + 2 * span, ymt3)
    elif "piano" in families and ymt3 is not None:
        piano_done = {"piano": _ymt3_track(ymt3)}
    # Piano first: its notes are needed to clean the bass part.
    for i, family in enumerate(sorted(families, key=lambda f: f != "piano")):
        kind, voice = FAMILY_TRACKS[family]
        lo = start + 0.05 + i * span
        report = job.stage(f"Transcribing {instruments.NAMES.get(family, family).split(' ')[0].lower()}", lo, lo + span)
        stem = {"stem": f"stems/{family}{audio.BROWSER_EXT}", "stemId": family}
        if family == "piano" and piano_done:
            piano = dict(piano_done["piano"])
            if piano.get("source") == "yourmt3":
                # YourMT3+ reports no dynamics (velocity 1): without this the piano plays
                # at a whisper next to the bass and drums.
                piano["notes"] = transcribe.loudness(piano["notes"], parts["piano"], sr)
            tracks.append({**piano, **stem})
            piano_notes = piano["notes"]
            continue
        if family == "horns":
            if piano_done and "horns" in piano_done:
                line = piano_done["horns"]
            else:
                # Gate: in another instrument's solo the horn part holds only leakage.
                line = transcribe.monophonic(paths[family], *MONO_RANGE["horns"], report, gate=0.2)
                line = untangle.clean_line(line, parts[family], sr, np.zeros((0, 2)))
            split = instruments.split_horns(line, lineup["horns"])
            for horn in lineup["horns"]:
                notes = split.get(horn, [])
                median = float(np.median([n[2] for n in notes])) if notes else 60
                hv = HORN_VOICES.get(horn) or ("alto_sax" if median >= 68 else "tenor_sax")
                tracks.append({"id": horn, "name": instruments.NAMES[horn], "voice": hv, "notes": notes,
                               "pedals": [], "group": "Horns", **stem})
            continue
        if family == "bass":
            voice = "acoustic_bass" if lineup.get("bassKind") == "double" else "electric_bass_finger"
        name = instruments.NAMES[family]
        if family == "bass" and lineup.get("bassKind") == "double":
            name = "Double bass"
        if family == "bass":
            track = _bass(d, name, voice, parts, paths, sr, piano_notes, ymt3_all, report)
        elif family == "drums" and ymt3_all is not None and _hears_drums(ymt3_all, parts["drums"].shape[-1] / sr):
            # YourMT3+ hears a jazz kit (ride, snare, brushes); the onset classifier only
            # knew kick, snare and hi-hat. Ron Carter trio: 84% of its cymbal hits on the
            # beat or the swung upbeat, against 77%.
            notes = transcribe.loudness(yourmt3.drum_notes(ymt3_all), parts["drums"], sr)
            track = {"id": "drums", "name": name, "voice": voice, "notes": notes, "pedals": [], "source": "yourmt3"}
            report(1.0)
        else:
            track = _transcribe(d, family, name, kind, voice, paths[family], dev, report)
        if family == "piano":
            piano_notes = track["notes"]
        tracks.append({**track, **stem})
    for wav in paths.values():
        wav.unlink(missing_ok=True)
    return tracks


def _hears_drums(ymt3_all: list, seconds: float) -> bool:
    """Whether YourMT3+ heard the drums well enough to replace the onset classifier.

    It needs the cymbals' high end: on a low-bitrate copy of a trio it heard 3 hits in a
    minute, on the original 3 a second. Under one hit every two seconds, keep the classifier.
    """
    return len(yourmt3.drum_notes(ymt3_all)) >= max(20, 0.5 * seconds)


def _yourmt3_all(d: Path) -> list | None:
    path = d / "yourmt3.json"
    return json.loads(path.read_text()) if path.exists() else None


def _bass(d: Path, name: str, voice: str, parts: dict, paths: dict, sr: int, piano_notes: list | None,
          ymt3_all: list | None, report) -> dict:
    """The bass line, and the bass part's audio with the piano taken out of it.

    Notes: from YourMT3+ when it heard the bass (tighter timing, notes above C4),
    otherwise pitch-tracked from the bass part. Either way each start moves to the
    nearest pluck in the bass part's own audio.
    Audio: Demucs leaves piano in the bass part (on a trio, a high piano note made the
    bass part twice as loud at that pitch for 43% of notes); every transcribed piano
    note's harmonics move from the bass part to the piano part (22% after), except where
    the bass itself plays them.
    """
    heard = yourmt3.bass_notes(ymt3_all) if ymt3_all is not None else []
    tracked = transcribe.monophonic(paths["bass"], *MONO_RANGE["bass"], report)
    if piano_notes and "piano" in parts:
        piano_audio, bass_audio = untangle.separate(parts["piano"], parts["bass"], sr, piano_notes,
                                                    heard + tracked, protect_partials=BASS_PROTECTED_PARTIALS)
        for family, y in (("piano", piano_audio), ("bass", bass_audio)):
            parts[family] = y
            sf.write(str(paths[family]), y.T, sr)
            audio.to_browser(paths[family], d / "stems" / f"{family}{audio.BROWSER_EXT}")
        tracked = transcribe.monophonic(paths["bass"], *MONO_RANGE["bass"], report)  # without the piano now
    plucks = transcribe.attacks(parts["bass"], sr)
    # The model's starts are already close (17 ms spread); the pitch tracker's run late.
    low = [n for n in (ymt3_all or []) if not n[5] and n[2] < 55]   # anything YourMT3+ heard down there
    notes = combine_bass(transcribe.snap_to_attacks(heard, plucks, 0.035),
                         transcribe.snap_to_attacks(tracked, plucks, 0.08), plucks, low)
    notes = transcribe.loudness(notes, parts["bass"], sr)
    track = {"id": "bass", "name": name, "voice": voice, "notes": notes, "pedals": []}
    if len(heard) >= 40:
        track["source"] = "yourmt3"
    report(1.0)
    return track


def combine_bass(heard: list, tracked: list, plucks: np.ndarray, low: list, gap: float = 0.08) -> list:
    """One bass line from YourMT3+'s bass notes and the pitch tracker's.

    YourMT3+ times notes well and hears high ones, but on a walking line it files about
    half the notes under other instruments. So its bass notes come first, and the pitch
    tracker fills the gaps, but only with notes that start on a real pluck. Where
    YourMT3+ did hear a low note there (filed under piano or guitar), its pitch is used:
    the pitch tracker is the one that slips by an octave. Each note then ends where the
    next one starts (a bass line is one note at a time).
    """
    starts = np.array(sorted(n[0] for n in heard))
    line = [list(n) for n in heard]
    for n in tracked:
        if len(starts) and np.min(np.abs(starts - n[0])) <= gap:
            continue                                   # YourMT3+ has this one
        if not len(plucks) or np.min(np.abs(plucks - n[0])) > 0.03 or n[1] - n[0] < 0.08:
            continue                                   # not on a pluck, or a flicker
        same_time = [m for m in low if abs(m[0] - n[0]) <= 0.05]
        if same_time:
            pitch = min(same_time, key=lambda m: abs(m[2] - n[2]))[2]
            n = [n[0], n[1], pitch, *n[3:]]
        line.append(list(n))
    line.sort()
    for a, b in zip(line, line[1:]):
        if a[1] > b[0]:
            a[1] = max(a[0] + 0.03, b[0])
    return line


def _ymt3_track(notes: list) -> dict:
    return {"id": "piano", "name": instruments.NAMES["piano"], "voice": "piano", "notes": notes, "pedals": [],
            "source": "yourmt3"}


def _untangle(job: Job, parts: dict, paths: dict, sr: int, lo: float, hi: float, ymt3: list | None) -> dict:
    """Piano and horns together: see untangle.py. Rewrites both parts' audio."""
    d, dev = job.dir, device()
    step = (hi - lo) / 4
    # 1. A first horn line, to take the horns out of what the piano model hears.
    job.stage("Listening to the horns", lo, lo + step)
    line = transcribe.monophonic(paths["horns"], *MONO_RANGE["horns"], lambda f: None, gate=0.2)
    line = untangle.clean_line(line, parts["horns"], sr, np.zeros((0, 2)))
    if ymt3 is not None:
        # 2a. The add-on, trained on band recordings, already heard the piano in the mix.
        piano = _ymt3_track(ymt3)
        piano_notes, pedals = ymt3, []
    else:
        # 2b. The solo-piano model, on piano and horn parts with the horn line filtered out
        #     (Demucs files many voicings under "other", and a loud horn hides quiet comping).
        bed = d / "stems" / "piano-bed.wav"
        sf.write(str(bed), (parts["piano"] + untangle.without_horns(parts["horns"], sr, line)).T, sr)
        output = transcribe.model_output(bed, dev, job.stage("Transcribing piano", lo + step, lo + 3 * step))
        bed.unlink()
        transcribe.save_output(output, d / "model-piano.npz")
        sensitivity = SENSITIVITY["piano"]
        piano_notes, pedals = transcribe.notes_from_output(output, sensitivity)
        piano = {"id": "piano", "name": instruments.NAMES["piano"], "voice": "piano", "pedals": pedals,
                 "sensitivity": sensitivity}
    onsets = np.array([[n[0], n[2]] for n in piano_notes]) if piano_notes else np.zeros((0, 2))
    # 3. Piano doubling a horn is the horn; then move the piano's harmonics out of the
    #    horn audio and hear the horns again.
    job.stage("Untangling piano and horns", lo + 3 * step, lo + 3.5 * step)
    line = untangle.clean_line(line, parts["horns"], sr, onsets)
    piano_notes = untangle.drop_doubled(piano_notes, line)
    piano["notes"] = piano_notes
    piano_audio, horn_audio = untangle.separate(parts["piano"], parts["horns"], sr, piano_notes, line, pedals)
    for family, y in (("piano", piano_audio), ("horns", horn_audio)):
        parts[family] = y
        sf.write(str(paths[family]), y.T, sr)
        audio.to_browser(paths[family], d / "stems" / f"{family}{audio.BROWSER_EXT}")
    report = job.stage("Transcribing horns", lo + 3.5 * step, hi)
    line = transcribe.monophonic(paths["horns"], *MONO_RANGE["horns"], report, gate=0.2)
    line = untangle.clean_line(line, horn_audio, sr, onsets)
    return {"piano": piano, "horns": line}


def _piano_notes(outputs: list, sensitivity: float) -> tuple[list, list]:
    """Notes from one or more piano-model outputs; a note heard in several counts once."""
    notes, pedals = transcribe.notes_from_output(outputs[0], sensitivity)
    for output in outputs[1:]:
        more, _ = transcribe.notes_from_output(output, sensitivity)
        for n in more:
            if not any(m[2] == n[2] and abs(m[0] - n[0]) < 0.06 for m in notes):
                notes.append(n)
    return sorted(notes), pedals


def relineup(job: Job, lineup: dict) -> None:
    """Rebuild the parts for a corrected line-up from the kept Demucs stems."""
    d = job.dir
    result = json.loads((d / "result.json").read_text())
    raw = d / "stems" / "raw"
    if not raw.exists():
        raise RuntimeError("This song was separated by an older version. Press Separate instruments again.")
    job.stage("Reading the separated parts", 0.0, 0.05)
    stems = {}
    for f in raw.glob(f"*{audio.BROWSER_EXT}"):
        wav = f.with_suffix(".wav")
        audio.ffmpeg("-i", str(f), "-ac", "2", "-ar", "44100", str(wav))
        stems[f.stem] = _read(wav)
        wav.unlink()
    lineup = instruments.normalise_lineup(lineup)
    tracks = _band(job, stems, lineup, 0.05)
    job.stage("Writing sheet music", 0.95, 1.0)
    _write_result(job, tracks, result["duration"], lineup=lineup)


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
    extra = d / f"model-{track_id}-part.npz"  # the untangled piano was heard twice (see _untangle)
    outputs = [transcribe.load_output(p) for p in (raw, extra) if p.exists()]
    track["notes"], track["pedals"] = _piano_notes(outputs, sensitivity)
    horn_notes = [n for t in result["tracks"] if t.get("group") == "Horns" for n in t["notes"]]
    if track_id == "piano" and horn_notes:
        track["notes"] = untangle.drop_doubled(track["notes"], horn_notes)
    track["sensitivity"] = sensitivity
    job.stage("Writing sheet music", 0.95, 1.0)
    _write_result(job, result["tracks"], result["duration"], lineup=result.get("lineup"))


def _write_result(job: Job, tracks: list[dict], duration: float, lineup: dict | None = None) -> None:
    d, rec = job.dir, job.record
    grid_path = d / "grid.json"
    if grid_path.exists():
        grid = json.loads(grid_path.read_text())
    else:  # made before 0.2: rebuild the grid from the (trimmed) one in the result
        old = json.loads((d / "result.json").read_text())
        grid = {"bpm": old["bpm"], "beats": old["beats"], "perBar": old["beatsPerBar"], "compound": old["beatQuarters"] > 1}
    bpm, beats = grid["bpm"], grid["beats"]

    pitched = [n for t in tracks if t["id"] != "drums" for n in t["notes"]]
    meter = notation.detect_meter(pitched, beats, grid["perBar"], grid["compound"], bpm)
    if pitched:
        # Start the score at the bar where the music starts, not with empty bars.
        first = notation.seconds_to_beats([min(n[0] for n in pitched)], beats)[0]
        # (+0.25: a note played a touch early still belongs to the next bar.)
        beats = beats[int((first + 0.25) // meter.beats_per_bar) * meter.beats_per_bar :]
    chord_list = chords.detect(tracks, beats, meter.beats_per_bar)
    for t in tracks:
        t["sheet"] = None
        if t["notes"] and t["id"] != "drums":
            fname = f"sheet-{t['id']}.musicxml"
            notation.write_musicxml(
                t["notes"], beats, meter, bpm, rec["title"], t["name"], d / fname, grand_staff=t["id"] == "piano",
                chords=chord_list,
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
        "swing": meter.swing,
        "beatsPerBar": meter.beats_per_bar,
        # Quarter notes per beat: the player maps score positions to beats with it.
        "beatQuarters": float(meter.beat_ql),
        "audio": f"audio{audio.BROWSER_EXT}",
        "midi": "transcription.mid",
        "tracks": tracks,
        "chords": chord_list,
        "lineup": lineup,
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
