"""Optional YourMT3+ add-on: a multi-instrument transcriber trained on band recordings.

On a test ballad with known notes it found the piano far better than the solo-piano
model (F 0.84 vs 0.59-0.73), so when it is installed (addons/yourmt3/install.sh)
Notewright takes the piano notes from it. It runs in its own Python environment as a
separate program; Notewright only reads the notes it prints. Without it, nothing changes.
"""

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable

ADDON = Path(os.environ.get("NOTEWRIGHT_YOURMT3", Path(__file__).resolve().parent.parent / "addons" / "yourmt3"))
CHECKPOINT = "space/amt/logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/checkpoints/last.ckpt"

# YourMT3+ names instrument classes by a representative General MIDI program.
PIANO_CLASSES = {0, 8, 16}  # piano, chromatic percussion (vibes etc.), organ
GUITAR = 24


def python() -> Path:
    return ADDON / "venv" / "bin" / "python"


def available() -> bool:
    return python().exists() and (ADDON / CHECKPOINT).exists()


_running: set[subprocess.Popen] = set()


def cancel() -> None:
    """Stop YourMT3+ (the song was deleted, or the step running next to it failed)."""
    for proc in list(_running):
        proc.kill()


def transcribe(wav: Path, progress: Callable[[float], None]) -> list:
    """All notes YourMT3+ hears: [start, end, pitch, velocity, program, is_drum]."""
    proc = subprocess.Popen(
        [str(python()), str(ADDON / "runner.py"), str(wav)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    _running.add(proc)
    # Read the notes while the progress comes in: a long song's notes don't fit in the pipe,
    # and reading them only after the program ends would leave both sides waiting forever.
    out_parts: list[str] = []
    reader = threading.Thread(target=lambda: out_parts.append(proc.stdout.read()), daemon=True)
    reader.start()
    tail = []
    try:
        for line in proc.stderr:
            if line.startswith("progress "):
                progress(float(line.split()[1]))
            else:
                tail = (tail + [line])[-20:]
        reader.join()
        out = "".join(out_parts)
    except BaseException:
        proc.kill()
        raise
    finally:
        _running.discard(proc)
    if proc.wait() != 0:
        raise RuntimeError("YourMT3+ add-on failed: " + "".join(tail)[-500:])
    return json.loads(out)["notes"]


def piano_notes(notes: list, guitar_in_lineup: bool) -> list:
    """Its piano notes. Without a guitar in the band, what it calls guitar is piano."""
    classes = PIANO_CLASSES | (set() if guitar_in_lineup else {GUITAR})
    return sorted([n[0], n[1], n[2], n[3]] for n in notes if not n[5] and n[4] in classes)


# General MIDI 32-39 are the basses: 32 acoustic (upright), 33-39 electric and synth.
BASSES = range(32, 40)
ACOUSTIC_BASS = 32


def bass_notes(notes: list) -> list:
    """Its bass notes, [start, end, pitch, velocity].

    On band recordings its note starts are about four times tighter than pitch tracking
    the separated bass part (17 ms against 76 ms spread on a Ron Carter trio track), and
    it hears notes above C4 that the bass part has lost.
    """
    return sorted([n[0], n[1], n[2], n[3]] for n in notes if not n[5] and n[4] in BASSES)


def bass_kind(notes: list) -> str | None:
    """"double" or "electric", by which bass it hears most; None if it hears hardly any."""
    progs = [n[4] for n in notes if not n[5] and n[4] in BASSES]
    if len(progs) < 12:  # a minute of a ballad can give only a couple of dozen
        return None
    return "double" if sum(p == ACOUSTIC_BASS for p in progs) >= len(progs) / 2 else "electric"


# General MIDI drum keys, folded onto the kit Notewright plays: kick, snare, cymbal.
KICKS, SNARES = {35, 36}, {37, 38, 39, 40, 41, 43, 45, 47, 48, 50}  # toms play as snare


def drum_notes(notes: list) -> list:
    """Its drum hits as [start, end, key, velocity]: 36 kick, 38 snare, the cymbal key otherwise."""
    out = []
    for n in notes:
        if not n[5]:
            continue
        key = 36 if n[2] in KICKS else 38 if n[2] in SNARES else n[2]
        out.append([n[0], max(n[1], n[0] + 0.05), key, n[3]])
    return sorted(out)
