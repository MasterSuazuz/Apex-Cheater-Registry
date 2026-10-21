# Apex Cheater Registry

[Русский](README.ru.md) · **English**

A Windows app that records Apex Legends like a replay buffer. Press F8 and it clips the moment and
files it into a dossier on whoever is on screen: the player you are spectating, or you. You decide
whether it is cheating by rating each moment from 1 to 5.

The app **only records the screen and audio** (like OBS). It never reads game memory or injects into
the game.

## Requirements

- Windows 10/11
- Python 3.11+ ([python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH")
- An NVIDIA GPU is optional. With one, clips are encoded with NVENC; without one, x264 on the CPU is used.

## Getting started

```bash
git clone https://github.com/MasterSuazuz/Apex-Cheater-Regisry.git
```

Run `run.bat`. On first launch it creates a virtual environment and installs the dependencies, which
takes a few minutes. It also downloads the legend portraits from the Apex Legends wiki, which takes
about 30 seconds.

For the in-game overlay, set Apex to **Borderless** or **Windowed**. Windows cannot draw over
exclusive fullscreen.

## How it works

1. **F9 starts/stops recording** (or use the button on the Live page). Recording never starts on its
   own. It works like NVIDIA's instant replay: only the last N seconds of video and audio are kept
   on disk, and anything older is erased automatically. **Set N in the first card of Settings,
   "Replay"** (default 30 s).
2. **F8 saves a moment.** At the moment you press it, the app reads the screen:
   - **Spectator mode** ("Live" → "Spectate" tabs): it reads the name from the red banner, the
     **identifier** from the "Identifier: …" line (the dossier's main key), the teammates and the
     legend, and the moment goes into that player's dossier. The clip never starts before that
     player came on screen.
   - **Your own match**: the moment goes into your own dossier tagged **[OWNER]**. It is created
     once, and your name is taken from the squad list.
3. **The dossier is where all editing happens**: name, ID, legend, tags, a rating and a note for each
   moment, the legend for each moment, the report text and export.
   - Video plays with volume control and in **fullscreen** (button or double-click). Esc exits, Space
     pauses, ← → skip 5 seconds.
   - **Trimming uses the ✂ button**. The 1st click puts a [ bracket on the timeline, the 2nd puts a ],
     and the 3rd trims. Drag the brackets with the mouse to adjust them, or press ✕ to remove them.
     The cut part is deleted from the dossier, so the file gets smaller.

## Legends

Legend portraits are downloaded from the Apex Legends wiki on first launch into `assets/legends`.
They are not part of this repository because they are game art. The app recognizes the legend from
the portrait in the squad list right away, without any training.

Every moment in a dossier shows its legend icon, and you can filter moments by legend. When a new
legend comes out, use Settings → "Download new legends". If the app gets a legend wrong, correct it
in the dossier and it will remember.

## Tags and pinning

Dossier tags are **OWNER** (yours), **CHEATER**, **PRO** and **NOOB**. You set them with buttons in
the dossier and can filter the list by them. ★ pins a dossier to the top of the list.

## Audio

In Settings → "Audio" you can record game audio (everything you hear in your headphones or speakers,
voice chat included) and your microphone. You can also choose the devices and set the microphone
volume.

## Moment rating

| | |
|---|---|
| 1 | movement / macro play |
| 2 | no cheat |
| 3 | good shooting |
| 4 | suspicious |
| 5 | cheating |

Moments rated 4 or 5 go into the EA report text.

## Language

The app is available in Russian and English. Change it in Settings → Language; the change applies
after a restart.

## The .apexcase file

An .apexcase file is a plain ZIP. It contains:

- `timeline.txt`
- `report.html` (the videos play in it once the archive is extracted)
- `ea_report.txt`
- `clips/*.mp4` (video with audio)
- `frames/*.jpg`
- `portrait.png`
- `manifest.json`

Files are named `<name>_ID<identifier>.apexcase`.

## Project layout

| Path | What it does |
|---|---|
| `main.py` | Entry point |
| `cheatdetector/capture.py` | Screen capture (DXGI / GDI), ffmpeg ring buffer, clips, trimming |
| `cheatdetector/audio.py` | Game audio (WASAPI loopback) and microphone |
| `cheatdetector/screenreader.py` | Reads the spectator screen and your HUD (Windows OCR) |
| `cheatdetector/identify.py` | Tracks who is on screen and switches dossiers |
| `cheatdetector/legends.py`, `legend_icons.py` | Legend recognition by portrait |
| `cheatdetector/casefile.py` | The .apexcase dossier format |
| `cheatdetector/ui/` | Interface (PySide6 / Qt) |
