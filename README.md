# FPS RePlayer  (v1.0.0)

English | [日本語](README.ja.md)

A Windows desktop video player for **frame-by-frame review of FPS gameplay clips**
(originally built for reviewing *Escape from Tarkov* recordings). Step through footage
one frame at a time in both directions, magnify any spot with a cursor-following lens,
and cut out the interesting moment as a **9:16 vertical clip**.

It was made because no off-the-shelf player (PotPlayer / GOM / VLC / KMPlayer) offered
**both** bidirectional frame stepping **and** a cursor-following magnifier in one app.

## Download (just run it)

1. Go to the [**Releases**](../../releases) page and download `FPSRePlayer-vX.Y.Z-win64.zip`
2. Unzip the whole folder
3. Double-click **`FPSRePlayer.exe`** — no Python or installation needed

> On first launch, Windows SmartScreen may show *"Windows protected your PC"*.
> Click **More info → Run anyway** (the app is unsigned).

## Features & controls

| Action | How |
|--------|-----|
| Play / Pause | `Space` / button / **left-click the player** (ignored if you drag) |
| Step back (1 frame) | `←` / `A`. Hold the button for continuous stepping |
| Step forward (1 frame) | `→` / `D`. Hold the button for continuous stepping |
| Speed up / down | `↑` / `↓`, or the **mouse wheel** (0.1×–16×) |
| Reset to 1× | **Middle-click** (wheel click) |
| Magnifier (cursor-following) | **Ctrl + mouse wheel** to zoom; the white frame only appears above 1× (up to 20×) |
| Seek | Click the timeline (filmstrip / waveform) at the bottom; does not stop playback |
| Volume | Volume slider in the playback controls (works before opening a file) |

- The magnifier always follows the cursor (no toggle); at 1× nothing is drawn.
- Current frame number, time, fps, speed and zoom are always shown.
- Audio follows slow/fast playback (pitch changes with speed).
- All shortcuts (keyboard **and** mouse) are rebindable in the ⚙ Settings dialog.

### Timeline (also the seek bar)

The bottom shows a **filmstrip of thumbnails** and an **audio waveform**. Both act as the
seek bar (loud moments such as gunshots show up as waveform spikes).

- Click / drag: seek
- **Ctrl + click: set In point**
- **Alt + click: set Out point**

### In / Out (time range)

Set with `I` / `O` (current position) or Ctrl/Alt-click on the timeline. The range is shown
in yellow and used as the export time range (whole clip if unset). "Clear" removes it.

### Vertical export

1. Click **Export vertical** → a **9:16 frame** appears on the video
2. Drag inside it to **move**, drag the corners to **resize** (always kept at 9:16)
3. (Optional) limit the time range with `I` / `O`
4. Click **Export this range** → choose resolution / audio and save

The frame is 9:16, so the output is a clean vertical video (H.264 + AAC) with no distortion
or black bars.

## Language

English / Japanese. The initial language follows your Windows locale; you can switch
between Auto / English / 日本語 in the ⚙ Settings dialog (applied instantly and saved).

## Supported formats

Powered by PyAV (bundled FFmpeg) — MP4 / MKV / MOV / AVI / WebM / FLV / TS / WMV and most
codecs (H.264, H.265/HEVC, AV1, VP9, etc.). No separate FFmpeg install required.

## Run from source

- Python 3.12
- Install dependencies:

```
python -m pip install -r requirements.txt
```

- Launch: double-click `run_player.bat`, or `python src\app.py` (or `python src\app.py "video path"`)

## Build the distributable exe

Run `build_exe.bat`, or:

```
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --windowed --name "FPSRePlayer" --collect-all av src\app.py
```

Output goes to `dist\FPSRePlayer\` — distribute the whole folder (zip it and attach to a
GitHub Release). `--collect-all av` bundles FFmpeg, so target PCs need nothing installed.

## Project layout

```
src/
  app.py            entry point
  main_window.py    UI, controls, playback
  video_widget.py   rendering, magnifier, crop frame
  reader.py         PyAV frame-accurate seeking + frame cache
  player_engine.py  background prefetch decoder (smooth playback)
  audio_player.py   audio output (QAudioSink), varispeed
  timeline.py       filmstrip + waveform seek bar
  exporter.py       crop → scale → pad vertical export (worker thread)
  shortcuts.py      rebindable key/mouse settings dialog
  i18n.py           English / Japanese strings
assets/             app icon
tools/              icon generation scripts
```

## Notes

- Backward stepping seeks to a keyframe and decodes forward (handles inter-frame
  compression); recently decoded frames are cached so repeated stepping stays smooth.
  Designed for short clips (a few minutes).
- Playback uses a background decode thread that prefetches frames into a queue; the UI
  just pulls and draws in sync with the audio clock, so decode time doesn't cause stutter.
- Export uses a PyAV filter graph (`crop → scale(decrease) → pad`) with timestamps
  rebased to 0 so the clip starts at its beginning.

## License

MIT — see [LICENSE](LICENSE).
