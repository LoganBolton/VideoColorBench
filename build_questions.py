"""Turn data/<dataset>/colors.json into 10-way multiple choice questions.

    uv run build_questions.py                      # films -> data/films/questions.jsonl
    uv run build_questions.py --dataset youtube    # youtube -> data/youtube/questions.jsonl
    uv run build_questions.py --strategy random    # distractors picked uniformly at random instead

Each question has the right answer plus 9 distractors from the rest of the same dataset, so every
title a model sees is also a real answer somewhere else.

With the default "similar" strategy, distractors are the items that look most like plausible
alternatives, so the answer can't be found just by ruling out obviously wrong options. Candidates
score higher when they have
  - similar colorfulness, measured from the frames (a black and white answer gets black and white options),
  - the same genre (from the catalog: noir, horror, kids, latin, ...),
  - a nearby year,
plus a little seeded jitter so questions don't all share the same option list. Any candidate whose
average color is practically identical to the answer's (CIE76 delta E below --min-delta-e) is left out,
so every question is answerable from the color in principle.
"""

import argparse
import json
import math
import random
import re
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def label(m):
    if m.get("label"):
        return m["label"]
    return f"{m['title']} ({m['year']})" if m.get("year") else m["title"]


def slug_of(entry):
    s = re.sub(r"[^a-z0-9]+", "-", entry["title"].lower()).strip("-")
    return f"{s}-{entry['year']}" if entry.get("year") else s


def srgb_to_lab(rgb):
    c = np.asarray(rgb, dtype=float) / 255
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def colorfulness(data, slug):
    """Mean per-frame saturation (max minus min channel, 0-255) read off the barcode. ~0 for black and white."""
    path = data / "items" / slug / "barcode.png"
    if not path.exists():
        return None
    row = np.asarray(Image.open(path).convert("RGB"))[0].astype(float)
    return float((row.max(axis=1) - row.min(axis=1)).mean())


def similarity(a, c):
    s = 0.0
    if a["colorful"] is not None and c["colorful"] is not None:
        s += 2.0 * math.exp(-abs(a["colorful"] - c["colorful"]) / 8)
    if a.get("genre") and a.get("genre") == c.get("genre"):
        s += 1.5
    if a.get("year") and c.get("year"):
        s += 1.0 * math.exp(-abs(a["year"] - c["year"]) / 15)
    return s


def pick_similar(a, others, k, rng, min_delta_e, jitter):
    far_enough = [c for c in others if np.linalg.norm(a["lab"] - c["lab"]) >= min_delta_e]
    if len(far_enough) < k:  # tiny dataset, fall back to everything
        far_enough = others
    scored = sorted(far_enough, key=lambda c: similarity(a, c) + rng.uniform(0, jitter), reverse=True)
    return scored[:k]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=["films", "youtube"], default="films")
    ap.add_argument("--choices", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--strategy", choices=["similar", "random"], default="similar")
    ap.add_argument("--min-delta-e", type=float, default=2.0, help="drop distractors closer than this to the answer color")
    ap.add_argument("--jitter", type=float, default=0.75, help="random noise added to similarity scores")
    args = ap.parse_args()
    data = ROOT / "data" / args.dataset

    catalog = {slug_of(e): e for e in json.loads((ROOT / "catalogs" / f"{args.dataset}.json").read_text())}
    items = json.loads((data / "colors.json").read_text())
    for m in items:
        meta = catalog.get(m["slug"], {})
        m["genre"] = m.get("genre") or meta.get("genre")
        m["colorful"] = colorfulness(data, m["slug"])
        m["lab"] = srgb_to_lab(m["rgb"])
        m["label"] = label(m)
    if len(items) < args.choices:
        raise SystemExit(f"need at least {args.choices} items to build questions, have {len(items)}")

    rng = random.Random(args.seed)
    out = data / "questions.jsonl"
    with open(out, "w") as f:
        for m in items:
            others = [c for c in items if c["slug"] != m["slug"]]
            if args.strategy == "random":
                distractors = rng.sample(others, args.choices - 1)
            else:
                distractors = pick_similar(m, others, args.choices - 1, rng, args.min_delta_e, args.jitter)
            choices = [c["label"] for c in distractors] + [m["label"]]
            rng.shuffle(choices)
            q = {
                "id": m["slug"],
                "dataset": args.dataset,
                "slug": m["slug"],
                "hex": m["hex"],
                "rgb": m["rgb"],
                "choices": choices,
                "answer": LETTERS[choices.index(m["label"])],
                "answer_title": m["label"],
                "strategy": args.strategy,
                "genre": m["genre"],
                "colorfulness": None if m["colorful"] is None else round(m["colorful"], 1),
            }
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"wrote {len(items)} {args.strategy} questions to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
