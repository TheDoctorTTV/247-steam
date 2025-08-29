# Stream247_GUI.py — GUI YouTube 24/7 streamer
# - Uses yt-dlp.exe (next to the EXE) for playlist IDs / titles / direct URLs
# - Auto-selects NVENC > QSV > AMF > x264 via safe probe
# - Runs ffmpeg and yt-dlp with hidden windows (no console)
# - Clean Start/Stop (kills ffmpeg reliably; Windows fallback uses taskkill /T /F)
# - Saves config to config.json next to the EXE
# - Overlay shows: "<TITLE> • <Pretty Date>" with title truncation (date preserved)

import os, sys, time, json, random, shutil, subprocess, threading, datetime
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets

APP_NAME = "Stream247"
IS_WIN = (os.name == "nt")
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WIN else 0

STARTUPINFO = None
if IS_WIN:
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # hide windows

# ---------- config.json helpers ----------
def _app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).parent  # packaged exe folder
    return Path.cwd()                       # running from source

CONFIG_PATH = _app_dir() / "config.json"

def load_config_json() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_config_json(data: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

# ---------- misc utilities ----------
def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
    p = Path(base) / name
    if p.exists():
        return str(p)
    return str(Path.cwd() / name)

def find_binary(candidates: List[str]) -> Optional[str]:
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
    return find_binary(["yt-dlp.exe", "yt-dlp"])

def run_hidden(cmd: List[str], check=False, capture=True, text=True, timeout=None) -> subprocess.CompletedProcess:
    kwargs = dict(startupinfo=STARTUPINFO, creationflags=CREATE_NO_WINDOW)
    if capture:
        kwargs.update(dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text))
    return subprocess.run(cmd, check=check, timeout=timeout, **kwargs)

def safe_write_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8", errors="ignore")
    except Exception:
        pass

def ffprobe_encoder(ffmpeg_path: str, codec: str) -> bool:
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

def fmt_yt_date(upload_date: Optional[str], timestamp: Optional[int], release_ts: Optional[int]) -> Optional[str]:
    dt = None
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        try:
            dt = datetime.datetime.strptime(upload_date, "%Y%m%d")
        except Exception:
            dt = None
    if dt is None:
        ts = release_ts or timestamp
        if ts:
            try:
                dt = datetime.datetime.fromtimestamp(int(ts))
            except Exception:
                dt = None
    if dt is None:
        return None
    return dt.strftime("%b %#d, %Y") if IS_WIN else dt.strftime("%b %-d, %Y")


