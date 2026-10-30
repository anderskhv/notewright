"""Slowed-down copies of the recording, rendered with Signalsmith Stretch.

Browsers slow audio down with a basic pitch-preserving method that smears and warbles
at 25-50% speed. Signalsmith Stretch (MIT) does far better; Notewright renders the
recording (and its separated parts) at the chosen speed once, and the player swaps the
rendered copy in. Rendering is roughly 40x faster than real time on a laptop.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def render(src: Path, speed: float, dst: Path) -> Path:
    """dst: src played at `speed` (0.25-0.95), same pitch. Cached; safe to call concurrently.

    The work runs in a separate, low-priority process: done in the server's own process
    it starved the audio the browser was streaming at that moment, and playback stalled.
    """
    with _guard:
        lock = _locks.setdefault(str(dst), threading.Lock())
    with lock:
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "notewright.slow", str(src), str(speed), str(dst)], check=True)
    return dst


def _render(src: Path, speed: float, dst: Path) -> None:
    import python_stretch as ps

    wav_in, wav_out = dst.with_suffix(".in.wav"), dst.with_suffix(".out.wav")
    try:
        audio.ffmpeg("-i", str(src), "-ac", "2", "-ar", "44100", str(wav_in))
        y, sr = sf.read(str(wav_in), dtype="float32", always_2d=True)
        stretch = ps.Signalsmith.Stretch()
        stretch.preset(y.shape[1], sr)
        stretch.timeFactor = speed  # playback speed: 0.5 makes it twice as long
        out = stretch.process(np.ascontiguousarray(y.T))
        sf.write(str(wav_out), out.T, sr)
        part = dst.with_suffix(".part" + dst.suffix)
        audio.to_browser(wav_out, part)
        part.rename(dst)
    finally:
        wav_in.unlink(missing_ok=True)
        wav_out.unlink(missing_ok=True)


if __name__ == "__main__":
    os.nice(10)
    _render(Path(sys.argv[1]), float(sys.argv[2]), Path(sys.argv[3]))
