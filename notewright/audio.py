"""Fetching and converting audio. Everything goes through ffmpeg."""

import os
import shutil
import subprocess
import sys
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


DOWNLOAD_LIMIT = 15 * 60  # seconds: a stuck download must not hold up every song after it
_active: subprocess.Popen | None = None  # the running download, so deleting its song can stop it


def cancel_download() -> None:
    if _active and _active.poll() is None:
        _active.kill()


def download(url: str, job_dir: Path) -> tuple[Path, str]:
    """Download the best audio stream of a YouTube (or any yt-dlp supported) link.

    yt-dlp runs as its own process with network timeouts and an overall limit, so a
    download that hangs fails with a message instead of stalling the queue.
    """
    global _active
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio/best", "-o", str(job_dir / "source.%(ext)s"), "--no-playlist",
        "--socket-timeout", "30", "--retries", "5", "--extractor-retries", "3", "--no-warnings",
        "--print", "after_move:title", "--print", "after_move:filepath",
    ]
    # YouTube extraction needs a JavaScript runtime; yt-dlp only looks for deno by default.
    if not shutil.which("deno"):
        for runtime in ("node", "bun"):
            if shutil.which(runtime):
                cmd += ["--js-runtimes", runtime]
                break
    # If YouTube asks to "confirm you're not a bot", borrow your browser's sign-in.
    browser = os.environ.get("NOTEWRIGHT_COOKIES_FROM_BROWSER")
    if browser:
        cmd += ["--cookies-from-browser", browser]
    cmd.append(url)
    _active = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = _active.communicate(timeout=DOWNLOAD_LIMIT)
    except subprocess.TimeoutExpired:
        _active.kill()
        _active.communicate()
        raise RuntimeError("YouTube didn't send the audio within 15 minutes. Try again, or download it "
                           "yourself and use Upload a file.") from None
    finally:
        code, _active = _active.returncode, None
    if code:
        errors = [line for line in err.splitlines() if "ERROR" in line] or err.strip().splitlines()[-1:]
        raise RuntimeError(errors[-1] if errors else "The download stopped.")
    lines = out.strip().splitlines()
    path = Path(lines[-1])
    return path, (lines[-2] if len(lines) >= 2 else "") or url
