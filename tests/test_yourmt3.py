import sys
import threading

from notewright import yourmt3

# A stand-in for the add-on: reports progress, then prints far more notes than a pipe holds.
FAKE_RUNNER = '''
import json, sys
for i in range(5):
    print(f"progress {(i + 1) / 5}", file=sys.stderr, flush=True)
json.dump({"notes": [[i * 0.1, i * 0.1 + 0.5, 60, 80, 0, False] for i in range(20000)]}, sys.stdout)
'''


def test_a_long_song_does_not_deadlock(tmp_path, monkeypatch):
    (tmp_path / "runner.py").write_text(FAKE_RUNNER)
    monkeypatch.setattr(yourmt3, "ADDON", tmp_path)
    monkeypatch.setattr(yourmt3, "python", lambda: sys.executable)
    result, seen = [], []
    worker = threading.Thread(target=lambda: result.append(yourmt3.transcribe(tmp_path / "x.wav", seen.append)),
                              daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "stuck after the last progress report"
    assert len(result[0]) == 20000 and seen[-1] == 1.0
