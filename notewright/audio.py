"""Fetching and converting audio. Everything goes through ffmpeg."""

import os
import shutil
import subprocess
from pathlib import Path


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def normalise(src: Path, job_dir: Path) -> Path:
    """Write the analysis copy (44.1 kHz stereo wav) and the browser copy (mp3)."""
    wav = job_dir / "audio.wav"
    ffmpeg("-i", str(src), "-ac", "2", "-ar", "44100", str(wav))
    ffmpeg("-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "2", str(job_dir / "audio.mp3"))
    return wav


def to_mp3(wav: Path, mp3: Path) -> None:
    ffmpeg("-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "3", str(mp3))


def download(url: str, job_dir: Path) -> tuple[Path, str]:
    """Download the best audio stream of a YouTube (or any yt-dlp supported) link."""
    import yt_dlp

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(job_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    # YouTube extraction needs a JavaScript runtime; yt-dlp only looks for deno by default.
    if not shutil.which("deno"):
        for runtime in ("node", "bun"):
            if shutil.which(runtime):
                opts["js_runtimes"] = {runtime: {}}
                break
    # If YouTube asks to "confirm you're not a bot", borrow your browser's sign-in.
    browser = os.environ.get("NOTEWRIGHT_COOKIES_FROM_BROWSER")
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    return path, info.get("title") or url
