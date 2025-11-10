# Stream247_GUI.py — GUI YouTube 24/7 streamer
# - Uses yt-dlp.exe (next to the EXE) for playlist IDs / titles / direct URLs
# - Auto-selects NVENC > QSV > AMF > x264 via safe probe
# - Runs ffmpeg and yt-dlp with hidden windows (no console)
# - Clean Start/Stop (kills ffmpeg reliably; Windows fallback uses taskkill /T /F)
# - Saves config to config.json next to the EXE
# - Overlay shows: "<TITLE> • <Pretty Date>" with title truncation (date preserved)

import os, sys, time, json, random, shutil, subprocess, threading, datetime, webbrowser
import zipfile, tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
import urllib.request
import urllib.error
import re

# General application metadata and platform helpers
APP_NAME = "Stream247"  # Name shown in the GUI and taskbar
APP_VERSION = "1.3.1"  # Current version
GITHUB_REPO = "TheDoctorTTV/247-steam"  # GitHub repository for updates
IS_WIN = (os.name == "nt")  # True when running on Windows
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0  # Hide console windows
CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WIN else 0  # Allow child process killing

# Startup information for subprocesses (only meaningful on Windows)
STARTUPINFO = None
if IS_WIN:
    STARTUPINFO = subprocess.STARTUPINFO()
    # Prevent ffmpeg/yt-dlp windows from flashing on screen
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # hide windows

# ---------- config.json helpers ----------
def _app_dir() -> Path:
    """Return the directory where the app is running from."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).parent  # packaged exe folder
    return Path.cwd()                       # running from source

CONFIG_PATH = _app_dir() / "config.json"

def load_config_json() -> dict:
    """Load configuration settings from CONFIG_PATH if it exists."""
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_config_json(data: dict) -> None:
    """Persist the configuration dictionary to CONFIG_PATH."""
    try:
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

# ---------- misc utilities ----------
# (Removed: default browser detection helpers)
def resource_path(name: str) -> str:
    """Resolve a resource path for frozen executables or source runs."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
    p = Path(base) / name
    if p.exists():
        return str(p)
    return str(Path.cwd() / name)

def find_drawtext_fontfile() -> Optional[str]:
    """Return a font file path suitable for ffmpeg drawtext across platforms.

    Tries common system fonts on Windows, macOS, and Linux. Returns None if not found.
    """
    candidates: List[Path] = []
    if IS_WIN:
        windir = os.environ.get("WINDIR", r"C:\\Windows")
        candidates += [
            Path(windir) / "Fonts" / name
            for name in ("segoeui.ttf", "arial.ttf", "calibri.ttf", "tahoma.ttf")
        ]
    else:
        # macOS common font locations
        candidates += [
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Helvetica.ttc"),
            Path("/System/Library/Fonts/Helvetica.ttc"),
        ]
        # Linux common fonts
        candidates += [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ]
    for p in candidates:
        try:
            if p.exists():
                return p.as_posix()
        except Exception:
            continue
    return None

def find_binary(candidates: List[str]) -> Optional[str]:
    """Search PATH and local resources for the first existing executable."""
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
    """Locate an ffmpeg binary in PATH or alongside the executable."""
    return find_binary(["ffmpeg", "ffmpeg.exe"])

def find_ytdlp() -> Optional[str]:
    """Locate a yt-dlp binary in PATH or alongside the executable."""
    # First try the Python-installed version (usually more up-to-date)
    candidates = ["yt-dlp", "yt-dlp.exe"]
    
    # Check PATH first
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    
    # Then check local resources
    for c in ["yt-dlp.exe", "yt-dlp"]:
        rp = resource_path(c)
        if Path(rp).exists():
            return rp
    
    return None

def _download_url(url: str, dest_path: Path, user_agent: Optional[str] = None) -> None:
    """Download a URL to dest_path atomically.

    Uses a temp file then renames into place to avoid partial files on failure.
    """
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = resp.length or 0
        # Write to a temporary file first
        with tempfile.NamedTemporaryFile(delete=False, dir=str(dest_path.parent)) as tf:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                tf.write(chunk)
            tmp_name = tf.name
    # Ensure parent exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # Replace existing file if present
    Path(tmp_name).replace(dest_path)

def github_latest_asset_url(repo: str, prefer_substrings: List[str], must_match_regex: str = ".*", user_agent: Optional[str] = None) -> Optional[str]:
    """Return browser_download_url of an asset from latest GitHub release.

    Args:
      repo: "owner/name" form
      prefer_substrings: list of substrings to prioritize in asset name order
      must_match_regex: regex that asset name must match
    """
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json"}
        if user_agent:
            headers["User-Agent"] = user_agent
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assets = data.get("assets", [])
        if not assets:
            return None
        regex = re.compile(must_match_regex)
        # Filter by regex
        filtered = [a for a in assets if regex.search(a.get("name", ""))]
        if not filtered:
            return None
        # Prefer entries containing preferred substrings in order
        def score(name: str) -> Tuple[int, int]:
            pri = len(prefer_substrings)
            for i, sub in enumerate(prefer_substrings):
                if sub.lower() in name.lower():
                    pri = i
                    break
            # Prefer smaller files might have shorter names; secondary metric by length
            return (pri, len(name))

        best = min(filtered, key=lambda a: score(a.get("name", "")))
        return best.get("browser_download_url")
    except Exception:
        return None

def run_hidden(cmd: List[str], check=False, capture=True, text=True, timeout=None) -> subprocess.CompletedProcess:
    """Run a subprocess without showing a console window."""
    kwargs = {}
    if IS_WIN:
        kwargs["startupinfo"] = STARTUPINFO
        kwargs["creationflags"] = CREATE_NO_WINDOW
    if capture:
        kwargs.update(dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text))
    return subprocess.run(cmd, check=check, timeout=timeout, **kwargs)

def safe_write_text(path: Path, text: str) -> None:
    """Write text to a file, ignoring any errors that occur."""
    try:
        path.write_text(text, encoding="utf-8", errors="ignore")
    except Exception:
        pass

def ffprobe_encoder(ffmpeg_path: str, codec: str) -> bool:
    """Check whether ``ffmpeg`` can use a specific encoder."""
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
    """Return a human‑friendly YouTube upload date in UTC (matching YouTube's display)."""
    dt = None
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        try:
            # Parse the date string directly - upload_date is YYYYMMDD in UTC
            year = int(upload_date[0:4])
            month = int(upload_date[4:6])
            day = int(upload_date[6:8])
            date_obj = datetime.date(year, month, day)
            # Subtract 1 day to account for timezone offset
            date_obj = date_obj - datetime.timedelta(days=1)
            # Format the corrected date
            dt_for_format = datetime.datetime.combine(date_obj, datetime.time.min)
            return dt_for_format.strftime("%b %#d, %Y") if IS_WIN else dt_for_format.strftime("%b %-d, %Y")
        except Exception:
            pass
    # Fallback to timestamp if upload_date not available
    ts = release_ts or timestamp
    if ts:
        try:
            # Convert timestamp to UTC datetime
            dt = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc)
            # Subtract 1 day to account for timezone offset
            dt = dt - datetime.timedelta(days=1)
            # Strip timezone info for formatting
            dt = dt.replace(tzinfo=None)
            return dt.strftime("%b %#d, %Y") if IS_WIN else dt.strftime("%b %-d, %Y")
        except Exception:
            pass
    return None


