"""Average every frame of a video into one color, one averaged image, and a color barcode.

There are two datasets, each with a catalog in catalogs/:

    uv run extract.py fetch                        # films: public domain features from archive.org
    uv run extract.py fetch --dataset youtube      # youtube: popular YouTube videos, via yt-dlp
    uv run extract.py local ~/Movies               # your own files, named like "Blade Runner (1982).mkv"

Outputs land in data/<dataset>/:
    colors.json                 one entry per video (title, year, hex, rgb, ...)
    items/<slug>/frame.png      pixel-wise average of all sampled frames
    items/<slug>/barcode.png    mean color of each sampled frame, left to right
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
DATASETS = ("films", "youtube")
DATA = COLORS = None  # set by use_dataset()
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ogv"}
UA = {"User-Agent": "VideoColorBench/0.1"}
MIN_FEATURE_SECONDS = 40 * 60

_lock = threading.Lock()


def use_dataset(name):
    global DATA, COLORS
    DATA = ROOT / "data" / name
    COLORS = DATA / "colors.json"


def load_catalog(name):
    return json.loads((ROOT / "catalogs" / f"{name}.json").read_text())


def label(m):
    """Display name used in questions, e.g. "Charade (1963)" or "PSY - Gangnam Style (2012)"."""
    name = f"{m['artist']} - {m['title']}" if m.get("artist") else m["title"]
    return f"{name} ({m['year']})" if m.get("year") else name


def slugify(title, year):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{s}-{year}" if year else s


def load_colors():
    if COLORS.exists():
        return {m["slug"]: m for m in json.loads(COLORS.read_text())}
    return {}


def save_entry(entry):
    out = DATA / "items" / entry["slug"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "entry.json").write_text(json.dumps(entry, indent=2) + "\n")
    with _lock:
        colors = load_colors()
        colors[entry["slug"]] = entry
        write_colors(colors)


def write_colors(colors):
    DATA.mkdir(parents=True, exist_ok=True)
    rows = sorted(colors.values(), key=lambda m: (m["year"] or 0, m["title"]))
    COLORS.write_text(json.dumps(rows, indent=2) + "\n")
    return rows


# ---------------------------------------------------------------- frame averaging


def find_ffmpeg():
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found. Run through uv (uv run extract.py ...) or install ffmpeg yourself")


FFMPEG = None


def scaled_size(path, scale):
    """Size ffmpeg produces for `scale` on this video, read off one decoded frame."""
    png = subprocess.run(
        [FFMPEG, "-nostdin", "-v", "error", "-i", str(path), "-vf", scale, "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True,
    ).stdout
    return Image.open(io.BytesIO(png)).size


def average_video(path, fps=1.0, width=160):
    """Decode the video at `fps` frames per second, downscaled to `width` px wide.

    Returns (mean_rgb, avg_frame uint8 HxWx3, per-frame mean colors Nx3).
    Colors are plain sRGB averages, which is what a naive "average all frames" gives you.
    """
    # square up anamorphic pixels first so the average frame has the real aspect ratio
    scale = f"scale=iw*sar:ih,scale={width}:-2:flags=area"
    width, height = scaled_size(path, scale)
    cmd = [
        FFMPEG, "-nostdin", "-v", "error", "-i", str(path),
        "-vf", f"fps={fps},{scale}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    frame_bytes = width * height * 3
    total = np.zeros((height, width, 3), dtype=np.float64)
    per_frame = []
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        f = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
        total += f
        per_frame.append(f.reshape(-1, 3).mean(axis=0))
    proc.wait()
    if not per_frame:
        raise RuntimeError(f"ffmpeg produced no frames for {path}")
    avg = total / len(per_frame)
    per_frame = np.array(per_frame)
    return avg.reshape(-1, 3).mean(axis=0), avg.round().astype(np.uint8), per_frame


def barcode_image(per_frame, width=1000, height=200):
    idx = np.linspace(0, len(per_frame), width + 1).astype(int)
    cols = np.array([per_frame[a:max(b, a + 1)].mean(axis=0) for a, b in zip(idx[:-1], idx[1:])])
    strip = np.repeat(cols.round().astype(np.uint8)[None, :, :], height, axis=0)
    return Image.fromarray(strip)


def process(path, item, source, fps):
    title, year = item["title"], item.get("year")
    slug = slugify(title, year)
    mean, frame, per_frame = average_video(path, fps=fps)
    out = DATA / "items" / slug
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(frame).save(out / "frame.png")
    barcode_image(per_frame).save(out / "barcode.png")
    rgb = [int(round(c)) for c in mean]
    entry = {
        "slug": slug,
        "title": title,
        "artist": item.get("artist"),
        "year": year,
        "label": label(item),
        "hex": "#{:02X}{:02X}{:02X}".format(*rgb),
        "rgb": rgb,
        "frames_sampled": len(per_frame),
        "sample_fps": fps,
        "source": source,
    }
    save_entry(entry)
    print(f"  {entry['hex']}  {entry['label']}  [{len(per_frame)} frames]", flush=True)
    return entry


# ---------------------------------------------------------------- archive.org


def get_json(url, timeout=60, headers=UA):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return json.load(r)


def parse_length(v):
    if v is None:
        return 0.0
    v = str(v)
    if ":" in v:
        secs = 0.0
        for part in v.split(":"):
            secs = secs * 60 + float(part or 0)
        return secs
    try:
        return float(v)
    except ValueError:
        return 0.0


def item_year(doc):
    for k in ("year", "date"):
        m = re.search(r"\d{4}", str(doc.get(k, "")))
        if m:
            return int(m.group())
    return None


def best_mp4(identifier):
    """Smallest feature-length mp4 in an archive.org item, or None."""
    meta = get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    files = []
    for f in meta.get("files", []):
        name = f.get("name", "")
        if not name.lower().endswith(".mp4"):
            continue
        if parse_length(f.get("length")) < MIN_FEATURE_SECONDS:
            continue
        files.append((int(f.get("size") or 1 << 40), name))
    if not files:
        return None
    _, name = min(files)
    return f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(name)}"


def resolve_archive(movie):
    """Find an archive.org item and mp4 URL for a catalog entry."""
    if movie.get("archive_id"):
        url = best_mp4(movie["archive_id"])
        return movie["archive_id"], url
    title = movie["title"].replace('"', "")
    q = f'title:("{title}") AND mediatype:(movies)'
    params = urllib.parse.urlencode(
        {"q": q, "fl[]": ["identifier", "title", "year", "date", "downloads"], "sort[]": "downloads desc",
         "rows": 25, "output": "json"},
        doseq=True,
    )
    docs = get_json(f"https://archive.org/advancedsearch.php?{params}")["response"]["docs"]
    norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
    want = norm(movie["title"])

    def rank(d):
        y = item_year(d)
        year_ok = y is not None and abs(y - movie["year"]) <= 1
        title_ok = want in norm(d.get("title", ""))
        return (not year_ok, not title_ok, -(d.get("downloads") or 0))

    for d in sorted(docs, key=rank)[:8]:
        if want not in norm(d.get("title", "")):
            continue
        url = best_mp4(d["identifier"])
        if url:
            return d["identifier"], url
    return None, None


def fetch_archive(movie, tmp):
    ident, url = resolve_archive(movie)
    if not url:
        raise RuntimeError("no feature-length mp4 found on archive.org")
    print(f"fetching {label(movie)} <- archive.org/details/{ident}", flush=True)
    dst = Path(tmp) / "movie.mp4"
    subprocess.run(["curl", "-sSfL", "--retry", "3", "-A", UA["User-Agent"], "-o", str(dst), url], check=True)
    return dst, {"archive_id": ident, "url": url}


# ---------------------------------------------------------------- youtube


def ytdlp_base(tmp):
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "--ffmpeg-location", FFMPEG]
    # YouTube blocks most datacenter IPs (GitHub runners included) unless you're signed in.
    # YT_COOKIES holds the text of a Netscape-format cookies.txt, YT_COOKIES_FILE a path to one.
    if os.environ.get("YT_COOKIES"):
        cookie_file = Path(tmp) / "cookies.txt"
        cookie_file.write_text(os.environ["YT_COOKIES"])
        cmd += ["--cookies", str(cookie_file)]
    elif os.environ.get("YT_COOKIES_FILE"):
        cmd += ["--cookies", os.environ["YT_COOKIES_FILE"]]
    return cmd


def curl_to(url, dst, max_time=600):
    subprocess.run(["curl", "-sSfL", "--retry", "2", "--max-time", str(max_time), "-A", "Mozilla/5.0", "-o", str(dst), url],
                   check=True, capture_output=True)
    if dst.stat().st_size < 200_000:
        raise RuntimeError(f"download too small ({dst.stat().st_size} bytes)")
    return dst


BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def mirror_json(url):
    return get_json(url, timeout=20, headers=BROWSER)


# Public Invidious and Piped servers proxy YouTube through their own IPs, which gets around the
# "confirm you're not a bot" wall YouTube puts up for GitHub's runners. Servers come and go, so try several.
INVIDIOUS_FALLBACK = ["inv.nadeko.net", "invidious.nerdvpn.de", "yewtu.be", "invidious.f5.si", "iv.melmac.space"]
PIPED_FALLBACK = ["https://pipedapi.kavin.rocks", "https://pipedapi.adminforge.de", "https://api.piped.private.coffee"]
_mirrors = {}


def invidious_instances():
    if "inv" not in _mirrors:
        hosts = []
        try:
            for host, meta in mirror_json("https://api.invidious.io/instances.json?sort_by=health"):
                if meta.get("api") and meta.get("type") == "https":
                    hosts.append(host)
        except Exception:
            pass
        _mirrors["inv"] = list(dict.fromkeys(hosts[:12] + INVIDIOUS_FALLBACK))
    return _mirrors["inv"]


def piped_instances():
    if "piped" not in _mirrors:
        apis = []
        try:
            apis = [i["api_url"] for i in mirror_json("https://piped-instances.kavin.rocks/") if i.get("api_url")]
        except Exception:
            pass
        _mirrors["piped"] = list(dict.fromkeys(apis[:8] + PIPED_FALLBACK))
    return _mirrors["piped"]


def pick_stream(streams, url_key, height_key):
    """Lowest-res mp4 video stream at or above 144p, preferring <=360p."""
    def h(st):
        m = re.search(r"\d+", str(st.get(height_key) or ""))
        return int(m.group()) if m else 0
    vids = [st for st in streams if "mp4" in str(st.get("type") or st.get("mimeType") or st.get("format") or "").lower()
            and h(st) >= 144 and st.get(url_key)]
    small = sorted([st for st in vids if h(st) <= 360], key=h, reverse=True) or sorted(vids, key=h)
    return small[0][url_key] if small else None


def fetch_via_mirrors(vid, dst, rounds=4):
    """Try every mirror; on a round where all fail, wait and go again (busy servers often recover)."""
    errors = []
    for attempt in range(rounds):
        try:
            return _fetch_via_mirrors_once(vid, dst, errors)
        except RuntimeError:
            if attempt < rounds - 1:
                time.sleep(20 * (attempt + 1))
    raise RuntimeError("every mirror failed: " + "; ".join(errors[-6:]))


def _fetch_via_mirrors_once(vid, dst, errors):
    for host in invidious_instances():
        try:
            meta = mirror_json(f"https://{host}/api/v1/videos/{vid}?local=true&fields=adaptiveFormats,formatStreams")
            url = pick_stream(meta.get("adaptiveFormats", []) + meta.get("formatStreams", []), "url", "resolution")
            if not url:
                raise RuntimeError("no mp4 stream")
            if url.startswith("/"):
                url = f"https://{host}{url}"
            return curl_to(url, dst), f"invidious:{host}"
        except Exception as e:
            errors.append(f"{host}: {str(e)[:80]}")
    for api in piped_instances():
        try:
            meta = mirror_json(f"{api}/streams/{vid}")
            url = pick_stream(meta.get("videoStreams", []), "url", "quality")
            if not url:
                raise RuntimeError("no mp4 stream")
            return curl_to(url, dst), f"piped:{api}"
        except Exception as e:
            errors.append(f"{api}: {str(e)[:80]}")
    raise RuntimeError("all mirrors failed this round")


def search_mirrors(query):
    for host in invidious_instances()[:6]:
        try:
            q = urllib.parse.quote(query)
            hits = mirror_json(f"https://{host}/api/v1/search?q={q}&type=video&fields=videoId,title,author")
            if hits:
                return hits[0]["videoId"], hits[0].get("title", ""), hits[0].get("author", "")
        except Exception:
            continue
    return None


def fetch_youtube(item, tmp):
    """Download a low-res video-only stream. yt-dlp first, then Invidious/Piped mirrors if YouTube blocks us.

    Uses item["id"] if set, else the top search hit for item["query"] (or "artist - title (year) official video").
    """
    ytdlp = ytdlp_base(tmp)
    query = item.get("query") or label(item) + " official video"
    if item.get("id"):
        vid, yt_title, channel = item["id"], "", ""
    else:
        r = subprocess.run(ytdlp + ["--print", "%(id)s\t%(title)s\t%(channel)s", f"ytsearch1:{query}"],
                           capture_output=True, text=True)
        info = r.stdout.strip().splitlines()
        if not r.returncode and info:
            vid, yt_title, channel = (info[0].split("\t") + ["", ""])[:3]
        else:
            hit = search_mirrors(query)
            if not hit:
                raise RuntimeError(f"no search result for {query!r}: {r.stderr.strip()[-300:]}")
            vid, yt_title, channel = hit
    print(f"fetching {label(item)} <- youtube.com/watch?v={vid} ({yt_title} / {channel})", flush=True)
    source = {"youtube_id": vid, "youtube_title": yt_title, "channel": channel}
    r = subprocess.run(ytdlp + ["-f", "bv*[height<=360]/b[height<=360]/wv*/w", "-o", f"{tmp}/video.%(ext)s",
                                f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
    found = list(Path(tmp).glob("video.*"))
    if not r.returncode and found:
        return found[0], {**source, "via": "yt-dlp"}
    print(f"  yt-dlp blocked ({r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else 'no output'}), trying mirrors", flush=True)
    path, via = fetch_via_mirrors(vid, Path(tmp) / "mirror.mp4")
    print(f"  got it via {via}", flush=True)
    return path, {**source, "via": via}


def fetch_film(item, tmp):
    """Films come from archive.org, or from YouTube when the catalog entry pins a video "id" (official full-film uploads)."""
    return fetch_youtube(item, tmp) if item.get("id") else fetch_archive(item, tmp)


FETCHERS = {"films": fetch_film, "youtube": fetch_youtube}


def run_fetch(args):
    catalog = load_catalog(args.dataset)
    done = load_colors()
    todo = [m for m in catalog if args.force or slugify(m["title"], m.get("year")) not in done]
    if args.only:
        todo = [m for m in todo if args.only.lower() in label(m).lower()]
    if args.slug:
        todo = [m for m in todo if slugify(m["title"], m.get("year")) == args.slug]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} {args.dataset} to process", flush=True)
    failures = []

    def one(item):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path, source = FETCHERS[args.dataset](item, tmp)
                process(path, item, source, args.fps)
        except Exception as e:  # keep going, report at the end
            print(f"FAILED {label(item)}: {e}", flush=True)
            failures.append((label(item), str(e)))

    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(one, todo))
    if failures:
        print(f"\n{len(failures)} failed:")
        for name, err in failures:
            print(f"  {name}: {err}")
        sys.exit(1)


# ---------------------------------------------------------------- local files


def run_local(args):
    paths = [p for p in sorted(Path(args.dir).expanduser().rglob("*")) if p.suffix.lower() in VIDEO_EXTS]
    done = load_colors()
    for p in paths:
        m = re.match(r"^(.*?)\s*\((\d{4})\)", p.stem)
        title, year = (m.group(1).strip(), int(m.group(2))) if m else (p.stem, None)
        if not args.force and slugify(title, year) in done:
            print(f"skip {title} (already done)")
            continue
        print(f"processing {p.name}", flush=True)
        try:
            process(p, {"title": title, "year": year}, {"local_file": p.name}, args.fps)
        except Exception as e:
            print(f"FAILED {p.name}: {e}", flush=True)


def run_pending(args):
    """Print catalog items not yet in colors.json, across datasets, as JSON for the CI job matrix."""
    out = []
    for name in [args.dataset] if args.dataset else DATASETS:
        use_dataset(name)
        done = load_colors()
        for m in load_catalog(name):
            slug = slugify(m["title"], m.get("year"))
            if args.force or slug not in done:
                out.append({"dataset": name, "slug": slug, "title": label(m)})
    print(json.dumps(out))


def run_merge(args):
    """Fold every data/<dataset>/items/*/entry.json into colors.json (used after parallel CI jobs)."""
    entries = [json.loads(p.read_text()) for p in sorted((DATA / "items").glob("*/entry.json"))]
    colors = load_colors()
    colors.update({e["slug"]: e for e in entries})
    rows = write_colors(colors)
    print(f"{COLORS.relative_to(ROOT)} now has {len(rows)} entries")


