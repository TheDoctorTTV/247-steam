# Stream247 — 24/7 VOD to any RTMP/RTMPS

Loop a **public or unlisted YouTube playlist** as a **24/7 livestream** to
virtually any RTMP/RTMPS ingest: **YouTube Live, Twitch, Facebook Live, Owncast,
Restream, custom Nginx-RTMP**, and more.\
Stream247 automatically selects the best encoder available (NVENC/QSV/AMF with
x264 fallback).

## Features

- Stream any **public or unlisted YouTube playlist** to **any RTMP/RTMPS
  endpoint**
  - Examples: YouTube Live, Twitch, Facebook Live, Owncast, Restream, custom
    RTMP servers
- Supports **480p, 720p, 1080p, 1440p & 4K** output (configurable)
- Adjustable **bitrate** and **buffer size**
- Options to:
  - Overlay current VOD title
  - Shuffle playlist order
  - Save playlist & key to config file
  - Test your RTMP endpoint with a 1s preflight push
- Smart encoder selection: **NVENC > QSV > AMF > x264** automatically
- RTMP and RTMPS supported; optional RTMP "live mode" for Owncast compatibility

## Usage

1. Paste your **YouTube playlist URL** (input source).
2. Enter your **RTMP/RTMPS ingest URL** for the destination (e.g.,
   `rtmp://a.rtmp.youtube.com/live2`, `rtmp://live.twitch.tv/app`,
   `rtmps://live-api-s.facebook.com:443/rtmp/`).
3. Enter your destination **Stream Key**.
4. Choose your desired **quality** and **bitrate**.
5. (Optional) Enable extras like overlays, shuffle, or "RTMP live mode"
   (Owncast).
6. (Optional) Click **Test RTMP** to verify connectivity.
7. Click **Start Stream** to go live!

## Example

<img src="https://cdn.thetimevortex.net/stream247-v1.3-screenshot.png" alt="screenshot" width="500">

## Notes

- Destinations: Any platform that accepts **RTMP or RTMPS** should work. Check
  your platform’s recommended bitrates and resolutions.
- Input source: Your YouTube playlist must be **public or unlisted** for the app
  to fetch videos with yt-dlp.
- Windows: If not found, the app can auto-download **FFmpeg** and **yt-dlp**
  next to the executable.
- Owncast: Enable the "RTMP live mode" toggle for improved compatibility.
- Networking: Ensure outbound traffic to your ingest host/port (commonly 1935
  for RTMP or 443 for RTMPS) is allowed.
- Designed for **24/7 operation** — great for music channels, replay loops, or
  archives.

### Current scope

Today, Stream247 uses a YouTube playlist as the input source and streams the
output to your chosen RTMP/RTMPS destination. Additional input sources may be
considered in the future.

---

## Creator

**TheDoctorTTV**\
<img src="https://github.com/TheDoctorTTV.png?size=80" alt="avatar">