# ---------- update checker ----------
class UpdateChecker(QtCore.QObject):
    """Background worker to check for application updates."""
    
    update_checked = QtCore.Signal(dict)  # Emits update info
    error_occurred = QtCore.Signal(str)   # Emits error message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.repo = GITHUB_REPO
        
    @QtCore.Slot()
    def check_for_updates(self):
        """Check GitHub releases for newer versions."""
        try:
            url = f"https://api.github.com/repos/{self.repo}/releases/latest"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', f'{APP_NAME}/{APP_VERSION}')
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                
            # Extract version info
            latest_version = data.get('tag_name', '').lstrip('v')
            release_name = data.get('name', '')
            release_notes = data.get('body', '')
            release_url = data.get('html_url', '')
            published_at = data.get('published_at', '')
            
            # Parse published date
            published_date = None
            if published_at:
                try:
                    dt = datetime.datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                    published_date = dt.strftime("%b %d, %Y")
                except Exception:
                    published_date = published_at
            
            # Compare versions (simple string comparison for now)
            current_version = APP_VERSION
            is_newer = self._is_version_newer(latest_version, current_version)
            
            # Find download URL for Windows executable
            download_url = None
            assets = data.get('assets', [])
            for asset in assets:
                name = asset.get('name', '').lower()
                if name.endswith('.exe') and 'stream247' in name:
                    download_url = asset.get('browser_download_url')
                    break
            
            result = {
                'current_version': current_version,
                'latest_version': latest_version,
                'is_newer': is_newer,
                'release_name': release_name,
                'release_notes': release_notes,
                'release_url': release_url,
                'download_url': download_url,
                'published_date': published_date
            }
            
            self.update_checked.emit(result)
            
        except urllib.error.URLError as e:
            self.error_occurred.emit(f"Network error: {e}")
        except json.JSONDecodeError:
            self.error_occurred.emit("Failed to parse update information")
        except Exception as e:
            self.error_occurred.emit(f"Update check failed: {e}")
    
    def _is_version_newer(self, latest: str, current: str) -> bool:
        """Compare version strings to determine if latest is newer than current."""
        try:
            # Simple version comparison (handles x.y.z format)
            latest_parts = [int(x) for x in latest.split('.')]
            current_parts = [int(x) for x in current.split('.')]
            
            # Pad shorter version with zeros
            max_len = max(len(latest_parts), len(current_parts))
            latest_parts.extend([0] * (max_len - len(latest_parts)))
            current_parts.extend([0] * (max_len - len(current_parts)))
            
            return latest_parts > current_parts
        except (ValueError, AttributeError):
            # Fallback to string comparison
            return latest != current and latest > current


# ---------- buffer presets ----------
BUFFER_PRESETS = {
    "Low": {
        "probesize": "15M",
        "analyzeduration": "5000000",  # 5 seconds
        "buffer_size": "2048k",
        "max_delay": "3000000",  # 3 seconds in microseconds
    },
    "Medium": {
        "probesize": "25M",
        "analyzeduration": "10000000",  # 10 seconds
        "buffer_size": "4096k",
        "max_delay": "7000000",  # 7 seconds in microseconds
    },
    "High": {
        "probesize": "40M",
        "analyzeduration": "15000000",  # 15 seconds
        "buffer_size": "6144k",
        "max_delay": "12000000",  # 12 seconds in microseconds
    },
    "Ultra": {
        "probesize": "50M",
        "analyzeduration": "30000000",  # 30 seconds
        "buffer_size": "8192k",
        "max_delay": "25000000",  # 25 seconds in microseconds
    }
}

# ---------- streaming core ----------
@dataclass
class StreamConfig:
    """Configuration options for the livestream."""

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
    title_file: str = "current_title.txt"
    rtmp_live: bool = False
    buffer_mode: str = "Medium"  # Low, Medium, or High

    # runtime-selected
    encoder: str = "libx264"
    encoder_name: str = "CPU x264"
    pix_fmt: str = "yuv420p"
    extra_venc_flags: List[str] = None  # type: ignore
    _overlay_fontsize: int = 24  # Optional runtime field for overlay fontsize

    def rtmp_url(self) -> str:
        """Construct the full RTMP URL using the base and stream key."""
        return f"{self.rtmp_base}/{self.stream_key}"


