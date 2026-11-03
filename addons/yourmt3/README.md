# YourMT3+ add-on (optional)

[YourMT3+](https://github.com/mimbres/YourMT3) (Chang et al., 2024) transcribes many instruments at once and was trained on band recordings. In Notewright's tests it finds the piano in a band far better than the solo-piano model, so when this add-on is installed, full-band transcriptions take their piano notes from it. Its bass notes are also used: on a Ron Carter trio its note starts were about four times tighter than pitch tracking, and it tells an upright from an electric bass. It files about half of a walking line under other instruments, so pitch tracking fills those in. Separation, horns and drums stay as they are.

Notewright's installer offers it. To add it later (it fetches its own Python 3.12):

```bash
addons/yourmt3/install.sh
```

It adds about 3 GB (its environment and a 560 MB checkpoint) and roughly 1.5x the song's length to each full-band transcription on a laptop CPU.

## Licence

YourMT3+ is not part of Notewright and is not distributed with it: `install.sh` downloads it from its authors' Hugging Face Space. Its GitHub repository is GPL-3.0 (the Space is marked Apache-2.0). Notewright runs it as a separate program (`runner.py`) and only reads the notes it prints. If you redistribute a bundle that includes it, follow its licence.
