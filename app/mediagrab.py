"""
MediaGrab - grab audio/video from the browser at the best available quality.

Windows desktop app. Works with the MediaGrab Opera extension: the extension
sends the page / stream URL to this app over http://127.0.0.1:47811, and the
app uses yt-dlp (+ ffmpeg) to pick and download the highest-quality version.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "MediaGrab"
APP_VERSION = "1.0.0"
PORT = 47811

# --------------------------------------------------------------------------
# Paths: make bundled ffmpeg.exe / deno.exe (next to MediaGrab.exe) visible
# --------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
os.environ["PATH"] = str(APP_DIR) + os.pathsep + os.environ.get("PATH", "")

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_CONFIG = {
    "download_dir": str(Path.home() / "Downloads" / APP_NAME),
    "audio_mp3": False,          # False = keep original best audio (no re-encode)
    "use_opera_cookies": False,  # for videos that need you to be logged in
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def find_tool(name):
    """Return full path of a tool (ffmpeg, deno) if found next to the app or on PATH."""
    from shutil import which
    exe = name + (".exe" if os.name == "nt" else "")
    local = APP_DIR / exe
    if local.exists():
        return str(local)
    return which(name)


# --------------------------------------------------------------------------
# Quality description helpers
# --------------------------------------------------------------------------
def pretty_codec(codec):
    if not codec or codec == "none":
        return ""
    c = codec.lower()
    table = [
        ("avc", "H.264"), ("h264", "H.264"), ("hev", "H.265"), ("hvc", "H.265"),
        ("h265", "H.265"), ("vp09", "VP9"), ("vp9", "VP9"), ("vp8", "VP8"),
        ("av01", "AV1"), ("av1", "AV1"), ("mp4a", "AAC"), ("aac", "AAC"),
        ("opus", "Opus"), ("vorbis", "Vorbis"), ("mp3", "MP3"), ("flac", "FLAC"),
        ("ac-3", "AC3"), ("ec-3", "E-AC3"),
    ]
    for key, name in table:
        if c.startswith(key):
            return name
    return codec.split(".")[0].upper()


def human_size(n):
    if not n:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def describe_selection(info):
    """Build a 'best quality' label + total size from a yt-dlp info dict."""
    fmts = info.get("requested_formats") or [info]
    video = next((f for f in fmts if f.get("vcodec") not in (None, "none")), None)
    audio = next((f for f in fmts if f.get("acodec") not in (None, "none")), None)
    parts = []
    if video:
        h = video.get("height")
        res = f"{h}p" if h else (video.get("resolution") or "video")
        fps = video.get("fps")
        if fps and fps > 30:
            res += f"{round(fps)}"
        if (video.get("dynamic_range") or "SDR") != "SDR":
            res += " " + video["dynamic_range"]
        vc = pretty_codec(video.get("vcodec"))
        parts.append(f"{res} {vc}".strip())
    if audio:
        ac = pretty_codec(audio.get("acodec"))
        abr = audio.get("abr")
        a = ac or "audio"
        if abr:
            a += f" {round(abr)}k"
        parts.append(a)
    size = 0
    for f in fmts:
        size += f.get("filesize") or f.get("filesize_approx") or 0
    return " + ".join(parts) or "best available", size


# --------------------------------------------------------------------------
# Download job
# --------------------------------------------------------------------------
class Cancelled(Exception):
    pass


class Job:
    def __init__(self, url, mode="video", referer=None, title=None):
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.mode = mode            # "video" or "audio"
        self.referer = referer
        self.page_title = title
        self.title = title or url
        self.quality = "Analyzing…"
        self.size = None
        self.status = "Queued"
        self.filepath = None
        self.cancel = False
        self.done = False


def build_ydl_opts(job, cfg, hook, pp_hook):
    out_dir = cfg["download_dir"]
    opts = {
        "paths": {"home": out_dir},
        "outtmpl": "%(title).150B [%(id)s].%(ext)s" if job.mode == "video"
                   else "%(title).150B.%(ext)s",
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "concurrent_fragment_downloads": 8,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
    }
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if job.referer:
        opts["http_headers"] = {"Referer": job.referer}
    if cfg.get("use_opera_cookies"):
        opts["cookiesfrombrowser"] = ("opera",)

    if job.mode == "audio":
        opts["format"] = "bestaudio/best"
        if cfg.get("audio_mp3"):
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                       "preferredcodec": "mp3", "preferredquality": "0"}]
        else:
            # 'best' = keep the original audio stream as-is (no quality loss)
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                       "preferredcodec": "best"}]
    else:
        # Highest resolution video + best audio, merged. Falls back to the
        # best single file when a site doesn't offer separate streams.
        opts["format"] = "bestvideo*+bestaudio/best"
        opts["merge_output_format"] = "mp4/mkv"   # mp4 when codecs allow, else mkv
    return opts


def run_job(job, cfg, notify):
    """Runs in a worker thread. notify(job) pushes UI updates."""
    import yt_dlp

    state = {"part": ""}

    def hook(d):
        if job.cancel:
            raise Cancelled()
        if d.get("status") == "downloading":
            info = d.get("info_dict") or {}
            if job.mode == "video":
                if info.get("vcodec") not in (None, "none") and info.get("acodec") in (None, "none"):
                    state["part"] = "video "
                elif info.get("vcodec") in (None, "none") and info.get("acodec") not in (None, "none"):
                    state["part"] = "audio "
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            got = d.get("downloaded_bytes") or 0
            speed = d.get("speed")
            pct = f"{got * 100 / total:.0f}%" if total else human_size(got)
            sp = f" · {human_size(speed)}/s" if speed else ""
            job.status = f"Downloading {state['part']}{pct}{sp}"
            notify(job)
        elif d.get("status") == "finished":
            job.filepath = d.get("filename") or job.filepath

    def pp_hook(d):
        if d.get("status") == "started":
            name = d.get("postprocessor", "")
            job.status = "Merging…" if "Merger" in name else "Processing…"
            notify(job)
        elif d.get("status") == "finished":
            fp = (d.get("info_dict") or {}).get("filepath")
            if fp:
                job.filepath = fp

    try:
        Path(cfg["download_dir"]).mkdir(parents=True, exist_ok=True)
        opts = build_ydl_opts(job, cfg, hook, pp_hook)
        job.status = "Analyzing…"
        notify(job)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=False)
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                info = entries[0] if entries else info
            # Raw stream links (m3u8/mp4) get names like "master" – use the page title instead
            if job.page_title and (info.get("extractor_key") or "").lower() in ("generic", "html5"):
                info["title"] = job.page_title
            job.title = info.get("title") or job.title
            job.quality, job.size = describe_selection(info)
            if job.mode == "audio":
                job.quality = "Audio: " + job.quality.split(" + ")[-1]
            notify(job)
            if job.cancel:
                raise Cancelled()
            ydl.process_ie_result(info, download=True)
            fp = None
            for rf in info.get("requested_downloads") or []:
                fp = rf.get("filepath") or fp
            job.filepath = fp or job.filepath
        job.status = "✔ Done"
    except Cancelled:
        job.status = "Cancelled"
    except Exception as e:  # yt-dlp errors -> friendly text
        msg = str(e)
        low = msg.lower()
        if "drm" in low:
            job.status = "✖ DRM-protected – can't be downloaded"
        elif "sign in" in low or "login" in low or "cookies" in low:
            job.status = "✖ Needs login – tick 'Use my Opera login'"
        elif "unsupported url" in low:
            job.status = "✖ No downloadable media found on this page"
        elif "ffmpeg" in low:
            job.status = "✖ ffmpeg missing – put ffmpeg.exe next to MediaGrab.exe"
        else:
            job.status = "✖ " + msg.replace("ERROR: ", "")[:160]
    job.done = True
    notify(job)


# --------------------------------------------------------------------------
# Local server the Opera extension talks to
# --------------------------------------------------------------------------
class ExtensionServer:
    def __init__(self, on_grab):
        self.on_grab = on_grab
        self.httpd = None

    def start(self):
        on_grab = self.on_grab

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _origin_ok(self):
                # Only the browser extension (or local tools) may talk to us.
                # Normal websites always send their own Origin and are refused.
                origin = self.headers.get("Origin")
                return origin is None or origin.startswith(
                    ("chrome-extension://", "moz-extension://"))

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                origin = self.headers.get("Origin")
                if origin and self._origin_ok():
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                if not self._origin_ok():
                    return self._send(403, {"ok": False})
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))
                self.send_header("Access-Control-Allow-Methods", "GET, POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self):
                if not self._origin_ok():
                    return self._send(403, {"ok": False})
                if self.path.startswith("/ping"):
                    return self._send(200, {"ok": True, "app": APP_NAME, "version": APP_VERSION})
                self._send(404, {"ok": False})

            def do_POST(self):
                if not self._origin_ok():
                    return self._send(403, {"ok": False, "error": "forbidden"})
                if not self.path.startswith("/grab"):
                    return self._send(404, {"ok": False})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 100_000:
                        raise ValueError("too large")
                    data = json.loads(self.rfile.read(n) or b"{}")
                    url = str(data.get("url") or "").strip()
                    if not url.startswith(("http://", "https://")):
                        raise ValueError("bad url")
                    mode = "audio" if data.get("mode") == "audio" else "video"
                    on_grab(url, mode, data.get("referer"), data.get("title"))
                    self._send(200, {"ok": True})
                except Exception as e:
                    self._send(400, {"ok": False, "error": str(e)})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    cfg = load_config()
    jobs = {}
    ui_queue = queue.Queue()

    root = tk.Tk()
    root.title(f"{APP_NAME} – best-quality audio & video grabber")
    root.geometry("980x520")
    root.minsize(760, 380)
    try:
        if getattr(sys, "frozen", False):
            root.iconbitmap(sys.executable)
    except Exception:
        pass

    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass
    style.configure("Treeview", rowheight=26)

    # ---- top: paste a link ----
    top = ttk.Frame(root, padding=(10, 10, 10, 4))
    top.pack(fill="x")
    ttk.Label(top, text="Link:").pack(side="left")
    url_var = tk.StringVar()
    url_entry = ttk.Entry(top, textvariable=url_var)
    url_entry.pack(side="left", fill="x", expand=True, padx=6)

    def add_from_entry(mode):
        u = url_var.get().strip()
        if not u.startswith(("http://", "https://")):
            messagebox.showinfo(APP_NAME, "Paste a link starting with http:// or https://")
            return
        url_var.set("")
        start_job(u, mode, None, None)

    ttk.Button(top, text="⬇ Best video", command=lambda: add_from_entry("video")).pack(side="left")
    ttk.Button(top, text="♪ Best audio", command=lambda: add_from_entry("audio")).pack(side="left", padx=(6, 0))

    # ---- settings row ----
    opts_row = ttk.Frame(root, padding=(10, 0, 10, 6))
    opts_row.pack(fill="x")
    dir_var = tk.StringVar(value=cfg["download_dir"])
    ttk.Label(opts_row, text="Save to:").pack(side="left")
    ttk.Label(opts_row, textvariable=dir_var, foreground="#555").pack(side="left", padx=6)

    def choose_dir():
        d = filedialog.askdirectory(initialdir=cfg["download_dir"])
        if d:
            cfg["download_dir"] = d
            dir_var.set(d)
            save_config(cfg)

    def open_dir():
        Path(cfg["download_dir"]).mkdir(parents=True, exist_ok=True)
        os.startfile(cfg["download_dir"]) if os.name == "nt" else None

    ttk.Button(opts_row, text="Change…", command=choose_dir).pack(side="left")
    ttk.Button(opts_row, text="Open folder", command=open_dir).pack(side="left", padx=(6, 0))

    mp3_var = tk.BooleanVar(value=cfg.get("audio_mp3", False))
    cookies_var = tk.BooleanVar(value=cfg.get("use_opera_cookies", False))

    def save_opts():
        cfg["audio_mp3"] = mp3_var.get()
        cfg["use_opera_cookies"] = cookies_var.get()
        save_config(cfg)

    ttk.Checkbutton(opts_row, text="Use my Opera login (for members-only videos)",
                    variable=cookies_var, command=save_opts).pack(side="right")
    ttk.Checkbutton(opts_row, text="Convert audio to MP3",
                    variable=mp3_var, command=save_opts).pack(side="right", padx=10)

    # ---- job list ----
    mid = ttk.Frame(root, padding=(10, 0, 10, 0))
    mid.pack(fill="both", expand=True)
    cols = ("title", "quality", "size", "status")
    tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
    for c, label, w, stretch in [("title", "Title", 330, True), ("quality", "Best quality found", 230, False),
                                 ("size", "Size", 80, False), ("status", "Status", 280, False)]:
        tree.heading(c, text=label)
        tree.column(c, width=w, stretch=stretch, anchor="w")
    sb = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    # ---- bottom buttons ----
    bottom = ttk.Frame(root, padding=10)
    bottom.pack(fill="x")

    def selected_jobs():
        return [jobs[i] for i in tree.selection() if i in jobs]

    def show_file():
        for j in selected_jobs():
            if j.filepath and Path(j.filepath).exists() and os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(Path(j.filepath))])
                return
        open_dir()

    def cancel_sel():
        for j in selected_jobs():
            j.cancel = True

    def clear_done():
        for jid, j in list(jobs.items()):
            if j.done:
                tree.delete(jid)
                del jobs[jid]

    ttk.Button(bottom, text="Show file", command=show_file).pack(side="left")
    ttk.Button(bottom, text="Cancel", command=cancel_sel).pack(side="left", padx=6)
    ttk.Button(bottom, text="Clear finished", command=clear_done).pack(side="left")
    status_var = tk.StringVar()
    ttk.Label(bottom, textvariable=status_var, foreground="#555").pack(side="right")
    tree.bind("<Double-1>", lambda e: show_file())

    # ---- job plumbing ----
    def notify(job):
        ui_queue.put(job.id)

    def start_job(url, mode, referer, title):
        job = Job(url, mode, referer, title)
        jobs[job.id] = job
        tree.insert("", 0, iid=job.id, values=(job.title, job.quality, "", job.status))
        threading.Thread(target=run_job, args=(job, dict(cfg), notify), daemon=True).start()

    def pump():
        seen = set()
        try:
            while True:
                seen.add(ui_queue.get_nowait())
        except queue.Empty:
            pass
        for jid in seen:
            j = jobs.get(jid)
            if j and tree.exists(jid):
                tree.item(jid, values=(j.title, j.quality, human_size(j.size) if j.size else "", j.status))
        root.after(150, pump)

    def on_grab(url, mode, referer, title):
        # called from server thread -> hop to the UI thread
        def go():
            start_job(url, mode, referer, title)
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.after(400, lambda: root.attributes("-topmost", False))
        root.after(0, go)

    server = ExtensionServer(on_grab)
    try:
        server.start()
        tools = []
        tools.append("ffmpeg ✔" if find_tool("ffmpeg") else "ffmpeg ✖ (merging won't work)")
        tools.append("deno ✔" if find_tool("deno") else "deno ✖ (YouTube may be limited)")
        status_var.set(f"Opera extension link: ready on port {PORT}  ·  " + "  ·  ".join(tools))
    except OSError:
        messagebox.showinfo(APP_NAME, "MediaGrab is already running (check the taskbar).")
        root.destroy()
        return

    url_entry.focus_set()
    root.after(150, pump)

    def on_close():
        running = [j for j in jobs.values() if not j.done]
        if running and not messagebox.askyesno(APP_NAME, f"{len(running)} download(s) still running. Quit anyway?"):
            return
        server.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# --------------------------------------------------------------------------
def selftest():
    """Used by the build pipeline: checks everything is bundled correctly."""
    lines = []
    ok = True
    try:
        import yt_dlp
        lines.append(f"yt-dlp {yt_dlp.version.__version__}")
    except Exception as e:
        ok = False
        lines.append(f"yt-dlp MISSING: {e}")
    try:
        import yt_dlp_ejs  # noqa: F401
        lines.append("yt-dlp-ejs OK")
    except Exception as e:
        ok = False
        lines.append(f"yt-dlp-ejs MISSING: {e}")
    for t in ("ffmpeg", "deno"):
        p = find_tool(t)
        lines.append(f"{t}: {p or 'NOT FOUND'}")
        ok = ok and bool(p)
    lines.append("RESULT: " + ("PASS" if ok else "FAIL"))
    (APP_DIR / "selftest.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run_gui()