class StreamWorker(QtCore.QObject):
    """Background worker that handles playlist streaming with ffmpeg."""

    log = QtCore.Signal(str)
    status = QtCore.Signal(str)
    finished = QtCore.Signal()

    ff_proc: Optional[subprocess.Popen]

    def __init__(self, cfg: StreamConfig, parent=None):
        """Store configuration and initialise worker state."""
        super().__init__(parent)
        self.cfg = cfg
        self._stop = threading.Event()
        self._skip = threading.Event()
        self.ffmpeg_path = find_ffmpeg()
        self.ytdlp_path = find_ytdlp()
        self.ff_proc = None
        # Prefetch cache for next video
        self._prefetch_video_id: Optional[str] = None
        self._prefetch_title: Optional[str] = None
        self._prefetch_date: Optional[str] = None
        self._prefetch_vurl: Optional[str] = None
        self._prefetch_aurl: Optional[str] = None
        self._prefetch_thread: Optional[threading.Thread] = None
        # (YouTube auth config removed)

    def _ytdlp_cookies_args(self) -> List[str]:
        """Return yt-dlp auth arguments. Cookies disabled (no auth needed)."""
        return []

    # ---------- dependency ensure / auto-download ----------
    def ensure_binaries(self, force: bool = False):
        """Ensure yt-dlp and ffmpeg are available; try to auto-download on Windows.

        - yt-dlp: download latest Windows exe from GitHub if missing
        - ffmpeg: download prebuilt gyan.dev latest zip if missing and extract ffmpeg.exe
        """
        if not IS_WIN:
            # On non-Windows, we don't attempt auto-install; rely on PATH.
            return

        app_dir = _app_dir()

        # Ensure a local yt-dlp.exe next to the EXE and prefer using it
        local_ytdlp = app_dir / "yt-dlp.exe"
        if force and local_ytdlp.exists():
            try:
                local_ytdlp.unlink()
            except Exception:
                pass
        if not local_ytdlp.exists():
            try:
                self.log.emit("[INFO] yt-dlp.exe not found next to the app — downloading latest release…")
                # Use GitHub latest/download stable filename; fallback to API lookup
                ytdlp_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
                _download_url(ytdlp_url, local_ytdlp, user_agent=f"{APP_NAME}/{APP_VERSION}")
                try:
                    os.chmod(local_ytdlp, 0o755)
                except Exception:
                    pass
                self.log.emit("[INFO] Downloaded yt-dlp.exe")
            except Exception:
                # Fallback via API
                alt = github_latest_asset_url(
                    "yt-dlp/yt-dlp",
                    prefer_substrings=["yt-dlp.exe"],
                    must_match_regex=r"yt-dlp.*\.exe$",
                    user_agent=f"{APP_NAME}/{APP_VERSION}"
                )
                if alt:
                    try:
                        _download_url(alt, local_ytdlp, user_agent=f"{APP_NAME}/{APP_VERSION}")
                        try:
                            os.chmod(local_ytdlp, 0o755)
                        except Exception:
                            pass
                        self.log.emit("[INFO] Downloaded yt-dlp.exe via API fallback")
                    except Exception as e2:
                        self.log.emit(f"[WARN] Failed to download yt-dlp.exe automatically: {e2}")
                else:
                    self.log.emit("[WARN] Could not determine latest yt-dlp.exe download URL")
        # Prefer local copy if available
        if local_ytdlp.exists():
            self.ytdlp_path = str(local_ytdlp)

        # Ensure a local ffmpeg.exe next to the EXE and prefer using it
        local_ffmpeg = app_dir / "ffmpeg.exe"
        if force and local_ffmpeg.exists():
            try:
                local_ffmpeg.unlink()
            except Exception:
                pass
        if not local_ffmpeg.exists():
            try:
                self.log.emit("[INFO] ffmpeg.exe not found next to the app — downloading latest Windows build…")
                # Find a recent Windows x64 zip from BtbN/FFmpeg-Builds via API
                ff_zip_api_url = github_latest_asset_url(
                    "BtbN/FFmpeg-Builds",
                    prefer_substrings=["win64", "lgpl", "shared", "zip"],
                    must_match_regex=r"ffmpeg-.*win64.*zip$",
                    user_agent=f"{APP_NAME}/{APP_VERSION}"
                )
                if not ff_zip_api_url:
                    raise RuntimeError("Could not determine latest FFmpeg Windows zip from GitHub API")
                dest_zip = app_dir / "ffmpeg-latest.zip"
                _download_url(ff_zip_api_url, dest_zip, user_agent=f"{APP_NAME}/{APP_VERSION}")

                # Extract ffmpeg.exe
                ffmpeg_exe_path: Optional[Path] = None
                try:
                    with zipfile.ZipFile(dest_zip, 'r') as zf:
                        # Find ffmpeg.exe inside the archive (path varies by build)
                        cand = [n for n in zf.namelist() if n.lower().endswith('/bin/ffmpeg.exe') or n.lower().endswith('ffmpeg.exe')]
                        if not cand:
                            # Extract all to temp and search
                            with tempfile.TemporaryDirectory() as tmpd:
                                zf.extractall(tmpd)
                                for root, _dirs, files in os.walk(tmpd):
                                    for f in files:
                                        if f.lower() == 'ffmpeg.exe':
                                            ffmpeg_exe_path = Path(root) / f
                                            break
                                    if ffmpeg_exe_path:
                                        break
                                if not ffmpeg_exe_path:
                                    raise RuntimeError("ffmpeg.exe not found inside archive")
                                # Copy to app_dir
                                target = local_ffmpeg
                                shutil.copy2(ffmpeg_exe_path, target)
                                ffmpeg_exe_path = target
                        else:
                            # Directly extract ffmpeg.exe entry
                            target = local_ffmpeg
                            # If entry contains folders, extract then move
                            member_name = cand[0]
                            with zf.open(member_name) as src, open(target, 'wb') as out:
                                shutil.copyfileobj(src, out)
                            ffmpeg_exe_path = target
                finally:
                    try:
                        dest_zip.unlink(missing_ok=True)  # cleanup zip
                    except Exception:
                        pass

                if ffmpeg_exe_path and ffmpeg_exe_path.exists():
                    try:
                        os.chmod(ffmpeg_exe_path, 0o755)
                    except Exception:
                        pass
                    self.ffmpeg_path = str(ffmpeg_exe_path)
                    self.log.emit("[INFO] FFmpeg downloaded and ready")
            except Exception as e:
                self.log.emit(
                    "[ERROR] Could not auto-download FFmpeg. Please place ffmpeg.exe next to the EXE or install FFmpeg in PATH."
                )
                self.log.emit(f"[DETAIL] {e}")
        # Prefer local copy if available
        if local_ffmpeg.exists():
            self.ffmpeg_path = str(local_ffmpeg)

    def preflight_rtmp(self) -> bool:
        """Quickly validate RTMP endpoint by pushing a 1-second test stream.

        Returns True on success; logs errors and returns False on failure.
        """
        try:
            # Build minimal test command using color source (video) and anullsrc (audio)
            gop = max(2, self.cfg.fps * 2)
            vf_chain = [
                "scale=-2:360:flags=bicubic",
                f"format={self.cfg.pix_fmt}",
            ]
            def try_push(url: str) -> Tuple[bool, str]:
                cmd = [
                    self.ffmpeg_path or "ffmpeg",
                    "-hide_banner", "-loglevel", "warning", "-stats",
                    "-re", "-f", "lavfi", "-i", f"color=black:s=640x360:rate={self.cfg.fps}",
                    "-f", "lavfi", "-i", "anullsrc=cl=stereo:r=44100",
                    "-t", "1",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", self.cfg.encoder, *(self.cfg.extra_venc_flags or []),
                    "-fflags", "+genpts",
                    "-r", str(self.cfg.fps), "-g", str(gop), "-keyint_min", str(gop),
                    "-b:v", "1000k", "-maxrate", "1000k", "-bufsize", "2000k",
                    "-vf", ",".join(vf_chain),
                    "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
                    "-f", "flv", url,
                ]
                try:
                    cp = run_hidden(cmd, capture=True, timeout=15)
                    return (cp.returncode == 0, (cp.stderr or "").strip())
                except subprocess.TimeoutExpired:
                    return (False, "RTMP preflight timed out")
                except Exception as e:
                    return (False, f"RTMP preflight exception: {e}")

            self.log.emit("[INFO] Preflight: testing RTMP connectivity…")
            url = self.cfg.rtmp_url()
            ok, err = try_push(url)
            if ok:
                self.log.emit("[INFO] Preflight: RTMP OK")
                return True

            # Attempt RTMPS fallback if original was RTMP
            try:
                from urllib.parse import urlparse, urlunparse
                u = urlparse(url)
                if u.scheme == "rtmp":
                    # Switch to rtmps and default to port 443 if none set or was 1935
                    netloc = u.netloc
                    host, sep, port = netloc.partition(":")
                    new_port = "443"
                    new_netloc = f"{host}:{new_port}" if host else netloc
                    rtmps_url = urlunparse(("rtmps", new_netloc, u.path, u.params, u.query, u.fragment))
                    self.log.emit("[INFO] Preflight: RTMP failed, trying RTMPS fallback…")
                    ok2, err2 = try_push(rtmps_url)
                    if ok2:
                        self.log.emit("[INFO] Preflight: RTMPS OK")
                        # Update cfg to use rtmps for the session
                        self.cfg.rtmp_base = rtmps_url.rsplit("/", 1)[0]
                        self.cfg.stream_key = rtmps_url.rsplit("/", 1)[-1]
                        return True
                    else:
                        self.log.emit(f"[ERROR] RTMPS preflight failed: {err2}")
            except Exception as e2:
                self.log.emit(f"[WARN] RTMPS fallback error: {e2}")

            self.log.emit(f"[ERROR] RTMP preflight failed: {err}")
            return False
        except Exception as e:
            self.log.emit(f"[ERROR] RTMP preflight exception: {e}")
            return False

    def _terminate_ff_proc(self) -> None:
        """Attempt to gracefully terminate any running ffmpeg process."""
        proc = self.ff_proc
        if not proc or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        if IS_WIN and proc.poll() is None:
            for cmd in (
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                ["taskkill", "/IM", "ffmpeg.exe", "/T", "/F"],
            ):
                try:
                    run_hidden(cmd, capture=False)
                except Exception:
                    pass
                if proc.poll() is not None:
                    break

    # ---------- control ----------
    def stop(self):
        """Request the current ffmpeg process to terminate."""
        self._stop.set()
        self.log.emit("[INFO] Stop requested — killing ffmpeg…")
        self._terminate_ff_proc()
        self.ff_proc = None

    def skip(self):
        """Abort the current video and advance to the next."""
        self._skip.set()
        self.log.emit("[INFO] Skip requested — advancing to next item…")
        self._terminate_ff_proc()
        self.ff_proc = None

    # ---------- yt-dlp helpers ----------
    def get_video_ids(self, playlist_url: str) -> List[str]:
        """Return a list of video IDs contained in a YouTube playlist."""
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp.exe not found. Put it next to the EXE or in PATH.")
        
        self.log.emit(f"[INFO] Extracting playlist IDs from: {playlist_url}")
        cmd = [self.ytdlp_path, "--ignore-errors", "--flat-playlist", "--get-id", *self._ytdlp_cookies_args(), playlist_url]
        cp = run_hidden(cmd)
        if cp.returncode != 0:
            err = (cp.stderr or "").strip()
            # Common Windows chromium-family locking issue
            if "Could not copy Chrome cookie database" in err:
                fix = (
                    "Browser cookie database is locked by a running Edge/Chrome instance.\n"
                    "Close all Edge/Chrome windows (including background processes) and try again.\n\n"
                    "Advanced: Launch your browser with --disable-features=LockProfileCookieDatabase to prevent locking.\n"
                    "See: https://github.com/yt-dlp/yt-dlp/issues/7271"
                )
                raise RuntimeError(f"yt-dlp cookie error: {fix}")
            raise RuntimeError(f"yt-dlp error: {err}")
        
        ids = [line.strip() for line in (cp.stdout or "").splitlines() if line.strip()]
        self.log.emit(f"[INFO] Found {len(ids)} videos in playlist")
        
        if len(ids) > 10:
            self.log.emit(f"[INFO] First 10 video IDs: {ids[:10]}")
        else:
            self.log.emit(f"[INFO] Video IDs: {ids}")
            
        return ids

    def get_metadata(self, video_id: str) -> Tuple[str, Optional[str]]:
        """Fetch the title and upload date for a video."""
        if not self.ytdlp_path:
            return self.get_title_legacy(video_id), None
        url = f"https://www.youtube.com/watch?v={video_id}"
        cp = run_hidden([self.ytdlp_path, "-j", *self._ytdlp_cookies_args(), url])
        if cp.returncode != 0 or not cp.stdout:
            if cp.returncode != 0 and cp.stderr and "Could not copy Chrome cookie database" in cp.stderr:
                self.log.emit("[WARN] Cookies locked by browser; close Edge/Chrome and retry (see issue #7271)")
            return self.get_title_legacy(video_id), None
        try:
            data = json.loads(cp.stdout.strip().splitlines()[-1])
        except Exception:
            return self.get_title_legacy(video_id), None
        title = data.get("title") or url
        pretty_date = fmt_yt_date(data.get("upload_date"), data.get("timestamp"), data.get("release_timestamp"))
        return title, pretty_date

    def get_title_legacy(self, video_id: str) -> str:
        """Fallback title retrieval using yt-dlp's --get-title."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        if not self.ytdlp_path:
            return url
        cp = run_hidden([self.ytdlp_path, "--get-title", *self._ytdlp_cookies_args(), url])
        return (cp.stdout or "").strip() if cp.returncode == 0 and cp.stdout else url

    def get_stream_urls(self, video_id: str) -> Tuple[str, Optional[str]]:
        """Return media URLs for a video. Tries HLS first for stability, then falls back to direct URLs."""
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp.exe not found.")
        url = f"https://www.youtube.com/watch?v={video_id}"
        cookies = self._ytdlp_cookies_args()
        
        # Strategy 1: Try HLS manifest (best for 24/7 streaming - no URL expiration)
        try:
            cp = run_hidden([self.ytdlp_path, "-g", "-f", "best", "--hls-prefer-native", *cookies, url])
            if cp.returncode == 0 and cp.stdout:
                lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
                # If we get an m3u8 URL, use it (single stream with muxed audio/video)
                if lines and any('.m3u8' in line for line in lines):
                    hls_url = next((line for line in lines if '.m3u8' in line), None)
                    if hls_url:
                        self.log.emit(f"[INFO] Using HLS manifest for {video_id} (stable for long streams)")
                        return (hls_url, None)  # HLS contains both video and audio
        except Exception as e:
            self.log.emit(f"[DEBUG] HLS attempt failed: {e}")
        
        # Strategy 2: Try direct URLs with multiple format fallbacks (current method)
        format_strategies = [
            "bv*+ba/best",  # Best video + best audio (separate)
            "best[height<=?1080]",  # Best combined format up to 1080p
            "worst[height>=480]",  # Fallback to worst acceptable quality
            "best"  # Last resort - any available format
        ]
        
        for fmt in format_strategies:
            try:
                cp = run_hidden([self.ytdlp_path, "-g", "-f", fmt, *cookies, url])
                if cp.returncode == 0 and cp.stdout:
                    lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
                    if lines:
                        # Skip if we got HLS URLs (we want direct URLs here)
                        if not any('.m3u8' in line for line in lines):
                            self.log.emit(f"[INFO] Using direct URLs for {video_id} (format: {fmt})")
                            return (lines[0], None) if len(lines) == 1 else (lines[0], lines[1])
            except Exception:
                continue
                
        # Strategy 3: Final fallback - try without format specification
        try:
            cp = run_hidden([self.ytdlp_path, "-g", *cookies, url])
            if cp.returncode == 0 and cp.stdout:
                lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
                if lines:
                    return (lines[0], None) if len(lines) == 1 else (lines[0], lines[1])
        except Exception:
            pass
            
        raise RuntimeError(f"No playable formats found for video {video_id}. This may be due to YouTube restrictions or an outdated yt-dlp version.")

    def prefetch_next_video(self, video_id: str) -> None:
        """Prefetch metadata and stream URLs for the next video in a background thread."""
        def _fetch():
            try:
                self.log.emit(f"[PREFETCH] Loading next video: {video_id}")
                title, date = self.get_metadata(video_id)
                vurl, aurl = self.get_stream_urls(video_id)
                
                # Store in cache
                self._prefetch_video_id = video_id
                self._prefetch_title = title
                self._prefetch_date = date
                self._prefetch_vurl = vurl
                self._prefetch_aurl = aurl
                self.log.emit(f"[PREFETCH] Ready: {title}")
            except Exception as e:
                self.log.emit(f"[PREFETCH] Failed for {video_id}: {e}")
                # Clear cache on error
                self._prefetch_video_id = None
                self._prefetch_title = None
                self._prefetch_date = None
                self._prefetch_vurl = None
                self._prefetch_aurl = None
        
        # Start prefetch in background thread
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self.log.emit("[PREFETCH] Previous prefetch still running, skipping...")
            return
        
        self._prefetch_thread = threading.Thread(target=_fetch, daemon=True)
        self._prefetch_thread.start()

    # ---------- encoder selection ----------
    def select_encoder(self):
        """Choose the best available hardware encoder."""
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
                "-spatial_aq", "1", "-temporal_aq", "1", "-aq-strength", "8",
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
        """Build the ffmpeg command for a single video stream."""
        gop = self.cfg.fps * 2
        vf = [f"scale=-2:{self.cfg.height}:flags=bicubic"]
        if self.cfg.overlay_titles:
            title_file = Path(self.cfg.title_file).as_posix().replace(":", r"\:").replace("'", r"\\'")
            fontsize = self.cfg._overlay_fontsize
            fontfile = find_drawtext_fontfile()
            if fontfile:
                esc = fontfile.replace(":", r"\:").replace("'", r"\\'")
                font_arg = f"fontfile='{esc}':"
            else:
                # Let ffmpeg pick a generic family via fontconfig if available
                font_arg = "font='Sans':"
            vf.append(
                f"drawtext=textfile='{title_file}':reload=1:" +
                font_arg +
                f"fontcolor=white:fontsize={fontsize}:box=1:boxcolor=black@0.5:x=10:y=10"
            )
        vf.append(f"format={self.cfg.pix_fmt}")  # keep format as a separate filter
        vf_chain = ",".join(vf)

        # Detect if we're using HLS (m3u8) or direct URLs
        is_hls = '.m3u8' in vurl.lower()
        
        # Get buffer settings based on selected mode
        buffer_settings = BUFFER_PRESETS.get(self.cfg.buffer_mode, BUFFER_PRESETS["Medium"])
        
        cmd = [
            self.ffmpeg_path or "ffmpeg",
            "-hide_banner", "-loglevel", "warning", "-stats",
        ]
        
        # Add buffer-related input options before input URL
        cmd += [
            "-probesize", buffer_settings["probesize"],
            "-analyzeduration", buffer_settings["analyzeduration"],
        ]
        
        # HLS-specific input options for better stability
        if is_hls:
            cmd += [
                "-reconnect", "1",  # Auto-reconnect on connection loss
                "-reconnect_streamed", "1",  # Reconnect for streamed protocols
                "-reconnect_delay_max", "5",  # Max 5s delay between reconnects
                "-live_start_index", "-3",  # Start from 3 segments before live edge
            ]
        
        cmd += ["-re", "-i", vurl]
        
        if aurl:
            cmd += ["-re", "-i", aurl]

        maps = ["-map", "0:v:0"]
        if aurl:
            maps += ["-map", "1:a:0"]
        else:
            maps += ["-map", "0:a:0?"]  # optional audio if progressive/HLS

        cmd += [
            *maps,
            "-c:v", self.cfg.encoder, *self.cfg.extra_venc_flags,
            "-fflags", "+genpts",
            "-r", str(self.cfg.fps), "-g", str(gop), "-keyint_min", str(gop),
            "-b:v", self.cfg.video_bitrate, "-maxrate", self.cfg.video_bitrate, "-bufsize", self.cfg.bufsize,
            "-vf", vf_chain,
            "-c:a", "aac", "-b:a", self.cfg.audio_bitrate, "-ar", "44100", "-ac", "2",
            # Add buffering for smoother streaming and handling network hiccups
            "-max_delay", buffer_settings["max_delay"],
            "-rtmp_buffer", buffer_settings["buffer_size"],
        ]

        # Add RTMP-specific protocol options if enabled
        out_url = self.cfg.rtmp_url()
        if out_url.lower().startswith(("rtmp://", "rtmps://")) and self.cfg.rtmp_live:
            cmd += ["-rtmp_live", "live", "-rtmp_tcurl", self.cfg.rtmp_base]

        cmd += [
            "-f", "flv", out_url
        ]
        return cmd

    def run_one_video(self, video_id: str):
        """Stream a single video using ffmpeg."""
        # Check if this video was prefetched
        if self._prefetch_video_id == video_id and self._prefetch_vurl:
            self.log.emit(f"[PREFETCH] Using cached data for {video_id}")
            title = self._prefetch_title
            pretty_date = self._prefetch_date
            vurl = self._prefetch_vurl
            aurl = self._prefetch_aurl
            # Clear prefetch cache after use
            self._prefetch_video_id = None
            self._prefetch_title = None
            self._prefetch_date = None
            self._prefetch_vurl = None
            self._prefetch_aurl = None
        else:
            # Not prefetched, fetch normally
            try:
                title, pretty_date = self.get_metadata(video_id)
                vurl, aurl = self.get_stream_urls(video_id)
                self.log.emit(f"[INFO] Video URL obtained successfully for {video_id}")
            except Exception as e:
                self.log.emit(f"[ERROR] Failed to get video info for {video_id}: {e}")
                # Try to check if video is available at all
                url = f"https://www.youtube.com/watch?v={video_id}"
                self.log.emit(f"[INFO] Video might be private, deleted, or region-restricted: {url}")
                return  # Skip this video and continue
        
        # Title + date overlay (truncate title; keep date intact)
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
            # Reset fontsize to default when overlay is disabled
            self.cfg._overlay_fontsize = 24

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
            for line in iter(stream.readline, ""):
                self.log.emit(line.rstrip())

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

        # Wait until ffmpeg finishes or a stop/skip is requested
        while self.ff_proc and self.ff_proc.poll() is None and not (
            self._stop.is_set() or self._skip.is_set()
        ):
            time.sleep(0.2)
        if self._stop.is_set() or self._skip.is_set():
            self._terminate_ff_proc()
        else:
            try:
                if self.ff_proc:
                    self.ff_proc.wait(timeout=1.0)
            except Exception:
                pass

        for t in readers:
            t.join(timeout=0.2)

        # Ensure any buffered ffmpeg output is flushed after the process exits
        if self.ff_proc:
            for stream in (self.ff_proc.stdout, self.ff_proc.stderr):
                if stream:
                    leftover = stream.read()
                    if leftover:
                        for line in leftover.splitlines():
                            self.log.emit(line.rstrip())
                    stream.close()
            self.ff_proc = None
        
        # CRITICAL: Wait for RTMP connection to fully close before starting next video
        # Without this delay, the RTMP server rejects the new connection (only 1 connection per key allowed)
        if not self._stop.is_set():
            time.sleep(2)


    # ---------- main loop ----------
    @QtCore.Slot()
    def run(self):
        """Main worker loop that continually streams the playlist."""
        # Try to self-heal dependencies on Windows
        try:
            self.ensure_binaries()
        except Exception:
            pass

        if not self.ffmpeg_path:
            self.log.emit("[ERROR] ffmpeg not found. Put ffmpeg.exe next to the EXE or in PATH.")
            self.finished.emit()
            return
        if not self.ytdlp_path:
            self.log.emit("[ERROR] yt-dlp.exe not found. Put yt-dlp.exe next to the EXE or in PATH.")
            self.finished.emit()
            return

        self.select_encoder()
        # Cookies/auth disabled
        self.log.emit("[INFO] yt-dlp auth: none")

        # Validate RTMP connectivity with a 1s preflight push
        if not self.preflight_rtmp():
            self.status.emit("Stopped")
            self.finished.emit()
            return
        self.status.emit("Starting…")
        self.log.emit(f"[INFO] Encoder: {self.cfg.encoder_name} ({self.cfg.encoder})")
        self.log.emit(f"[INFO] Playlist: {self.cfg.playlist_url}")
        self.log.emit(f"[INFO] RTMP:     {self.cfg.rtmp_url()}")
        self.log.emit(
            f"[INFO] Output:   {self.cfg.height}p@{self.cfg.fps}  ~{self.cfg.video_bitrate} video + {self.cfg.audio_bitrate} audio\n"
        )

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

                    # Prefetch the next video in the background (if available)
                    if idx < len(ids):
                        next_vid = ids[idx]  # idx is 1-based, so ids[idx] is the next video
                        self.prefetch_next_video(next_vid)

                    try:
                        self.run_one_video(vid)
                    except Exception as e:
                        self.log.emit(f"[WARN] Stream error for video {vid}: {e}")
                        self.log.emit("[INFO] Continuing to next video...")
                        # Add a small delay before trying the next video
                        if not self._stop.is_set():
                            time.sleep(2)

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

QToolTip {
  background: #1a1d21; color: #e6e6e6; border: 1px solid #2a2f36; padding: 4px;
}
"""

