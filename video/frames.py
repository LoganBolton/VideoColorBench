"""Download example videos again and save evenly spaced stills for make_video.py.

    uv run python video/frames.py <work dir> youtube:gangnam-style-2012 films:nosferatu-1922

Writes <work dir>/frames/<slug>/NNNN.jpg.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import extract  # noqa: E402
import viewer  # noqa: E402

STILLS = 180

extract.FFMPEG = extract.find_ffmpeg()
out = Path(sys.argv[1]) / "frames"
for pick in sys.argv[2:]:
    dataset, slug = pick.split(":", 1)
    dst = out / slug
    if dst.exists() and len(list(dst.glob("*.jpg"))) >= STILLS - 2:
        continue
    dst.mkdir(parents=True, exist_ok=True)
    entry = json.loads((ROOT / "data" / dataset / "items" / slug / "entry.json").read_text())
    seconds = entry["frames_sampled"] / entry["sample_fps"]
    with tempfile.TemporaryDirectory() as tmp:
        video = viewer.download(dataset, entry, tmp)
        subprocess.run([extract.FFMPEG, "-nostdin", "-v", "error", "-i", str(video),
                        "-vf", f"fps={STILLS / seconds:.8f},scale=iw*sar:ih,scale=960:-2", "-q:v", "3", str(dst / "%04d.jpg")], check=True)
    print(slug, len(list(dst.glob("*.jpg"))), flush=True)
