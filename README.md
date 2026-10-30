# Notewright

Paste a YouTube link and get the music back as sheet music and a falling-notes piano roll. Play the original next to the transcription at any speed, or mute your own instrument and play along with the rest of the band.

Everything runs on your own computer. Notewright uses no accounts, no cloud service and no subscription.

## What it does

- **Piano mode** transcribes a piano recording: every note, how hard it was played, and the sustain pedal.
- **Full band mode** separates the instruments, recognises which ones actually play, and transcribes each one. You see your real line-up (for example piano, saxophone, trumpet, double bass and drums), not a fixed list. If the line-up is wrong, tick the right instruments and the parts are rebuilt.
- **Sheet music** in the right time signature (2/4, 3/4, 4/4, 6/8, 9/8, 12/8), with bar lines on the real downbeats, a key signature, and the piano split across two staves by hand. Swing is written in plain eighth notes marked "Swing", the way jazz charts are, and real triplets stay triplets.
- **Piano roll**, Synthesia style, with each instrument in its own colour.
- **Playback** of the original, the separated parts and the transcription (played on sampled instruments), each with its own volume. The player measures the delay between the recording and the notes in your browser and corrects for it, so they stay together.
- **Speed** from 25% to 150% without changing pitch, plus A–B looping for practice.
- **Play along**: mute your instrument and keep either the real band (the separated recording) or the transcribed band. A song transcribed in piano mode can be separated later with one click, without downloading it again.
- **More or fewer notes**: if quiet notes are missing, such as comping chords under a solo, move the slider above the sheet music. Notewright then reads that part again from the saved model output, which takes seconds.
- **Export** to MusicXML (opens in MuseScore, Sibelius, Finale or Dorico) and to MIDI.

## Install

You need Python 3.11–3.13, [ffmpeg](https://ffmpeg.org), and a JavaScript runtime for YouTube downloads: [Node.js](https://nodejs.org) or [Deno](https://deno.com).

On macOS with [Homebrew](https://brew.sh):

```bash
brew install python@3.13 ffmpeg node
```

Then create the environment with `python3.13`. On a Mac, plain `python3` is Apple's older Python 3.9, which can't install the requirements.


```bash
git clone https://github.com/anderskhv/notewright.git
cd notewright
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python -m notewright
```

This opens http://127.0.0.1:8765 in your browser. The first transcription downloads the models once into `~/.cache`: about 165 MB for the piano model, 80 MB for the beat tracker, 80 MB for the separator and 330 MB for the instrument recogniser. After that, transcription works offline. Instrument sounds for playback load from the web the first time they are used.

If YouTube answers "Sign in to confirm you're not a bot", let yt-dlp use your browser's YouTube sign-in. Name your browser (`chrome`, `safari`, `firefox`, `edge` or `brave`):

```bash
NOTEWRIGHT_COOKIES_FROM_BROWSER=chrome python -m notewright
```

Transcriptions are kept in `data/` next to the code. Set `NOTEWRIGHT_DATA` to keep them somewhere else.

### Speed

On a recent laptop CPU, piano mode takes about as long as the song itself, and full band mode takes a few times longer. With an NVIDIA GPU, PyTorch uses CUDA automatically. To try Apple Silicon acceleration, run `NOTEWRIGHT_DEVICE=mps python -m notewright`.

## How it works

| Step | Model or method |
| --- | --- |
| Download | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Separate instruments | [Demucs](https://github.com/facebookresearch/demucs) `htdemucs_6s` (MIT) |
| Recognise instruments, regroup parts | [PANNs](https://github.com/qiuqiangkong/panns_inference) sound-event model, Kong et al. 2020 (MIT), plus note-content matching between parts |
| Horns (sax, trumpet, trombone) | One melodic line from the horn part, divided between the horns by register and phrase |
| Piano and horns together | The piano is transcribed twice, from its own part and from piano and horn parts together with the horn line filtered out (a loud horn hides quiet comping), and the notes are combined; then each piano note's harmonics are filtered out of the horn audio (score-informed separation), and notes that start with a piano chord and die away are dropped from the horn line |
| Piano, guitar, other | [High-resolution piano transcription](https://github.com/qiuqiangkong/piano_transcription_inference), Kong et al. 2021 (MIT) |
| Bass, vocal melody | Probabilistic YIN pitch tracking ([librosa](https://librosa.org)) plus onset detection |
| Drums | Onset detection plus a three-band energy split into kick, snare and hi-hat |
| Beats, downbeats, meter | [Beat This!](https://github.com/CPJKU/beat_this), Foscarin et al. 2024 (MIT) |
| Sheet music | Per-beat rhythm quantisation (straight or triplet), hand splitting and [music21](https://github.com/cuthbertLab/music21) (BSD-3-Clause) |
| Rendering and sound | [OpenSheetMusicDisplay](https://opensheetmusicdisplay.org) (BSD-3-Clause) and [smplr](https://github.com/danigb/smplr) (MIT) |

The sheet music is written on the beat grid detected in the recording, so tempo changes and rubato don't push the bar lines out of place. The player uses the same grid to move the cursor.

## Tips

- **Piano with a band** (jazz, pop): use full band mode, or press "Separate instruments" afterwards. The piano model then hears the piano on its own, so it picks up much more of the quiet voicings than it does from the full mix.

## Limits

- **Piano** is the strongest part: the model was trained on concert piano recordings.
- **Horns** share one separated recording part: no open-source separator splits a sax from a trumpet. Their note tracks are separate, divided by register (trumpet sits above tenor sax) and smoothed over whole solos. In unison passages the line goes to one of them.
- **Instrument recognition** is a suggestion. Old or dull recordings can hide an instrument, so check the line-up and correct it.
- **Guitar and "other"** reuse the piano model. That works reasonably for plucked and struck instruments and less well for distorted guitar or pads.
- **Bass and vocals** are tracked as single melodic lines. Chords sung by a choir become one line.
- **Drums** come out as kick, snare and hi-hat only.
- **Sheet music** is a starting point, not finished engraving. It uses one voice per staff, and it does not detect meter changes in the middle of a song.
- **YouTube downloads** are for music you have the right to use.

## Development

```bash
python -m pytest
```

The web player is plain JavaScript modules in `web/`, with no build step. `window.notewright.player` is exposed for poking at playback from the browser console.

## License

MIT. Libraries and models keep their own licenses, listed above and in `web/vendor/README.md`. `notewright/audioset_labels.csv` is Google's AudioSet label list (CC BY 4.0).
