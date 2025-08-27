# Stream247_GUI.py — GUI YouTube 24/7 streamer
# - Uses yt-dlp.exe (next to the EXE) for playlist IDs / titles / direct URLs
# - Auto-selects NVENC > QSV > AMF > x264 via safe probe
# - Runs ffmpeg and yt-dlp with hidden windows (no console)
# - Clean Start/Stop (kills child procs, thread-safe)

import os, sys, time, random, shutil, subprocess, threading
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

APP_NAME = "Stream247"
IS_WIN = (os.name == "nt")
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
STARTUPINFO = None
if IS_WIN:
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # hide windows

# ---------- utilities ----------
def resource_path(name: str) -> str:
    """Resolve a bundled resource (PyInstaller) or local file."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
        if p.exists():
            return str(p)
    return str(Path.cwd() / name)

def find_binary(candidates: List[str]) -> Optional[str]:
    """Find an executable in PATH, bundle, or current dir."""
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    for c in candidates:
        rp = resource_path(c)
        if Path(rp).exists():
            return rp
    return None

def find_ffmpeg() -> Optional[str]:
    return find_binary(["ffmpeg", "ffmpeg.exe"])

def find_ytdlp() -> Optional[str]:
    # User will place yt-dlp.exe next to the EXE (or in PATH)
    return find_binary(["yt-dlp.exe", "yt-dlp"])

def run_hidden(cmd: List[str], check=False, capture=True, text=True, timeout=None) -> subprocess.CompletedProcess:
    """Run a command with no visible window; optionally capture output."""
    kwargs = dict(
        startupinfo=STARTUPINFO,
        creationflags=CREATE_NO_WINDOW,
    )
    if capture:
        kwargs.update(dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text))
    return subprocess.run(cmd, check=check, timeout=timeout, **kwargs)

def safe_write_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8", errors="ignore")
    except Exception:
        pass

def ffprobe_encoder(ffmpeg_path: str, codec: str) -> bool:
    """Try a tiny encode at a safe size; NVENC needs > tiny (use 320x180)."""
    try:
        null = "NUL" if IS_WIN else "/dev/null"
        cmd = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=black:s=320x180:rate=30",
            "-t", "0.2", "-c:v", codec, "-f", "null", null
        ]
        return run_hidden(cmd).returncode == 0
    except Exception:
        return False

# ---------- streaming core ----------
@dataclass
class StreamConfig:
    playlist_url: str
    stream_key: str
    rtmp_base: str = "rtmp://a.rtmp.youtube.com/live2"
    fps: int = 30
    height: int = 720
    video_bitrate: str = "2300k"
    bufsize: str = "4600k"
    audio_bitrate: str = "128k"
    overlay_titles: bool = True
    shuffle: bool = False
    sleep_between: int = 3
    fontfile: str = r"C:\Windows\Fonts\arial.ttf"
    title_file: str = "current_title.txt"

    # runtime-selected
    encoder: str = "libx264"
    encoder_name: str = "CPU x264"
    pix_fmt: str = "yuv420p"
    extra_venc_flags: List[str] = None  # type: ignore

    def rtmp_url(self) -> str:
        return f"{self.rtmp_base}/{self.stream_key}"

class StreamWorker(QtCore.QObject):
    log = QtCore.Signal(str)
    status = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, cfg: StreamConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._stop = threading.Event()
        self.ffmpeg_path = find_ffmpeg()
        self.ytdlp_path = find_ytdlp()
        self.ff_proc: Optional[subprocess.Popen] = None

    # ---- control ----
    def stop(self):
        self._stop.set()
        try:
            if self.ff_proc and self.ff_proc.poll() is None:
                self.ff_proc.kill()
        except Exception:
            pass

    # ---- yt-dlp.exe helpers ----
    def get_video_ids(self, playlist_url: str) -> List[str]:
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp.exe not found. Put it next to the EXE or in PATH.")
        cmd = [self.ytdlp_path, "--ignore-errors", "--flat-playlist", "--get-id", playlist_url]
        cp = run_hidden(cmd)
        if cp.returncode != 0:
            raise RuntimeError(f"yt-dlp error: {cp.stderr.strip()}")
        ids = [line.strip() for line in (cp.stdout or "").splitlines() if line.strip()]
        return ids

    def get_title(self, video_id: str) -> str:
        if not self.ytdlp_path:
            return f"https://www.youtube.com/watch?v={video_id}"
        url = f"https://www.youtube.com/watch?v={video_id}"
        cp = run_hidden([self.ytdlp_path, "--get-title", url])
        if cp.returncode == 0 and cp.stdout:
            return cp.stdout.strip()
        return url

    def get_stream_urls(self, video_id: str) -> (str, Optional[str]):
        """
        Use yt-dlp.exe to emit direct URLs:
          - If progressive exists: single URL (audio+video)
          - else: returns (video_url, audio_url)
        """
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp.exe not found.")
        url = f"https://www.youtube.com/watch?v={video_id}"
        # Ask yt-dlp to print direct URLs. We try best video+audio; if progressive,
        # yt-dlp will still output one or two lines depending on format selection.
        # Use modern selection to prefer h264 streams when possible.
        fmt = "bv*+ba/best"
        cp = run_hidden([self.ytdlp_path, "-g", "-f", fmt, url])
        if cp.returncode != 0:
            raise RuntimeError(f"yt-dlp -g failed: {cp.stderr.strip()}")

        lines = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError("No playable formats found.")
        if len(lines) == 1:
            return lines[0], None
        # when two lines: first is video, second is audio
        return lines[0], lines[1]

    # ---- encoder selection ----
    def select_encoder(self):
        # Defaults
        self.cfg.encoder = "libx264"
        self.cfg.encoder_name = "CPU x264"
        self.cfg.pix_fmt = "yuv420p"
        self.cfg.extra_venc_flags = ["-preset", "veryfast"]

        if not self.ffmpeg_path:
            return

        # NVENC → QSV → AMF
        if ffprobe_encoder(self.ffmpeg_path, "h264_nvenc"):
            self.cfg.encoder = "h264_nvenc"
            self.cfg.encoder_name = "NVIDIA NVENC"
            self.cfg.pix_fmt = "yuv420p"
            self.cfg.extra_venc_flags = [
                "-preset", "p4", "-rc", "cbr_hq", "-tune", "hq",
                "-spatial_aq", "1", "-temporal_aq", "1", "-aq-strength", "8"
            ]
            return

        if ffprobe_encoder(self.ffmpeg_path, "h264_qsv"):
            self.cfg.encoder = "h264_qsv"
            self.cfg.encoder_name = "Intel Quick Sync"
            self.cfg.pix_fmt = "nv12"
            self.cfg.extra_venc_flags = ["-look_ahead", "1"]
            return

        if ffprobe_encoder(self.ffmpeg_path, "h264_amf"):
            self.cfg.encoder = "h264_amf"
            self.cfg.encoder_name = "AMD AMF"
            self.cfg.pix_fmt = "yuv420p"
            self.cfg.extra_venc_flags = ["-rc", "cbr", "-quality", "quality", "-usage", "transcoding"]
            return

    # ---- ffmpeg ----
    def build_ffmpeg_cmd(self, vurl: str, aurl: Optional[str]) -> List[str]:
        gop = self.cfg.fps * 2
        vf = [f"scale=-2:{self.cfg.height}:flags=bicubic"]
        if self.cfg.overlay_titles:
            fontfile = self.cfg.fontfile.replace(":", r"\:")
            vf.append(
                f"drawtext=fontfile='{fontfile}':textfile='{self.cfg.title_file}':reload=1:"
                f"fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:x=10:y=10"
            )
        vf.append(f"format={self.cfg.pix_fmt}")

        cmd = [
            self.ffmpeg_path or "ffmpeg",
            "-hide_banner", "-loglevel", "warning", "-stats",
            "-re", "-i", vurl,
        ]
        if aurl:
            cmd += ["-re", "-i", aurl]

        maps = ["-map", "0:v:0"]
        if aurl:
            maps += ["-map", "1:a:0"]
        else:
            maps += ["-map", "0:a:0?"]  # optional audio if progressive

        cmd += [
            *maps,
            "-c:v", self.cfg.encoder, *self.cfg.extra_venc_flags,
            "-fflags", "+genpts",
            "-r", str(self.cfg.fps), "-g", str(gop), "-keyint_min", str(gop),
            "-b:v", self.cfg.video_bitrate, "-maxrate", self.cfg.video_bitrate, "-bufsize", self.cfg.bufsize,
            "-vf", ",".join(vf),
            "-c:a", "aac", "-b:a", self.cfg.audio_bitrate, "-ar", "44100", "-ac", "2",
            "-f", "flv", self.cfg.rtmp_url()
        ]
        return cmd

    def run_one_video(self, video_id: str):
        # Get direct URLs (no yt-dlp subprocess piping, no console)
        vurl, aurl = self.get_stream_urls(video_id)
        ff_cmd = self.build_ffmpeg_cmd(vurl, aurl)
        self.log.emit(f"[CMD] ffmpeg: {' '.join(ff_cmd)}")

        # Start ffmpeg hidden; stream until it exits
        self.ff_proc = subprocess.Popen(
            ff_cmd,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            startupinfo=STARTUPINFO,
            creationflags=CREATE_NO_WINDOW
        )

        # pump ffmpeg log into the GUI console
        if self.ff_proc.stdout:
            for line in iter(self.ff_proc.stdout.readline, b""):
                if self._stop.is_set():
                    break
                try:
                    self.log.emit(line.decode("utf-8", errors="ignore").rstrip())
                except Exception:
                    pass

        self.ff_proc.wait()

    @QtCore.Slot()
    def run(self):
        if not self.ffmpeg_path:
            self.log.emit("[ERROR] ffmpeg not found. Put ffmpeg.exe next to the EXE or in PATH.")
            self.finished.emit()
            return
        if not self.ytdlp_path:
            self.log.emit("[ERROR] yt-dlp.exe not found. Put yt-dlp.exe next to the EXE or in PATH.")
            self.finished.emit()
            return

        self.select_encoder()
        self.status.emit("Starting…")
        self.log.emit(f"[INFO] Encoder: {self.cfg.encoder_name} ({self.cfg.encoder})")
        self.log.emit(f"[INFO] Playlist: {self.cfg.playlist_url}")
        self.log.emit(f"[INFO] RTMP:     {self.cfg.rtmp_url()}")
        self.log.emit(f"[INFO] Output:   {self.cfg.height}p@{self.cfg.fps}  ~{self.cfg.video_bitrate} video + {self.cfg.audio_bitrate} audio\n")

        while not self._stop.is_set():
            try:
                ids = self.get_video_ids(self.cfg.playlist_url)
                if not ids:
                    self.log.emit("[WARN] No IDs found; retrying in 30s…")
                    for _ in range(30):
                        if self._stop.is_set(): break
                        time.sleep(1)
                    continue
                if self.cfg.shuffle:
                    random.shuffle(ids)

                for idx, vid in enumerate(ids, 1):
                    if self._stop.is_set():
                        break

                    title = self.get_title(vid)
                    safe_write_text(Path(self.cfg.title_file), title)

                    self.log.emit("-" * 46)
                    self.log.emit(f"[INFO] Item #{idx} - {title}")
                    self.log.emit(f"[INFO] URL: https://www.youtube.com/watch?v={vid}")
                    self.log.emit("-" * 46)

                    try:
                        self.run_one_video(vid)
                    except Exception as e:
                        self.log.emit(f"[WARN] Stream error: {e}")

                    if self._stop.is_set():
                        break
                    if self.cfg.sleep_between > 0:
                        self.log.emit(f"[INFO] Sleeping {self.cfg.sleep_between}s…")
                        for _ in range(self.cfg.sleep_between):
                            if self._stop.is_set(): break
                            time.sleep(1)

                if self._stop.is_set():
                    break
                self.log.emit("\n[INFO] End of playlist. Refreshing IDs and looping…\n")

            except Exception as e:
                self.log.emit(f"[WARN] Loop error: {e}. Retrying in 30s…")
                for _ in range(30):
                    if self._stop.is_set(): break
                    time.sleep(1)

        self.status.emit("Stopped")
        self.finished.emit()

# ---------- GUI ----------
DARK_QSS = """
* { color: #e6e6e6; font-family: Segoe UI, Arial, sans-serif; }
QWidget { background: #111315; }
QLineEdit, QComboBox, QTextEdit, QSpinBox { background: #1a1d21; border: 1px solid #2a2f36; border-radius: 8px; padding: 6px; }
QPushButton { background: #2b6cb0; border: none; border-radius: 10px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #2f76c2; }
QPushButton:disabled { background: #2a2f36; color: #8a8f98; }
QGroupBox { border: 1px solid #2a2f36; border-radius: 10px; margin-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QCheckBox::indicator { width: 16px; height: 16px; }
"""

class MainWindow(QtWidgets.QWidget):
    startRequested = QtCore.Signal(StreamConfig)
    stopRequested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — YouTube 24/7 VOD Streamer")
        self.setMinimumSize(860, 620)
        self.worker_thread: Optional[QtCore.QThread] = None
        self.worker: Optional[StreamWorker] = None
        self.streaming = False

        # Inputs
        self.playlist_edit = QtWidgets.QLineEdit("")
        self.playlist_edit.setPlaceholderText("Your YouTube playlist URL…")
        self.key_edit = QtWidgets.QLineEdit("")
        self.key_edit.setPlaceholderText("Your YouTube stream key…")
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)

        self.res_combo = QtWidgets.QComboBox()
        self.res_combo.addItems(["720p30 (stable)", "720p60", "1080p30"])
        self.bitrate_edit = QtWidgets.QLineEdit("2300k")
        self.bufsize_edit = QtWidgets.QLineEdit("4600k")

        self.overlay_chk = QtWidgets.QCheckBox("Overlay current VOD title")
        self.overlay_chk.setChecked(True)
        self.shuffle_chk = QtWidgets.QCheckBox("Shuffle playlist order")
        self.console_chk = QtWidgets.QCheckBox("Show console")
        self.console_chk.setChecked(True)

        self.console = QtWidgets.QTextEdit()
        self.console.setReadOnly(True)
        self.console.setVisible(True)

        self.start_btn = QtWidgets.QPushButton("Start Stream")
        self.stop_btn = QtWidgets.QPushButton("Stop Stream")
        self.stop_btn.setEnabled(False)

        # Layout
        form = QtWidgets.QGridLayout()
        form.addWidget(QtWidgets.QLabel("Playlist URL"), 0, 0)
        form.addWidget(self.playlist_edit, 0, 1, 1, 3)
        form.addWidget(QtWidgets.QLabel("Stream Key"), 1, 0)
        form.addWidget(self.key_edit, 1, 1, 1, 3)

        form.addWidget(QtWidgets.QLabel("Quality"), 2, 0)
        form.addWidget(self.res_combo, 2, 1)
        form.addWidget(QtWidgets.QLabel("Video Bitrate"), 2, 2)
        form.addWidget(self.bitrate_edit, 2, 3)

        form.addWidget(QtWidgets.QLabel("Buffer Size"), 3, 2)
        form.addWidget(self.bufsize_edit, 3, 3)

        toggles = QtWidgets.QHBoxLayout()
        toggles.addWidget(self.overlay_chk)
        toggles.addWidget(self.shuffle_chk)
        toggles.addStretch(1)
        toggles.addWidget(self.console_chk)

        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        btns.addStretch(1)

        v = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("Loop your public YouTube VOD playlist to YouTube Live 24/7. Auto-encoder picks NVENC/QSV/AMF/x264.")
        header.setWordWrap(True)
        header.setStyleSheet("font-size:14px; color:#b9c2cf;")
        v.addWidget(header)
        v.addLayout(form)
        v.addLayout(toggles)
        v.addLayout(btns)
        v.addWidget(self.console, 1)

        # Signals
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        self.console_chk.toggled.connect(self.console.setVisible)
        self.res_combo.currentIndexChanged.connect(self.on_quality_change)

        self.on_quality_change()

    # --- UI helpers ---
    def append_log(self, text: str):
        self.console.append(text)
        self.console.moveCursor(QtGui.QTextCursor.End)

    def on_quality_change(self):
        choice = self.res_combo.currentText()
        if "720p30" in choice:
            self._fps = 30; self._height = 720
            if not self.streaming:
                self.bitrate_edit.setText("2300k"); self.bufsize_edit.setText("4600k")
        elif "720p60" in choice:
            self._fps = 60; self._height = 720
            if not self.streaming:
                self.bitrate_edit.setText("3200k"); self.bufsize_edit.setText("6400k")
        else:  # 1080p30
            self._fps = 30; self._height = 1080
            if not self.streaming:
                self.bitrate_edit.setText("4500k"); self.bufsize_edit.setText("9000k")

    def make_config(self) -> StreamConfig:
        return StreamConfig(
            playlist_url=self.playlist_edit.text().strip(),
            stream_key=self.key_edit.text().strip(),
            fps=self._fps,
            height=self._height,
            video_bitrate=self.bitrate_edit.text().strip(),
            bufsize=self.bufsize_edit.text().strip(),
            audio_bitrate="128k",
            overlay_titles=self.overlay_chk.isChecked(),
            shuffle=self.shuffle_chk.isChecked(),
            sleep_between=3,
            fontfile=r"C:\Windows\Fonts\arial.ttf",
            title_file="current_title.txt",
        )

    # --- start/stop wiring ---
    def on_start(self):
        if self.streaming:
            return
        cfg = self.make_config()
        if not cfg.playlist_url or not cfg.stream_key:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Please enter Playlist URL and Stream Key.")
            return

        self.streaming = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.append_log("[INFO] Starting stream…")

        self.worker_thread = QtCore.QThread(self)
        self.worker = StreamWorker(cfg)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.status.connect(lambda s: self.append_log(f"[STATUS] {s}"))
        self.worker.finished.connect(self.on_finished)
        self.stopRequested.connect(self.worker.stop)

        self.worker_thread.start()

    def on_stop(self):
        if not self.streaming:
            return
        self.append_log("[INFO] Stopping…")
        self.stopRequested.emit()
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait(5000)

    def on_finished(self):
        self.append_log("[INFO] Worker finished.")
        try:
            if self.worker_thread:
                self.worker_thread.quit()
                self.worker_thread.wait(5000)
        except Exception:
            pass
        self.worker = None
        self.worker_thread = None
        self.streaming = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

# ---------- entry ----------
def main():
    # (Optional DPI flag; safe to omit if it warns)
    # QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    w = MainWindow()
    w.resize(980, 660)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
