import numpy as np
import soundfile as sf

from notewright import audio, slow


def test_slowed_copy_is_longer_and_keeps_pitch(tmp_path):
    sr = 44100
    t = np.arange(sr * 2) / sr
    y = 0.3 * np.sin(2 * np.pi * 440 * t)
    sf.write(tmp_path / "a.wav", np.stack([y, y], 1), sr)
    audio.to_browser(tmp_path / "a.wav", tmp_path / "a.m4a")
    out = slow.render(tmp_path / "a.m4a", 0.5, tmp_path / "slow" / "a@50.m4a")
    audio.ffmpeg("-i", str(out), "-ac", "1", str(tmp_path / "b.wav"))
    z, _ = sf.read(tmp_path / "b.wav")
    assert abs(len(z) / sr - 4.0) < 0.2  # twice as long
    spectrum = np.abs(np.fft.rfft(z[sr : 2 * sr]))
    assert abs(np.argmax(spectrum) - 440) < 5  # still A440 (1 Hz bins over one second)
