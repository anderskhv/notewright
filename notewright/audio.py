"""Fetching and converting audio. Everything goes through ffmpeg."""

import os
import shutil
import subprocess
from pathlib import Path


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


# The browser copies are AAC in MP4: seeking in it is sample accurate in every browser,
# unlike variable-bitrate MP3, which Safari and Firefox seek by estimate.
BROWSER_EXT = ".m4a"


def normalise(src: Path, job_dir: Path) -> Path:
    """Write the analysis copy (44.1 kHz stereo wav) and, if missing, the browser copy."""
    wav = job_dir / "audio.wav"
    ffmpeg("-i", str(src), "-ac", "2", "-ar", "44100", str(wav))
    browser = job_dir / f"audio{BROWSER_EXT}"
    if not browser.exists():
        to_browser(wav, browser)
    return wav


def to_browser(src: Path, dst: Path) -> None:
    ffmpeg("-i", str(src), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst))


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