def default_font_path() -> str:
    """Return a reasonable default font path for the current OS."""
    if IS_WIN:
        candidates = [
            r"C:\\Windows\\Fonts\\arial.ttf",
            r"C:\\Windows\\Fonts\\Arial.ttf",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    else:  # Linux and others
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    for c in candidates:
        if Path(c).exists():
            return c
    return ""

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
    sleep_between: int = 0
    fontfile: str = field(default_factory=default_font_path)
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

    ff_proc: Optional[subprocess.Popen]

    def __init__(self, cfg: StreamConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._stop = threading.Event()
        self._skip = threading.Event()
        self.ffmpeg_path = find_ffmpeg()
        self.ytdlp_path = find_ytdlp()
        self.ff_proc = None

    # ---------- control ----------
    def stop(self):
        self._stop.set()
        self.log.emit("[INFO] Stop requested — killing ffmpeg…")
        try:
            if self.ff_proc and self.ff_proc.poll() is None:
                try:
                    self.ff_proc.terminate()
                    self.ff_proc.wait(timeout=1.0)
                except Exception:
                    pass
                if self.ff_proc and self.ff_proc.poll() is None:
                    try:
                        self.ff_proc.kill()
                        self.ff_proc.wait(timeout=1.0)
                    except Exception:
                        pass
                if IS_WIN and self.ff_proc and self.ff_proc.poll() is None:
                    try:
                        run_hidden(["taskkill", "/PID", str(self.ff_proc.pid), "/T", "/F"], capture=False)
                    except Exception:
                        pass
                if IS_WIN and self.ff_proc and self.ff_proc.poll() is None:
                    self.log.emit("[WARN] Aggressive kill: taskkill /IM ffmpeg.exe /T /F")
                    try:
                        run_hidden(["taskkill", "/IM", "ffmpeg.exe", "/T", "/F"], capture=False)
                    except Exception:
                        pass
        finally:
            self.ff_proc = None

    def skip(self):
        self._skip.set()
        self.log.emit("[INFO] Skip requested — advancing to next item…")
        try:
            if self.ff_proc and self.ff_proc.poll() is None:
                self.ff_proc.kill()
        except Exception:
            pass

    # ---------- yt-dlp helpers ----------
    def get_video_ids(self, playlist_url: str) -> List[str]:
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp.exe not found. Put it next to the EXE or in PATH.")
        cmd = [self.ytdlp_path, "--ignore-errors", "--flat-playlist", "--get-id", playlist_url]
        cp = run_hidden(cmd)
        if cp.returncode != 0:
            raise RuntimeError(f"yt-dlp error: {cp.stderr.strip()}")
        return [line.strip() for line in (cp.stdout or "").splitlines() if line.strip()]

    def get_metadata(self, video_id: str) -> Tuple[str, Optional[str]]:
        if not self.ytdlp_path:
            return self.get_title_legacy(video_id), None
        url = f"https://www.youtube.com/watch?v={video_id}"
        cp = run_hidden([self.ytdlp_path, "-j", url])
        if cp.returncode != 0 or not cp.stdout:
            return self.get_title_legacy(video_id), None
        try:
            data = json.loads(cp.stdout.strip().splitlines()[-1])
        except Exception:
            return self.get_title_legacy(video_id), None
        title = data.get("title") or url
        pretty_date = fmt_yt_date(data.get("upload_date"), data.get("timestamp"), data.get("release_timestamp"))
        return title, pretty_date

    def get_title_legacy(self, video_id: str) -> str:
        url = f"https://www.youtube.com/watch?v={video_id}"
        if not self.ytdlp_path:
            return url
        cp = run_hidden([self.ytdlp_path, "--get-title", url])
        return (cp.stdout or "").strip() if cp.returncode == 0 and cp.stdout else url

    def get_stream_urls(self, video_id: str) -> Tuple[str, Optional[str]]:
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp.exe not found.")
        url = f"https://www.youtube.com/watch?v={video_id}"
        fmt = "bv*+ba/best"
        cp = run_hidden([self.ytdlp_path, "-g", "-f", fmt, url])
        if cp.returncode != 0:
            raise RuntimeError(f"yt-dlp -g failed: {cp.stderr.strip()}")
        lines = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError("No playable formats found.")
        return (lines[0], None) if len(lines) == 1 else (lines[0], lines[1])

    # ---------- encoder selection ----------
    def select_encoder(self):
        self.cfg.encoder = "libx264"
        self.cfg.encoder_name = "CPU x264"
        self.cfg.pix_fmt = "yuv420p"
        self.cfg.extra_venc_flags = ["-preset", "veryfast"]
        if not self.ffmpeg_path:
            return
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

    # ---------- ffmpeg ----------
    def build_ffmpeg_cmd(self, vurl: str, aurl: Optional[str]) -> List[str]:
        gop = self.cfg.fps * 2
        vf = [f"scale=-2:{self.cfg.height}:flags=bicubic"]
        if self.cfg.overlay_titles:
            font_path = Path(self.cfg.fontfile)
            if self.cfg.fontfile and font_path.exists():
                fontfile = font_path.as_posix().replace(":", r"\:").replace("'", r"\\'")
                title_file = Path(self.cfg.title_file).as_posix().replace(":", r"\:").replace("'", r"\\'")
                fontsize = getattr(self.cfg, "_overlay_fontsize", 24)
                vf.append(
                    f"drawtext=fontfile='{fontfile}':textfile='{title_file}':reload=1:"
                    f"fontcolor=white:fontsize={fontsize}:box=1:boxcolor=black@0.5:x=10:y=10"
                )
            elif self.cfg.fontfile:
                self.log.emit(f"[WARN] Font file not found: {font_path}")
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
        # Title + date overlay (truncate title; keep date intact)
        title, pretty_date = self.get_metadata(video_id)
        if self.cfg.overlay_titles:
            suffix = f" • {pretty_date}" if pretty_date else ""
            title_clean = (title or "").replace("\n", " ").strip()

            MAX_LEN = 75  # total length including suffix
            if len(title_clean) + len(suffix) > MAX_LEN:
                avail = max(10, MAX_LEN - len(suffix) - 3)  # leave room for "..."
                title_clean = title_clean[:avail] + "..."

            overlay_text = title_clean + suffix
            self.cfg._overlay_fontsize = 24
            safe_write_text(Path(self.cfg.title_file), overlay_text)
        else:
            if hasattr(self.cfg, "_overlay_fontsize"):
                delattr(self.cfg, "_overlay_fontsize")

        # Direct URLs and ffmpeg run (hidden window, own process group)
        vurl, aurl = self.get_stream_urls(video_id)
        ff_cmd = self.build_ffmpeg_cmd(vurl, aurl)
        self.log.emit(f"[CMD] ffmpeg: {' '.join(ff_cmd)}")
        self._skip.clear()
        self.ff_proc = subprocess.Popen(
            ff_cmd,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            startupinfo=STARTUPINFO,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        )

        def _reader(stream):
            try:
                for line in iter(stream.readline, ""):
                    self.log.emit(line.rstrip())
            finally:
                stream.close()

        readers = []
        if self.ff_proc.stdout:
            t = threading.Thread(target=_reader, args=(self.ff_proc.stdout,))
            t.daemon = True
            t.start()
            readers.append(t)
        if self.ff_proc.stderr:
            t = threading.Thread(target=_reader, args=(self.ff_proc.stderr,))
            t.daemon = True
            t.start()
            readers.append(t)

        while self.ff_proc and self.ff_proc.poll() is None and not (
            self._stop.is_set() or self._skip.is_set()
        ):
            time.sleep(0.2)

        if (self._stop.is_set() or self._skip.is_set()) and self.ff_proc and self.ff_proc.poll() is None:
            try:
                self.ff_proc.kill()
            except Exception:
                pass

        try:
            if self.ff_proc:
                self.ff_proc.wait(timeout=1.0)
        except Exception:
            pass

        for t in readers:
            t.join(timeout=0.2)


    # ---------- main loop ----------
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
                        if self._stop.is_set():
                            break
                        time.sleep(1)
                    continue

                if self.cfg.shuffle:
                    random.shuffle(ids)

                for idx, vid in enumerate(ids, 1):
                    if self._stop.is_set():
                        break

                    self.log.emit("-" * 46)
                    self.log.emit(f"[INFO] Item #{idx} - https://www.youtube.com/watch?v={vid}")
                    self.log.emit("-" * 46)

                    try:
                        self.run_one_video(vid)
                    except Exception as e:
                        self.log.emit(f"[WARN] Stream error: {e}")

                    if self._stop.is_set():
                        break


                if self._stop.is_set():
                    break
                self.log.emit("\n[INFO] End of playlist. Refreshing IDs and looping…\n")

            except Exception as e:
                self.log.emit(f"[WARN] Loop error: {e}. Retrying in 30s…")
                for _ in range(30):
                    if self._stop.is_set():
                        break
                    time.sleep(1)

        self.status.emit("Stopped")
        self.finished.emit()

# ---------- GUI (dark & readable checkboxes) ----------
DARK_QSS = """
* { color: #e6e6e6; font-family: Segoe UI, Arial, sans-serif; }
QWidget { background: #111315; }
QLineEdit, QComboBox, QTextEdit, QSpinBox {
  background: #1a1d21; border: 1px solid #2a2f36; border-radius: 8px; padding: 6px;
}
QPushButton { background: #2b6cb0; border: none; border-radius: 10px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #2f76c2; }
QPushButton:disabled { background: #2a2f36; color: #8a8f98; }
QGroupBox { border: 1px solid #2a2f36; border-radius: 10px; margin-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }

QCheckBox::indicator {
  width: 18px; height: 18px; border: 1px solid #2a2f36; border-radius: 4px;
  background: #1a1d21;
}
QCheckBox::indicator:checked {
  background: #2b6cb0; border: 1px solid #2b6cb0; image: none;
}
QCheckBox::indicator:unchecked {
  background: #1a1d21; image: none;
}
"""

class MainWindow(QtWidgets.QWidget):
    startRequested = QtCore.Signal(StreamConfig)
    stopRequested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — YouTube 24/7 VOD Streamer")
        # Allow the window to shrink when the console is hidden. Keep a sensible
        # minimum width, but let the height adjust dynamically.
        self.setMinimumWidth(860)
        self.resize(860, 680)
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
        self.res_combo.addItems(["480p30", "480p60", "720p30", "720p60", "1080p30", "1080p60"])
        self.res_combo.setCurrentText("720p30")
        self.bitrate_edit = QtWidgets.QLineEdit("2300k")
        self.bufsize_edit = QtWidgets.QLineEdit("4600k")
        self.font_edit = QtWidgets.QLineEdit(default_font_path())
        self.font_btn = QtWidgets.QPushButton("Font…")

        self.overlay_chk = QtWidgets.QCheckBox("Overlay current VOD title")
        self.overlay_chk.setChecked(True)
        self.shuffle_chk = QtWidgets.QCheckBox("Shuffle playlist order")
        self.console_chk = QtWidgets.QCheckBox("Show console")
        self.console_chk.setChecked(True)
        self.remember_chk = QtWidgets.QCheckBox("Save playlist and key")
        self.remember_chk.setChecked(True)

        self.console = QtWidgets.QTextEdit()
        self.console.setReadOnly(True)
        self.console.setVisible(True)

        self.start_btn = QtWidgets.QPushButton("Start Stream")
        self.stop_btn = QtWidgets.QPushButton("Stop Stream")
        self.stop_btn.setEnabled(False)
        self.skip_btn = QtWidgets.QPushButton("Skip Video")
        self.skip_btn.setEnabled(False)

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

        form.addWidget(QtWidgets.QLabel("Overlay Font"), 4, 0)
        form.addWidget(self.font_edit, 4, 1, 1, 2)
        form.addWidget(self.font_btn, 4, 3)

        toggles = QtWidgets.QHBoxLayout()
        toggles.addWidget(self.overlay_chk)
        toggles.addWidget(self.shuffle_chk)
        toggles.addStretch(1)
        toggles.addWidget(self.console_chk)

        bottom_opts = QtWidgets.QHBoxLayout()
        bottom_opts.addWidget(self.remember_chk)
        bottom_opts.addStretch(1)

        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.skip_btn)
        btns.addStretch(1)

        v = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("Loop your public YouTube VOD playlist to YouTube Live 24/7. Auto-encoder picks NVENC/QSV/AMF/x264.")
        header.setWordWrap(True)
        header.setStyleSheet("font-size:14px; color:#b9c2cf;")
        v.addWidget(header)
        v.addLayout(form)
        v.addLayout(toggles)
        v.addLayout(bottom_opts)
        v.addLayout(btns)
        v.addWidget(self.console, 1)

        # Signals
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        self.skip_btn.clicked.connect(self.on_skip)
        self.console_chk.toggled.connect(self.on_console_toggle)
        self.res_combo.currentIndexChanged.connect(self.on_quality_change)
        self.font_btn.clicked.connect(self.on_browse_font)

        # persist as you tweak
        self.remember_chk.toggled.connect(lambda _: self.save_settings())
        self.overlay_chk.toggled.connect(lambda _: self.save_settings())
        self.shuffle_chk.toggled.connect(lambda _: self.save_settings())
        self.res_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.bitrate_edit.textChanged.connect(lambda _: self.save_settings())
        self.bufsize_edit.textChanged.connect(lambda _: self.save_settings())
        self.font_edit.textChanged.connect(lambda _: self.save_settings())

        self.on_quality_change()
        self.load_settings()

    # --- settings (config.json) ---
    def load_settings(self):
        cfg = load_config_json()
        remember = str(cfg.get("remember", True)).lower() in ("1", "true", "yes", "on")
        self.remember_chk.setChecked(remember)

        if remember:
            self.playlist_edit.setText(cfg.get("playlist_url", ""))
            self.key_edit.setText(cfg.get("stream_key", ""))

        self.overlay_chk.setChecked(bool(cfg.get("overlay_titles", True)))
        self.shuffle_chk.setChecked(bool(cfg.get("shuffle", False)))
        self.font_edit.setText(cfg.get("fontfile", default_font_path()))

        if "quality" in cfg:
            idx = self.res_combo.findText(cfg["quality"])
            if idx >= 0:
                self.res_combo.setCurrentIndex(idx)
        if "video_bitrate" in cfg:
            self.bitrate_edit.setText(cfg["video_bitrate"])
        if "bufsize" in cfg:
            self.bufsize_edit.setText(cfg["bufsize"])

    def save_settings(self):
        data = load_config_json()
        data.update({
            "remember": self.remember_chk.isChecked(),
            "overlay_titles": self.overlay_chk.isChecked(),
            "shuffle": self.shuffle_chk.isChecked(),
            "quality": self.res_combo.currentText(),
            "video_bitrate": self.bitrate_edit.text().strip(),
            "bufsize": self.bufsize_edit.text().strip(),
            "fontfile": self.font_edit.text().strip(),
        })
        if self.remember_chk.isChecked():
            data["playlist_url"] = self.playlist_edit.text().strip()
            data["stream_key"] = self.key_edit.text().strip()
        else:
            data.pop("playlist_url", None)
            data.pop("stream_key", None)
        save_config_json(data)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.save_settings()
        return super().closeEvent(event)

    # --- UI helpers ---
    def append_log(self, text: str):
        self.console.append(text)
        self.console.moveCursor(QtGui.QTextCursor.End)

    def on_browse_font(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select overlay font",
            "",
            "Font Files (*.ttf *.otf *.ttc);;All Files (*)",
        )
        if path:
            self.font_edit.setText(path)
            self.save_settings()

    def on_console_toggle(self, checked: bool):
        """Show or hide the log console without shifting the rest of the UI."""
        self.console.setVisible(checked)
        # Collapse the console area when hidden so the window height adjusts
        self.console.setMaximumHeight(16777215 if checked else 0)
        self.adjustSize()

    def on_quality_change(self):
        choice = self.res_combo.currentText()
        if "480p30" in choice:
            self._fps = 30; self._height = 480
            if not self.streaming:
                self.bitrate_edit.setText("1000k"); self.bufsize_edit.setText("2000k")
        elif "480p60" in choice:
            self._fps = 60; self._height = 480
            if not self.streaming:
                self.bitrate_edit.setText("1500k"); self.bufsize_edit.setText("3000k")
        elif "720p30" in choice:
            self._fps = 30; self._height = 720
            if not self.streaming:
                self.bitrate_edit.setText("2300k"); self.bufsize_edit.setText("4600k")
        elif "720p60" in choice:
            self._fps = 60; self._height = 720
            if not self.streaming:
                self.bitrate_edit.setText("3200k"); self.bufsize_edit.setText("6400k")
        elif "1080p30" in choice:
            self._fps = 30; self._height = 1080
            if not self.streaming:
                self.bitrate_edit.setText("4500k"); self.bufsize_edit.setText("9000k")
        else:  # 1080p60
            self._fps = 60; self._height = 1080
            if not self.streaming:
                self.bitrate_edit.setText("6000k"); self.bufsize_edit.setText("12000k")

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
            sleep_between=0,
            fontfile=self.font_edit.text().strip() or default_font_path(),
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

        # save settings right when starting (if 'remember' is checked)
        self.save_settings()

        self.streaming = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.skip_btn.setEnabled(True)
        self.append_log("[INFO] Starting stream…")

        self.worker_thread = QtCore.QThread(self)
        self.worker = StreamWorker(cfg)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.status.connect(lambda s: self.append_log(f"[STATUS] {s}"))
        self.worker.finished.connect(self.on_finished)

        # Keep a stop signal for completeness, but call worker.stop() directly on Stop
        self.stopRequested.connect(self.worker.stop)

        self.worker_thread.start()

    def on_stop(self):
        if not self.streaming:
            return
        self.append_log("[INFO] Stopping…")

        # Call the worker's stop immediately (don’t wait for queued signal)
        try:
            if self.worker:
                self.worker.stop()
        except Exception:
            pass

        # Also emit the signal (harmless if already stopped)
        self.stopRequested.emit()

        # Do not quit the thread here; let on_finished() handle cleanup
        self.stop_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)

    def on_skip(self):
        if not self.streaming or not self.worker:
            return
        self.append_log("[INFO] Skipping…")
        try:
            self.worker.skip()
        except Exception:
            pass

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
        self.skip_btn.setEnabled(False)

# ---------- entry ----------
def main():
    # Ensure taskbar groups under our app and can show our icon (Windows)
    if IS_WIN:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)

    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app = QtWidgets.QApplication(sys.argv)

    # Load .ico (next to EXE when frozen, or cwd when running from source)
    icon = QtGui.QIcon(resource_path("icon.ico"))
    app.setWindowIcon(icon)  # taskbar/dock icon

    app.setStyleSheet(DARK_QSS)
    w = MainWindow()
    w.setWindowIcon(icon)    # title-bar icon
    w.resize(980, 700)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
