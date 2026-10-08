"""Build viewer.html, an interactive page where hovering over a barcode shows the frame at that point.

    uv run viewer.py youtube:gangnam-style-2012 films:charade-1963 ...

Each item is downloaded again from the source recorded in its entry.json, then sampled into a
sheet of small thumbnails that gets embedded in the page next to the barcode.
"""

import base64
import html
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

import extract

ROOT = Path(__file__).resolve().parent
MAX_THUMBS = 400
THUMB_W = 240
SHEET_COLS = 20

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VideoColorBench viewer</title>
<style>
:root{--bg:#fff;--fg:#111;--mut:#666;--line:#ddd}
@media(prefers-color-scheme:dark){:root{--bg:#151515;--fg:#e8e8e8;--mut:#999;--line:#333}}
body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:24px 16px;background:var(--bg);color:var(--fg)}
h1{margin:0 0 4px}.mut{color:var(--mut);font-size:13px}
.item{border:1px solid var(--line);border-radius:8px;padding:14px;margin:20px 0}
.item h2{font-size:16px;margin:0 0 10px}
.row{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.thumb{flex:0 0 auto;background-color:#000;background-repeat:no-repeat;border-radius:4px}
.side{flex:1 1 300px;min-width:0}
.bar{position:relative;cursor:crosshair;touch-action:none}
.bar img{width:100%;height:120px;display:block;border-radius:4px;image-rendering:pixelated}
.mark{position:absolute;top:-3px;bottom:-3px;width:2px;background:#fff;outline:1px solid #000;pointer-events:none;left:0}
.time{font:13px ui-monospace,monospace;margin-top:6px}
</style>
<h1>Barcode viewer</h1>
<p class="mut">Move the cursor (or drag a finger) across a barcode to see the frame from that point in the video.</p>
__ITEMS__
<script>
document.querySelectorAll('.item').forEach(el=>{
  const d=el.dataset,n=+d.n,cols=+d.cols,w=+d.w,h=+d.h,dur=+d.dur;
  const bar=el.querySelector('.bar'),thumb=el.querySelector('.thumb'),mark=el.querySelector('.mark'),time=el.querySelector('.time');
  const fmt=s=>{s=Math.round(s);const m=Math.floor(s/60)%60,hh=Math.floor(s/3600);return (hh?hh+':'+String(m).padStart(2,'0'):m)+':'+String(s%60).padStart(2,'0')};
  const show=f=>{
    f=Math.min(Math.max(f,0),0.9999);
    const i=Math.floor(f*n);
    thumb.style.backgroundPosition=`-${(i%cols)*w}px -${Math.floor(i/cols)*h}px`;
    mark.style.left=(f*100)+'%';
    time.textContent=fmt(f*dur)+' / '+fmt(dur);
  };
  bar.addEventListener('pointermove',e=>{const r=bar.getBoundingClientRect();show((e.clientX-r.left)/r.width)});
  show(0.5);
});
</script>
"""


def data_url(path, mime):
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def download(dataset, entry, tmp):
    src = entry["source"]
    if src.get("youtube_id"):
        subprocess.run(extract.ytdlp_base(tmp) + ["-f", "bv*[height<=360]/b[height<=360]/wv*/w", "-o", f"{tmp}/video.%(ext)s",
                                                  f"https://www.youtube.com/watch?v={src['youtube_id']}"], check=True, capture_output=True)
        return next(Path(tmp).glob("video.*"))
    dst = Path(tmp) / "movie.mp4"
    subprocess.run(["curl", "-sSfL", "--retry", "3", "-A", extract.UA["User-Agent"], "-o", str(dst), src["url"]], check=True)
    return dst


def build_item(dataset, slug):
    item_dir = ROOT / "data" / dataset / "items" / slug
    entry = json.loads((item_dir / "entry.json").read_text())
    seconds = entry["frames_sampled"] / entry["sample_fps"]
    n = min(MAX_THUMBS, entry["frames_sampled"])
    rows = math.ceil(n / SHEET_COLS)
    print(f"{entry['label']}: {n} thumbnails", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        video = download(dataset, entry, tmp)
        sheet = Path(tmp) / "sheet.jpg"
        vf = f"fps={n / seconds:.8f},scale=iw*sar:ih,scale={THUMB_W}:-2,tile={SHEET_COLS}x{rows}"
        subprocess.run([extract.FFMPEG, "-nostdin", "-v", "error", "-i", str(video), "-vf", vf,
                        "-frames:v", "1", "-q:v", "6", str(sheet)], check=True)
        sheet_w, sheet_h = Image.open(sheet).size
        w, h = sheet_w // SHEET_COLS, sheet_h // rows
        return (
            f'<div class="item" data-n="{n}" data-cols="{SHEET_COLS}" data-w="{w}" data-h="{h}" data-dur="{seconds}">'
            f'<h2>{html.escape(entry["label"])}</h2><div class="row">'
            f'<div class="thumb" style="width:{w}px;height:{h}px;background-image:url({data_url(sheet, "image/jpeg")})"></div>'
            f'<div class="side"><div class="bar"><img alt="barcode" src="{data_url(item_dir / "barcode.png", "image/png")}">'
            f'<div class="mark"></div></div><div class="time"></div></div></div></div>'
        )


def main():
    picks = [a.split(":", 1) for a in sys.argv[1:]]
    if not picks:
        sys.exit(__doc__)
    extract.FFMPEG = extract.find_ffmpeg()
    items = [build_item(dataset, slug) for dataset, slug in picks]
    out = ROOT / "viewer.html"
    out.write_text(PAGE.replace("__ITEMS__", "\n".join(items)))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
