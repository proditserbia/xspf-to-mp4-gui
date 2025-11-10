#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XSPF → MP4 (Windows) – local converter with tiny GUI

- Expects ffmpeg.exe in the SAME folder as this script/.exe (no install).
- Pick a single .xspf OR process all .xspf files in an "input" folder.
- Audio-only items are wrapped in a black 1080p30 video segment (AAC).
- All segments normalized to H.264 (yuv420p) + AAC @ 1080p30, then concatenated.
- Outputs one MP4 per playlist into an "output" folder.

Tested on Windows with Python 3.9+.
"""

import os
import re
import sys
import shutil
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, unquote
from pathlib import Path
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# -------------------- CONFIG --------------------
TARGET_WIDTH   = 1920       # output width (keeps AR with -2)
TARGET_FPS     = 30         # output fps
VIDEO_CRF      = 19         # lower = better quality / bigger file
VIDEO_PRESET   = "veryfast" # x264 preset
AUDIO_BR       = "192k"     # output audio bitrate
PAD_COLOR      = "black"    # background for audio-only items

APP_TITLE      = "XSPF → MP4 Converter"
FFMPEG_NAME    = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
# ------------------------------------------------

NS = {"x": "http://xspf.org/ns/0/"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}

def get_base_dir() -> Path:
    """Folder pored .exe kada je frozen, ili folder .py skripte u dev modu."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()

BASE_DIR = get_base_dir()

def find_ffmpeg(base_dir: Path) -> Path:
    cand = base_dir / FFMPEG_NAME
    return cand if cand.exists() else Path(FFMPEG_NAME)  # PATH fallback

FFMPEG = find_ffmpeg(BASE_DIR)

def uri_to_windows_path(uri: str) -> str:
    """
    Convert file:///C:/Users/... to Windows path C:\\Users\\...
    If already a Windows path, return as-is.
    """
    if not uri:
        return ""
    u = uri.strip()
    if re.match(r"^[A-Za-z]:[\\/]", u):
        return u.replace("/", "\\")
    if u.lower().startswith("file://"):
        parsed = urlparse(u)
        path = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return path.replace("/", "\\")
    return u

def parse_xspf(xspf_path: Path):
    tracks = []
    try:
        tree = ET.parse(str(xspf_path))
        root = tree.getroot()
        for i, tr in enumerate(root.findall(".//x:trackList/x:track", NS), start=1):
            loc = tr.findtext("x:location", default="", namespaces=NS).strip()
            title = tr.findtext("x:title", default="", namespaces=NS).strip() or None
            dur = tr.findtext("x:duration", default="", namespaces=NS).strip() or None
            win_path = uri_to_windows_path(loc)
            ext = Path(win_path).suffix.lower()
            if ext in AUDIO_EXT:
                mtype = "audio"
            elif ext in VIDEO_EXT:
                mtype = "video"
            else:
                mtype = "unknown"
            tracks.append({
                "index": i,
                "location": loc,
                "path": win_path,
                "title": title,
                "duration_ms": int(dur) if dur and dur.isdigit() else None,
                "type": mtype,
                "ext": ext or "",
            })
    except ET.ParseError as e:
        raise RuntimeError(f"XML parse error: {e}")
    return tracks

def run_ffmpeg(args, log, cwd=None):
    """Pokreni FFmpeg i SAKRIJ prozor na Windowsu; čitaj stdout u log."""
    # Hide console window for ffmpeg on Windows
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    for line in proc.stdout:
        if log:
            log(line.rstrip())
    return proc.wait()

