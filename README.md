# MediaGrab

A Windows app and Opera extension that find video and audio on the page you're watching and download it at the **highest quality the site offers** (for example 4K/8K, 60fps or HDR video merged with the best audio track, or audio only).

## What's in the box

| Part | What it does |
|---|---|
| `MediaGrab.exe` | The app. Shows the best quality found (e.g. `2160p60 HDR10 AV1 + Opus 160k`), its size and download progress, and saves to `Downloads\MediaGrab`. |
| `opera-extension/` | Lives in Opera's toolbar. Spots media files and streams (MP4, WebM, MP3, M4A, HLS `.m3u8`, DASH `.mpd`) as the page plays and sends your pick to the app. |
| `ffmpeg.exe`, `ffprobe.exe` | Join the separate best-video and best-audio streams into one file. |
| `deno.exe` | Lets the engine unlock YouTube's top-quality formats. |

## 1 · Get the .exe (GitHub build route)

1. Create a new GitHub repository (e.g. `MediaGrab`) and upload **everything in this folder**, including the hidden `.github` folder.
2. Go to the repo's **Actions** tab. The build starts automatically, or you can click **Build MediaGrab (Windows) → Run workflow**.
3. After about 3–5 minutes, open the finished run and download **MediaGrab-windows** under *Artifacts*. Unzip it anywhere, e.g. `C:\Apps\MediaGrab`.

The build also re-runs every Monday, so the download engine keeps up with site changes. If a site stops working, download the newest artifact.

## 2 · Install the extension in Opera (or Opera GX)

1. Go to `opera://extensions` in the address bar.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose the `opera-extension` folder from the unzipped download.
4. Pin MediaGrab: click the cube/extensions icon in the sidebar or toolbar and pin the red ⬇ icon.

> Keep the `opera-extension` folder where it is. Opera loads it from that folder.

## 3 · Use it

1. Open **MediaGrab.exe**. The bottom bar should show `ffmpeg ✔ · deno ✔`.
2. In Opera, open a page with a video and **press play**.
3. Click the **MediaGrab** icon:
   - **This page → ⬇ Best video / ♪ Best audio**: the best choice for YouTube, Instagram, Facebook, TikTok, X, Vimeo, SoundCloud and other well-known sites (1,000+ supported).
   - **Detected media**: raw files and streams caught on the current page. Useful for sites the engine doesn't know. Streams always come down at their top quality.
4. You can also **right-click** a video, a link or the page and choose *MediaGrab: download best video/audio*.
5. Or paste a link straight into the app.

The red number on the icon shows how many media items were found on the current tab.

## Settings (in the app)

- **Save to:** choose the download folder.
- **Convert audio to MP3:** off by default, so audio keeps its original best stream with no quality loss (usually Opus or AAC). Turn it on if you need MP3 for an older device. It converts at the highest MP3 quality.
- **Use my Opera login:** for videos you can only watch while logged in. Close Opera first if the app reports a cookie error.

## Good to know

- **Video format:** files are saved as MP4 when the best streams fit in MP4. Otherwise they're saved as MKV, because the very highest qualities (AV1/VP9 + Opus) often don't fit in MP4. VLC plays everything.
- **Can't be downloaded:** streams protected with DRM (Netflix, Prime Video, Disney+, Spotify, Apple Music, etc.). The app tells you when a video is DRM-protected.
- **Privacy:** the app only accepts requests from the extension on your own PC (`127.0.0.1`). Websites can't trigger downloads.
- Only download content you own or have permission to save, and follow each site's terms.

## Troubleshooting

| Problem | Fix |
|---|---|
| Popup says *app not running* | Open MediaGrab.exe first. If Windows Firewall asks, *Private networks* is enough. |
| *Detected media* is empty | Press play and wait a few seconds, then reopen the popup. For big sites, use **This page**. |
| `ffmpeg ✖` in the app | Put `ffmpeg.exe` and `ffprobe.exe` in the same folder as `MediaGrab.exe`. |
| YouTube only gives low quality | Make sure `deno.exe` is next to `MediaGrab.exe`, and download the latest build. |
| *Needs login* | Tick **Use my Opera login**, close Opera, and try again. |

## Run from source (optional)

```
pip install -U "yt-dlp[default]"
python app/mediagrab.py
```