def main():
    global FFMPEG
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and process everything in catalogs/<dataset>.json")
    f.add_argument("--only", help="substring of a title to process")
    f.add_argument("--slug", help="exact slug to process, e.g. charade-1963")
    f.add_argument("--limit", type=int)
    f.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    loc = sub.add_parser("local", help="process your own video files (name them 'Title (Year).ext')")
    loc.add_argument("dir")
    pend = sub.add_parser("pending", help="print unprocessed catalog items as JSON (all datasets unless --dataset)")
    mrg = sub.add_parser("merge", help="rebuild colors.json from data/<dataset>/items/*/entry.json")
    for p in (f, loc, pend, mrg):
        p.add_argument("--dataset", choices=DATASETS, default=None if p is pend else "films")
    for p in (f, loc, pend):
        p.add_argument("--force", action="store_true", help="reprocess items already in colors.json")
    for p in (f, loc):
        p.add_argument("--fps", type=float, default=1.0, help="frames sampled per second of video (default 1)")
    args = ap.parse_args()
    if args.dataset:
        use_dataset(args.dataset)
    if args.cmd in ("fetch", "local"):
        FFMPEG = find_ffmpeg()
    {"fetch": run_fetch, "local": run_local, "pending": run_pending, "merge": run_merge}[args.cmd](args)


if __name__ == "__main__":
    main()
