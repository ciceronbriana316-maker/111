import hmac
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import anthropic
from flask import Flask, jsonify, request, send_from_directory
from youtube_transcript_api.proxies import GenericProxyConfig
from youtube_transcript_api import YouTubeTranscriptApi

BASE = Path(__file__).parent
CLIPS_DIR = BASE / "clips"
CLIPS_DIR.mkdir(exist_ok=True)

MODEL = os.environ.get("CLIPFINDER_MODEL", "claude-sonnet-5-5")
app = Flask(__name__, static_folder=str(BASE / "static"))
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

# Hosting settings (all optional when running locally)
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")   # set this when hosting online
PROXY_URL = os.environ.get("PROXY_URL", "")         # e.g. http://user:pass@host:port


@app.before_request
def require_password():
    if not APP_PASSWORD or not (request.path.startswith("/api/") or request.path.startswith("/clips/")):
        return None
    given = request.headers.get("X-Access-Key") or request.args.get("key", "")
    if not hmac.compare_digest(given.encode(), APP_PASSWORD.encode()):
        return jsonify(error="Wrong or missing password."), 401


@app.get("/clips/<path:name>")
def get_clip(name):
    return send_from_directory(CLIPS_DIR, name, as_attachment=True)


def video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    if not m:
        raise ValueError("That doesn't look like a YouTube link.")
    return m.group(1)


def get_transcript(vid: str):
    """Returns a list of (start_seconds, text). Works with old and new library versions."""
    proxy = GenericProxyConfig(http_url=PROXY_URL, https_url=PROXY_URL) if PROXY_URL else None
    try:
        items = YouTubeTranscriptApi(proxy_config=proxy).fetch(vid)
        rows = [(s.start, s.duration, s.text) for s in items]
    except AttributeError:
        rows = [(s["start"], s["duration"], s["text"]) for s in YouTubeTranscriptApi.get_transcript(vid)]
    return rows


def compact(rows, chunk=8):
    """Merge tiny caption lines into ~8s chunks tagged with start seconds."""
    out, cur_start, cur = [], None, []
    for start, _dur, text in rows:
        if cur_start is None:
            cur_start = start
        cur.append(text.replace("\n", " "))
        if start - cur_start >= chunk:
            out.append(f"[{int(cur_start)}] {' '.join(cur)}")
            cur_start, cur = None, []
    if cur:
        out.append(f"[{int(cur_start)}] {' '.join(cur)}")
    return "\n".join(out)


def srt_time(t):
    ms = int(max(t, 0) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def build_srt(rows, start, end):
    """Caption file for one clip, times shifted to start at 0, no overlapping lines."""
    cues = []
    for st, dur, text in rows:
        a, b = max(st, start), min(st + dur, end)
        if b > a and text.strip():
            cues.append([a, b, textwrap.fill(" ".join(text.split()), 26)])
    for i in range(len(cues) - 1):
        cues[i][1] = min(cues[i][1], cues[i + 1][0])
    return "\n".join(
        f"{i}\n{srt_time(a - start)} --> {srt_time(b - start)}\n{t}\n"
        for i, (a, b, t) in enumerate(cues, 1)
    )


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/clips")
def clips():
    data = request.get_json(force=True)
    try:
        vid = video_id(data.get("url", ""))
        text = compact(get_transcript(vid))[:150_000]
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception:
        return jsonify(error="Couldn't get a transcript for this video. Captions may be turned off."), 422

    lo, hi, n = int(data.get("min", 20)), int(data.get("max", 60)), int(data.get("n", 5))
    style = (data.get("style") or "").strip()
    prompt = f"""You are an expert short-form video editor. Pick the {n} best standalone clips from this transcript.
Each line starts with its start time in seconds, like [123].

Rules:
- Each clip is {lo} to {hi} seconds long.
- Start on a strong hook and end on a complete thought. Never cut mid-sentence.
- Prefer surprising, emotional, funny, opinionated moments, or concrete takeaways.
- Clips must not overlap. Rank best first.
{f'- Viewer preference: {style}' if style else ''}

Return ONLY JSON, no other text:
{{"clips":[{{"start_sec":number,"end_sec":number,"title":"short catchy title","hook":"opening line, verbatim","score":1-10,"reason":"one sentence on why it works"}}]}}

TRANSCRIPT:
{text}"""

    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": prompt}]
        )
        raw = "".join(b.text for b in msg.content if b.type == "text")
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        result = json.loads(raw)["clips"]
    except Exception as e:
        return jsonify(error=f"The clip picker failed: {e}"), 500

    result = [c for c in result if c["end_sec"] > c["start_sec"]]
    return jsonify(video_id=vid, clips=result)


@app.post("/api/cut")
def cut():
    """Download one section, then (optionally) make it vertical 9:16 with burned-in captions."""
    d = request.get_json(force=True)
    try:
        vid = video_id(d["url"])
        start, end = int(d["start"]), int(d["end"])
    except Exception:
        return jsonify(error="Bad request."), 400
    vertical, captions = bool(d.get("vertical", True)), bool(d.get("captions", True))

    stem = f"{vid}_{start}-{end}"
    raw, final = CLIPS_DIR / f"{stem}_raw.mp4", CLIPS_DIR / f"{stem}.mp4"
    dl = [
        "yt-dlp", f"https://www.youtube.com/watch?v={vid}",
        "--download-sections", f"*{start}-{end}", "--force-keyframes-at-cuts",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format", "mp4", "-o", str(raw),
    ]
    if PROXY_URL:
        dl += ["--proxy", PROXY_URL]
    try:
        subprocess.run(dl, check=True, capture_output=True, timeout=600)
    except FileNotFoundError:
        return jsonify(error="yt-dlp or ffmpeg isn't installed. See the README."), 500
    except subprocess.CalledProcessError:
        return jsonify(error="The download failed. Try again or check the link."), 500

    filters = []
    if vertical:
        filters.append("crop=min(iw\\,ih*9/16):ih,scale=1080:1920")
    if captions:
        try:
            srt = build_srt(get_transcript(vid), start, end)
            if srt:
                (CLIPS_DIR / f"{stem}.srt").write_text(srt, encoding="utf-8")
                filters.append(
                    f"subtitles={stem}.srt:force_style='Alignment=2,FontName=Arial,"
                    "FontSize=12,Bold=1,Outline=3,MarginV=110'"
                )
        except Exception:
            pass  # no captions available: still deliver the clip

    if not filters:
        raw.replace(final)
    else:
        ff = ["ffmpeg", "-y", "-i", raw.name, "-vf", ",".join(filters),
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", final.name]
        try:
            subprocess.run(ff, check=True, capture_output=True, timeout=600, cwd=CLIPS_DIR)
        except subprocess.CalledProcessError:
            return jsonify(error="Video processing failed. Check that FFmpeg is installed."), 500
        finally:
            raw.unlink(missing_ok=True)
            (CLIPS_DIR / f"{stem}.srt").unlink(missing_ok=True)
    return jsonify(file=final.name, url=f"/clips/{final.name}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5055)), debug=False)
