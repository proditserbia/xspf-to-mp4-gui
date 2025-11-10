XSPF → MP4 (Windows) – Quick Guide
==================================

Contents:
- xspf_to_mp4_gui.py  (Python GUI script)
- ffmpeg.exe          (place in the same folder as the script)
- input/              (put your .xspf files here if you want batch mode)
- output/             (results will be written here)

How to run:
1) Make sure Python 3.9+ is installed.
2) Put ffmpeg.exe next to the script (or ensure ffmpeg is in PATH).
3) Double-click run.bat OR run `py xspf_to_mp4_gui.py` from Command Prompt.

Usage:
- Click “Convert XSPF…” to select a single .xspf file, OR
- Click “Process 'input' folder” to convert all .xspf files placed in the `input` folder.

What it does:
- Reads your .xspf playlist and finds local file paths.
- Converts audio-only items into black 1080p30 video segments with AAC audio.
- Normalizes all segments to H.264 (yuv420p) + AAC at 1080p30.
- Concatenates segments into one MP4 per playlist with `-movflags +faststart`.

Notes:
- Missing files will be reported; verify the paths exist on your machine.
- If you need different resolution/FPS/bitrate, these can be adjusted easily.