class MainWindow(QtWidgets.QWidget):
    """Main application window housing the GUI and controls."""

    RESOLUTION_PRESETS = {
        "480p": (480, "1000k", "2000k"),
        "720p": (720, "2300k", "4600k"),
        "1080p": (1080, "6000k", "9000k"),
        "1440p": (1440, "9000k", "12000k"),
        "2160p": (2160, "35000k", "35000k")
    }
    
    FRAMERATE_OPTIONS = [30, 60]

    stopRequested = QtCore.Signal()

    def __init__(self):
        """Initialise all widgets and connect signals."""
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — YouTube 24/7 VOD Streamer")
        # Provide a sensible minimum size and allow resizing.
        self.setMinimumSize(960, 480)
        self.resize(1200, 700)
        self.worker_thread: Optional[QtCore.QThread] = None
        self.worker: Optional[StreamWorker] = None
        self.streaming = False
        self.log_fh = None
        
        # Update checker components
        self.update_thread: Optional[QtCore.QThread] = None
        self.update_checker: Optional[UpdateChecker] = None

        # Inputs
        self.playlist_edit = QtWidgets.QLineEdit("")
        self.playlist_edit.setPlaceholderText("Your YouTube playlist URL…")
        self.rtmp_edit = QtWidgets.QLineEdit("rtmp://a.rtmp.youtube.com/live2")
        self.rtmp_edit.setPlaceholderText("RTMP ingest URL (e.g., rtmp://a.rtmp.youtube.com/live2)")
        self.key_edit = QtWidgets.QLineEdit("")
        self.key_edit.setPlaceholderText("Your YouTube stream key…")
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.res_combo = QtWidgets.QComboBox()
        self.res_combo.addItems(["480p", "720p", "1080p", "1440p", "2160p"])
        self.res_combo.setCurrentText("720p")
        
        self.fps_combo = QtWidgets.QComboBox()
        self.fps_combo.addItems(["30", "60"])
        self.fps_combo.setCurrentText("30")
        
        self.buffer_combo = QtWidgets.QComboBox()
        self.buffer_combo.addItems(["Low", "Medium", "High", "Ultra"])
        self.buffer_combo.setCurrentText("Medium")
        self.buffer_combo.setToolTip("Buffering helps smooth out network hiccups.\nLow: 3s, Medium: 7s (default), High: 12s, Ultra: 25s")
        
        self.bitrate_edit = QtWidgets.QLineEdit("2300k")
        self.bufsize_edit = QtWidgets.QLineEdit("4600k")

        self.overlay_chk = QtWidgets.QCheckBox("Overlay current VOD title")
        self.overlay_chk.setChecked(True)
        self.shuffle_chk = QtWidgets.QCheckBox("Shuffle playlist order")
        self.logfile_chk = QtWidgets.QCheckBox("Log to file")
        self.remember_chk = QtWidgets.QCheckBox("Save playlist and key")
        self.remember_chk.setChecked(True)
        self.check_updates_startup_chk = QtWidgets.QCheckBox("Check for updates on startup")
        self.check_updates_startup_chk.setChecked(True)

        self.console = QtWidgets.QTextEdit()
        self.console.setReadOnly(True)
        self.console.setVisible(True)

        self.start_btn = QtWidgets.QPushButton("Start Stream")
        self.stop_btn = QtWidgets.QPushButton("Stop Stream")
        self.stop_btn.setEnabled(False)
        self.skip_btn = QtWidgets.QPushButton("Skip Video")
        self.skip_btn.setEnabled(False)
        self.test_rtmp_btn = QtWidgets.QPushButton("Test RTMP")

        # --- Tabs ---
        tabs = QtWidgets.QTabWidget()

        # Stream Tab
        stream_tab = QtWidgets.QWidget()
        stream_layout = QtWidgets.QVBoxLayout(stream_tab)

        stream_form = QtWidgets.QGridLayout()
        stream_form.addWidget(QtWidgets.QLabel("Playlist URL"), 0, 0)
        stream_form.addWidget(self.playlist_edit, 0, 1, 1, 3)
        stream_form.addWidget(QtWidgets.QLabel("Stream URL"), 1, 0)
        stream_form.addWidget(self.rtmp_edit, 1, 1, 1, 3)
        stream_form.addWidget(QtWidgets.QLabel("Stream Key"), 2, 0)
        stream_form.addWidget(self.key_edit, 2, 1, 1, 3)
        stream_layout.addLayout(stream_form)

        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.skip_btn)
        btns.addWidget(self.test_rtmp_btn)
        btns.addStretch(1)
        stream_layout.addLayout(btns)
        tabs.addTab(stream_tab, "Stream")

        # Console Tab
        console_tab = QtWidgets.QWidget()
        console_layout = QtWidgets.QVBoxLayout(console_tab)
        console_layout.addWidget(self.console)
        tabs.addTab(console_tab, "Console")

        # Settings Tab
        settings_tab = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_tab)

        settings_form = QtWidgets.QGridLayout()
        settings_form.addWidget(QtWidgets.QLabel("Resolution"), 0, 0)
        settings_form.addWidget(self.res_combo, 0, 1)
        settings_form.addWidget(QtWidgets.QLabel("Frame Rate"), 0, 2)
        settings_form.addWidget(self.fps_combo, 0, 3)
        settings_form.addWidget(QtWidgets.QLabel("Video Bitrate"), 1, 0)
        settings_form.addWidget(self.bitrate_edit, 1, 1)
        settings_form.addWidget(QtWidgets.QLabel("Buffer Size"), 1, 2)
        settings_form.addWidget(self.bufsize_edit, 1, 3)
        settings_form.addWidget(QtWidgets.QLabel("Stream Buffer"), 2, 0)
        settings_form.addWidget(self.buffer_combo, 2, 1)
        settings_layout.addLayout(settings_form)

        toggles = QtWidgets.QHBoxLayout()
        toggles.addWidget(self.overlay_chk)
        toggles.addWidget(self.shuffle_chk)
        toggles.addWidget(self.logfile_chk)
        self.rtmp_live_chk = QtWidgets.QCheckBox("RTMP live mode (Owncast)")
        self.rtmp_live_chk.setToolTip("Adds -rtmp_live live and tcurl for better compatibility with Owncast and some servers")
        toggles.addWidget(self.rtmp_live_chk)
        toggles.addStretch(1)
        settings_layout.addLayout(toggles)

    # (YouTube auth UI removed)

        bottom_opts = QtWidgets.QHBoxLayout()
        bottom_opts.addWidget(self.remember_chk)
        bottom_opts.addStretch(1)
        settings_layout.addLayout(bottom_opts)

        settings_layout.addStretch(1)
        tabs.addTab(settings_tab, "Settings")

        # About Tab
        about_tab = QtWidgets.QWidget()
        about_layout = QtWidgets.QVBoxLayout(about_tab)
        
        # Version info
        version_layout = QtWidgets.QHBoxLayout()
        about_text = QtWidgets.QLabel(f"<b>{APP_NAME}</b> - YouTube 24/7 VOD Streamer<br>Version {APP_VERSION}")
        about_text.setWordWrap(True)
        version_layout.addWidget(about_text)
        version_layout.addStretch(1)
        
        # Update checker section
        update_group = QtWidgets.QGroupBox("Updates")
        update_group_layout = QtWidgets.QVBoxLayout(update_group)
        
        # Update status and buttons
        update_controls_layout = QtWidgets.QHBoxLayout()
        self.check_update_btn = QtWidgets.QPushButton("Check for Updates")
        self.check_update_btn.clicked.connect(self.check_for_updates)
        update_controls_layout.addWidget(self.check_update_btn)
        self.force_update_btn = QtWidgets.QPushButton("Force Update Binaries (yt-dlp & FFmpeg)")
        self.force_update_btn.setToolTip("Re-download latest yt-dlp.exe and ffmpeg.exe next to the app")
        self.force_update_btn.clicked.connect(self.on_force_update_binaries)
        update_controls_layout.addWidget(self.force_update_btn)
        update_controls_layout.addStretch(1)
        
        # Update status label
        self.update_status_label = QtWidgets.QLabel("Click 'Check for Updates' to check for new versions")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setStyleSheet("color: #888; font-style: italic;")
        
        # Check on startup toggle
        startup_check_layout = QtWidgets.QHBoxLayout()
        startup_check_layout.addWidget(self.check_updates_startup_chk)
        startup_check_layout.addStretch(1)
        
        update_group_layout.addLayout(update_controls_layout)
        update_group_layout.addWidget(self.update_status_label)
        update_group_layout.addLayout(startup_check_layout)
        
        # Credits
        credits_text = QtWidgets.QLabel("Open-source tool created by TheDoctorTTV<br>"
                                       f"<a href='https://github.com/{GITHUB_REPO}' style='color: #5DADE2;'>GitHub Repository</a>")
        credits_text.setWordWrap(True)
        credits_text.setOpenExternalLinks(True)
        
        about_layout.addLayout(version_layout)
        about_layout.addWidget(update_group)
        about_layout.addStretch(1)
        about_layout.addWidget(credits_text)
        tabs.addTab(about_tab, "About")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(tabs)

        # Signals
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        self.skip_btn.clicked.connect(self.on_skip)
        self.test_rtmp_btn.clicked.connect(self.on_test_rtmp)
        self.res_combo.currentIndexChanged.connect(self.on_quality_change)
        self.fps_combo.currentIndexChanged.connect(self.on_quality_change)

        # restore persisted settings before wiring save handlers
        self.on_quality_change()
        self.load_settings()

    # No auth mode toggles

        # persist as you tweak
        self.remember_chk.toggled.connect(lambda _: self.save_settings())
        self.overlay_chk.toggled.connect(lambda _: self.save_settings())
        self.shuffle_chk.toggled.connect(lambda _: self.save_settings())
        self.logfile_chk.toggled.connect(lambda _: self.save_settings())
        if hasattr(self, "rtmp_live_chk"):
            self.rtmp_live_chk.toggled.connect(lambda _: self.save_settings())
        self.check_updates_startup_chk.toggled.connect(lambda _: self.save_settings())
        self.res_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.fps_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.buffer_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.bitrate_edit.textChanged.connect(lambda _: self.save_settings())
        self.bufsize_edit.textChanged.connect(lambda _: self.save_settings())
        self.rtmp_edit.textChanged.connect(lambda _: self.save_settings())
        
        # Check for updates on startup if enabled
        if self.check_updates_startup_chk.isChecked():
            QtCore.QTimer.singleShot(2000, self.check_for_updates_silent)

    # (YouTube auth helpers removed)

    # --- Force update binaries ---
    def on_force_update_binaries(self):
        if self.streaming:
            QtWidgets.QMessageBox.information(self, APP_NAME, "Stop streaming before updating binaries.")
            return
        self.force_update_btn.setEnabled(False)
        self.append_log("[INFO] Forcing binaries update (yt-dlp, FFmpeg)…")

        # Use a tiny one-off worker to reuse ensure_binaries with force=True
        class _Updater(QtCore.QObject):
            done = QtCore.Signal()
            log = QtCore.Signal(str)
            def run(self):
                try:
                    cfg = StreamConfig(playlist_url="", stream_key="")
                    worker = StreamWorker(cfg)
                    worker.log.connect(self.log)
                    worker.ensure_binaries(force=True)
                except Exception as e:
                    self.log.emit(f"[ERROR] Force update failed: {e}")
                finally:
                    self.done.emit()

        self._updater_thread = QtCore.QThread(self)
        self._updater = _Updater()
        self._updater.moveToThread(self._updater_thread)
        self._updater_thread.started.connect(self._updater.run)
        self._updater.log.connect(self.append_log)
        self._updater.done.connect(self._updater_thread.quit)
        def _finish():
            self.force_update_btn.setEnabled(True)
            self.append_log("[INFO] Binaries update finished.")
            self._updater.deleteLater()
            self._updater_thread.deleteLater()
        self._updater_thread.finished.connect(_finish)
        self._updater_thread.start()

    # --- settings (config.json) ---
    def load_settings(self):
        """Restore persisted settings from ``config.json``."""
        cfg = load_config_json()
        remember = str(cfg.get("remember", True)).lower() in ("1", "true", "yes", "on")
        self.remember_chk.setChecked(remember)

        if remember:
            self.playlist_edit.setText(cfg.get("playlist_url", ""))
            self.rtmp_edit.setText(cfg.get("rtmp_base", "rtmp://a.rtmp.youtube.com/live2"))
            self.key_edit.setText(cfg.get("stream_key", ""))

        self.overlay_chk.setChecked(bool(cfg.get("overlay_titles", True)))
        self.shuffle_chk.setChecked(bool(cfg.get("shuffle", False)))
        self.logfile_chk.setChecked(bool(cfg.get("log_to_file", False)))
        self.check_updates_startup_chk.setChecked(bool(cfg.get("check_updates_startup", True)))
        # RTMP live mode compatibility toggle
        try:
            self.rtmp_live_chk.setChecked(bool(cfg.get("rtmp_live", False)))
        except Exception:
            pass

        if "resolution" in cfg:
            idx = self.res_combo.findText(cfg["resolution"])
            if idx >= 0:
                self.res_combo.setCurrentIndex(idx)
        
        if "framerate" in cfg:
            idx = self.fps_combo.findText(str(cfg["framerate"]))
            if idx >= 0:
                self.fps_combo.setCurrentIndex(idx)
        
        if "buffer_mode" in cfg:
            idx = self.buffer_combo.findText(cfg["buffer_mode"])
            if idx >= 0:
                self.buffer_combo.setCurrentIndex(idx)
                
        if "video_bitrate" in cfg:
            self.bitrate_edit.setText(cfg["video_bitrate"])
        if "bufsize" in cfg:
            self.bufsize_edit.setText(cfg["bufsize"])

    def save_settings(self):
        """Persist user settings to ``config.json``."""
        data = load_config_json()
        data.update({
            "remember": self.remember_chk.isChecked(),
            "overlay_titles": self.overlay_chk.isChecked(),
            "shuffle": self.shuffle_chk.isChecked(),
            "log_to_file": self.logfile_chk.isChecked(),
            "rtmp_live": (self.rtmp_live_chk.isChecked() if hasattr(self, "rtmp_live_chk") and self.rtmp_live_chk is not None else False),
            "check_updates_startup": self.check_updates_startup_chk.isChecked(),
            "resolution": self.res_combo.currentText(),
            "framerate": int(self.fps_combo.currentText()),
            "buffer_mode": self.buffer_combo.currentText(),
            "video_bitrate": self.bitrate_edit.text().strip(),
            "bufsize": self.bufsize_edit.text().strip(),
        })
        if self.remember_chk.isChecked():
            data["playlist_url"] = self.playlist_edit.text().strip()
            data["rtmp_base"] = self.rtmp_edit.text().strip()
            data["stream_key"] = self.key_edit.text().strip()
        else:
            data.pop("playlist_url", None)
            data.pop("rtmp_base", None)
            data.pop("stream_key", None)
        save_config_json(data)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Persist settings when the window is closed."""
        self.save_settings()
        return super().closeEvent(event)

    # --- UI helpers ---
    def append_log(self, text: str):
        """Append text to the on-screen console and optional log file."""
        self.console.append(text)
        self.console.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        if self.log_fh:
            try:
                self.log_fh.write(text + "\n")
                self.log_fh.flush()
            except Exception:
                pass

    def on_quality_change(self):
        """Update internal FPS/height presets when the quality dropdown changes."""
        resolution = self.res_combo.currentText()
        framerate = int(self.fps_combo.currentText())
        
        height, suggested_bitrate, suggested_bufsize = self.RESOLUTION_PRESETS.get(
            resolution, self.RESOLUTION_PRESETS["720p"]
        )
        
        self._fps = framerate
        self._height = height
        
        # Only update bitrate/bufsize if not currently streaming
        if not self.streaming:
            self.bitrate_edit.setText(suggested_bitrate)
            self.bufsize_edit.setText(suggested_bufsize)

    def make_config(self) -> StreamConfig:
        """Create a StreamConfig from the current UI state."""
        return StreamConfig(
            playlist_url=self.playlist_edit.text().strip(),
            stream_key=self.key_edit.text().strip(),
            rtmp_base=self.rtmp_edit.text().strip(),
            fps=self._fps,
            height=self._height,
            video_bitrate=self.bitrate_edit.text().strip(),
            bufsize=self.bufsize_edit.text().strip(),
            audio_bitrate="128k",
            overlay_titles=self.overlay_chk.isChecked(),
            shuffle=self.shuffle_chk.isChecked(),
            title_file="current_title.txt",
            rtmp_live=(self.rtmp_live_chk.isChecked() if hasattr(self, "rtmp_live_chk") and self.rtmp_live_chk is not None else False),
            buffer_mode=self.buffer_combo.currentText(),
        )

    # --- update checker ---
    def check_for_updates(self):
        """Manually check for updates (triggered by button click)."""
        if self.update_thread and self.update_thread.isRunning():
            return  # Already checking
            
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("Checking...")
        self.update_status_label.setText("Checking for updates...")
        self.update_status_label.setStyleSheet("color: #888; font-style: italic;")
        
        self._start_update_check()
    
    def check_for_updates_silent(self):
        """Silently check for updates on startup."""
        if self.update_thread and self.update_thread.isRunning():
            return  # Already checking
            
        self._start_update_check()
    
    def _start_update_check(self):
        """Start the update checking process in a background thread."""
        self.update_thread = QtCore.QThread(self)
        self.update_checker = UpdateChecker()
        self.update_checker.moveToThread(self.update_thread)
        
        self.update_thread.started.connect(self.update_checker.check_for_updates)
        self.update_checker.update_checked.connect(self._on_update_checked)
        self.update_checker.error_occurred.connect(self._on_update_error)
        self.update_checker.update_checked.connect(self.update_thread.quit)
        self.update_checker.error_occurred.connect(self.update_thread.quit)
        self.update_thread.finished.connect(self._on_update_check_finished)
        
        self.update_thread.start()
    
    def _on_update_checked(self, update_info: dict):
        """Handle successful update check."""
        current_version = update_info.get('current_version', APP_VERSION)
        latest_version = update_info.get('latest_version', 'Unknown')
        is_newer = update_info.get('is_newer', False)
        release_url = update_info.get('release_url', '')
        download_url = update_info.get('download_url', '')
        published_date = update_info.get('published_date', '')
        
        if is_newer:
            # Update available
            self.update_status_label.setText(
                f"<b>Update available!</b> v{latest_version} "
                f"(Current: v{current_version})"
            )
            self.update_status_label.setStyleSheet("color: #5DADE2; font-weight: bold;")
            
            # Show update dialog
            self._show_update_dialog(update_info)
        else:
            # Up to date
            self.update_status_label.setText(f"You're up to date! (v{current_version})")
            self.update_status_label.setStyleSheet("color: #58D68D; font-weight: bold;")
    
    def _on_update_error(self, error_message: str):
        """Handle update check error."""
        self.update_status_label.setText(f"Update check failed: {error_message}")
        self.update_status_label.setStyleSheet("color: #E74C3C; font-style: italic;")
    
    def _on_update_check_finished(self):
        """Re-enable the check button after update check completes."""
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("Check for Updates")
        
        # Clean up
        if self.update_thread:
            self.update_thread.deleteLater()
            self.update_thread = None
        if self.update_checker:
            self.update_checker.deleteLater()
            self.update_checker = None
    
    def _show_update_dialog(self, update_info: dict):
        """Show a dialog with update information."""
        latest_version = update_info.get('latest_version', 'Unknown')
        release_name = update_info.get('release_name', '')
        release_notes = update_info.get('release_notes', '')
        release_url = update_info.get('release_url', '')
        download_url = update_info.get('download_url', '')
        published_date = update_info.get('published_date', '')
        
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Update Available - {APP_NAME}")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # Header
        header = QtWidgets.QLabel(f"<h2>Update Available!</h2>")
        layout.addWidget(header)
        
        # Version info
        version_text = f"<b>Current Version:</b> {APP_VERSION}<br>"
        version_text += f"<b>Latest Version:</b> {latest_version}"
        if published_date:
            version_text += f"<br><b>Released:</b> {published_date}"
        
        version_label = QtWidgets.QLabel(version_text)
        layout.addWidget(version_label)
        
        # Release notes
        if release_notes:
            notes_label = QtWidgets.QLabel("<b>Release Notes:</b>")
            layout.addWidget(notes_label)
            
            notes_text = QtWidgets.QTextEdit()
            notes_text.setPlainText(release_notes)
            notes_text.setMaximumHeight(200)
            notes_text.setReadOnly(True)
            layout.addWidget(notes_text)
        
        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        if download_url:
            download_btn = QtWidgets.QPushButton("Download Update")
            download_btn.clicked.connect(lambda: self._open_url(download_url))
            button_layout.addWidget(download_btn)
        
        if release_url:
            view_btn = QtWidgets.QPushButton("View on GitHub")
            view_btn.clicked.connect(lambda: self._open_url(release_url))
            button_layout.addWidget(view_btn)
        
        button_layout.addStretch()
        
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _open_url(self, url: str):
        """Open URL in default browser."""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    # --- start/stop wiring ---
    def on_start(self):
        """Validate input and start the background streaming worker."""
        if self.streaming:
            return
        cfg = self.make_config()
        if not cfg.playlist_url or not cfg.rtmp_base or not cfg.stream_key:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Please enter Playlist URL, Stream URL, and Stream Key.")
            return

        # save settings right when starting (if 'remember' is checked)
        self.save_settings()

        if self.log_fh:
            try:
                self.log_fh.close()
            except Exception:
                pass
            self.log_fh = None
        if self.logfile_chk.isChecked():
            log_path = _app_dir() / "latest.log"
            try:
                if log_path.exists():
                    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    log_path.rename(log_path.with_name(f"{log_path.stem}-{ts}{log_path.suffix}"))
                self.log_fh = log_path.open("w", encoding="utf-8")
            except Exception:
                self.log_fh = None

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
        """Stop the streaming worker gracefully."""
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
        """Skip the current video."""
        if not self.streaming or not self.worker:
            return
        self.append_log("[INFO] Skipping…")
        try:
            self.worker.skip()
        except Exception:
            pass

    def on_test_rtmp(self):
        """Run a 1-second RTMP preflight without starting the full stream."""
        if self.streaming:
            QtWidgets.QMessageBox.information(self, APP_NAME, "Stop streaming to test RTMP.")
            return
        cfg = self.make_config()
        if not cfg.rtmp_base or not cfg.stream_key:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Please enter Stream URL and Stream Key to test RTMP.")
            return
        self.test_rtmp_btn.setEnabled(False)
        self.append_log("[INFO] Testing RTMP connectivity…")

        class _RTMPTester(QtCore.QObject):
            done = QtCore.Signal(bool)
            log = QtCore.Signal(str)
            def __init__(self, cfg: StreamConfig):
                super().__init__()
                self.cfg = cfg
            def run(self):
                ok = False
                try:
                    worker = StreamWorker(self.cfg)
                    worker.log.connect(self.log)
                    worker.ensure_binaries()
                    worker.select_encoder()
                    ok = worker.preflight_rtmp()
                except Exception as e:
                    self.log.emit(f"[ERROR] RTMP test failed: {e}")
                finally:
                    self.done.emit(ok)

        self._rtmp_thread = QtCore.QThread(self)
        self._rtmp_worker = _RTMPTester(cfg)
        self._rtmp_worker.moveToThread(self._rtmp_thread)
        self._rtmp_thread.started.connect(self._rtmp_worker.run)
        self._rtmp_worker.log.connect(self.append_log)
        def _finish(ok: bool):
            if ok:
                self.append_log("[INFO] RTMP test succeeded.")
            else:
                self.append_log("[WARN] RTMP test failed. Check URL/port/app/key and TLS (rtmp vs rtmps).")
            self.test_rtmp_btn.setEnabled(True)
            self._rtmp_worker.deleteLater()
            self._rtmp_thread.deleteLater()
        self._rtmp_worker.done.connect(_finish)
        self._rtmp_thread.start()

    def on_finished(self):
        """Cleanup once the worker thread stops."""
        self.append_log("[INFO] Worker finished.")
        if self.log_fh:
            try:
                self.log_fh.close()
            except Exception:
                pass
            self.log_fh = None
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
    """Entry point to launch the Qt application."""
    # Ensure taskbar groups under our app and can show our icon (Windows)
    if IS_WIN:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)

    # Set environment variable for high DPI support before creating QApplication
    os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'

    app = QtWidgets.QApplication(sys.argv)

    # Load .ico (next to EXE when frozen, or cwd when running from source)
    icon = QtGui.QIcon(resource_path("icon.ico"))
    app.setWindowIcon(icon)  # taskbar/dock icon

    app.setStyleSheet(DARK_QSS)
    w = MainWindow()
    w.setWindowIcon(icon)    # title-bar icon
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