def prepare_segment(ffmpeg: Path, input_path: Path, out_path: Path, media_type: str, log):
    """Audio -> black 1080p30 + AAC ; Video -> normalize to 1080p30 H.264/AAC"""
    if media_type == "audio":
        cmd = [
            str(ffmpeg), "-y",
            "-f", "lavfi", "-i", f"color=size={TARGET_WIDTH}x1080:rate={TARGET_FPS}:color={PAD_COLOR}",
            "-i", str(input_path),
            "-shortest",
            "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF),
            "-pix_fmt", "yuv420p", "-r", str(TARGET_FPS),
            "-c:a", "aac", "-b:a", AUDIO_BR,
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        vf = f"scale={TARGET_WIDTH}:-2:flags=lanczos,format=yuv420p,fps={TARGET_FPS}"
        cmd = [
            str(ffmpeg), "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF),
            "-c:a", "aac", "-b:a", AUDIO_BR,
            "-movflags", "+faststart",
            str(out_path),
        ]
    return run_ffmpeg(cmd, log)

def concat_segments(ffmpeg: Path, parts, out_mp4: Path, log):
    """Concat bez re-enkoda (copy), jer su svi segmenti ujednačeni"""
    concat_txt = out_mp4.parent / "concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in parts:
            # concat demuxer voli forward slashes
            f.write(f"file '{p.as_posix()}'\n")
    cmd = [str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(out_mp4)]
    return run_ffmpeg(cmd, log)

def convert_playlist(xspf_path: Path, output_dir: Path, ffmpeg: Path, log, progress=None):
    if log:
        log(f"==> Converting: {xspf_path}")
    tracks = parse_xspf(xspf_path)
    if not tracks:
        raise RuntimeError("No tracks found in playlist.")

    # Validacija postojanja fajlova
    missing = [t for t in tracks if not Path(t["path"]).exists()]
    if missing:
        msg = "Missing files:\n" + "\n".join(f"  [{m['index']}] {m['path']}" for m in missing[:50])
        raise FileNotFoundError(msg)

    work_dir = output_dir / f"{xspf_path.stem}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    total = len(tracks)
    for idx, tr in enumerate(tracks, start=1):
        if progress:
            progress(idx - 1, total)
        ip = Path(tr["path"])
        seg_out = work_dir / f"part_{idx:03d}.mp4"
        ttype = tr["type"]
        if ttype not in {"audio", "video"}:
            raise RuntimeError(f"Unsupported item type for: {ip}")
        if log:
            log(f"- [{idx}/{total}] {ttype.upper()}: {ip}")
        rc = prepare_segment(ffmpeg, ip, seg_out, ttype, log)
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed on item {idx} ({ip}) with code {rc}")
        parts.append(seg_out)

    if progress:
        progress(total, total)

    out_mp4 = output_dir / f"{xspf_path.stem}.mp4"
    if log:
        log("Concatenating segments…")
    rc = concat_segments(ffmpeg, parts, out_mp4, log)
    if rc != 0:
        raise RuntimeError(f"Concatenation failed with code {rc}")
    if log:
        log(f"✅ Done: {out_mp4}")
    return out_mp4

# -------------------- GUI --------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("780x540")
        self.resizable(True, True)

        self.base_dir = BASE_DIR
        self.ffmpeg   = FFMPEG

        # ensure folders exist beside .exe/.py
        (self.base_dir / "input").mkdir(exist_ok=True)
        (self.base_dir / "output").mkdir(exist_ok=True)

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.status = tk.StringVar(
            value=f"ffmpeg: {self.ffmpeg} ({'found' if self.ffmpeg.exists() else 'PATH'})"
        )

        row1 = ttk.Frame(frm)
        row1.pack(fill="x", pady=(0,10))
        ttk.Button(row1, text="Convert XSPF…", command=self.on_pick_xspf).pack(side="left")
        ttk.Button(row1, text="Process 'input' folder", command=self.on_process_folder).pack(side="left", padx=6)
        ttk.Label(row1, textvariable=self.status).pack(side="left", padx=12)

        self.pb = ttk.Progressbar(frm, mode="determinate")
        self.pb.pack(fill="x")

        self.text = tk.Text(frm, height=20, wrap="word")
        self.text.pack(fill="both", expand=True, pady=(10,0))
        self.text.tag_configure("mono", font=("Consolas", 9))

    def log(self, msg: str):
        self.text.insert("end", msg + "\n", ("mono",))
        self.text.see("end")
        self.update_idletasks()

    def set_progress(self, cur, total):
        self.pb["maximum"] = max(1, total)
        self.pb["value"]   = cur
        self.update_idletasks()

    def run_convert(self, xspf_path: Path):
        out_dir = self.base_dir / "output"
        def worker():
            try:
                out = convert_playlist(
                    xspf_path=xspf_path,
                    output_dir=out_dir,
                    ffmpeg=self.ffmpeg,
                    log=self.log,
                    progress=self.set_progress,
                )
                messagebox.showinfo("Done", f"Output saved:\n{out}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        threading.Thread(target=worker, daemon=True).start()

    def on_pick_xspf(self):
        if not self._check_ffmpeg():
            return
        path = filedialog.askopenfilename(
            title="Choose .xspf playlist",
            filetypes=[("XSPF playlist", "*.xspf"), ("All files", "*.*")]
        )
        if not path:
            return
        self.text.delete("1.0", "end")
        self.set_progress(0, 1)
        self.run_convert(Path(path))

    def on_process_folder(self):
        if not self._check_ffmpeg():
            return
        self.text.delete("1.0", "end")
        self.set_progress(0, 1)
        xspfs = sorted((self.base_dir / "input").glob("*.xspf"))
        if not xspfs:
            messagebox.showwarning("No files", "Place .xspf files into the 'input' folder and try again.")
            return
        def worker():
            for x in xspfs:
                try:
                    self.log(f"\n=== Processing: {x.name} ===")
                    convert_playlist(x, self.base_dir / "output", self.ffmpeg, self.log, self.set_progress)
                except Exception as e:
                    self.log(f"ERROR: {e}")
            messagebox.showinfo("Done", "Finished processing all playlists. Check the 'output' folder.")
        threading.Thread(target=worker, daemon=True).start()

    def _check_ffmpeg(self) -> bool:
        if self.ffmpeg.exists():
            return True
        # PATH fallback check
        if shutil.which(FFMPEG_NAME):
            self.ffmpeg = Path(shutil.which(FFMPEG_NAME))
            self.status.set(f"ffmpeg: {self.ffmpeg} (PATH)")
            return True
        messagebox.showerror("Missing ffmpeg", "Place ffmpeg.exe next to this program, then try again.")
        return False

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
