"""Draw results/title_bias.png: do models recognise a barcode, or just like certain titles?

    uv run --with matplotlib plot_title_bias.py

Every title is the right answer to one question and a wrong option in several others. For each
title this plots how often the six plain API models picked it when it was right against how often
they picked it when it was wrong. A title on the diagonal gets picked equally often either way, so
the picture made no difference. Real recognition is the distance above the diagonal.
"""

import collections
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from plot_results import RUNS

ROOT = Path(__file__).resolve().parent
SETS = [("films", "Films", "#b5651d"), ("youtube", "YouTube videos", "#2f6fde")]
MIN_CHANCES = 18  # a title needs this many model answers as a wrong option to be plotted
LABELS = {"films": ["The Illusionist (2006)", "Jane Got a Gun (2015)", "Metropolis (1927)", "Carnival of Souls (1962)",
                    "Silent House (2011)", "The General (1926)", "Black Christmas (1974)"],
          "youtube": ["Meghan Trainor - All About That Bass (2014)", "Luis Fonsi ft. Daddy Yankee - Despacito (2017)",
                      "Adele - Someone Like You (2011)", "Avicii - Wake Me Up (2013)", "Queen - Bohemian Rhapsody (1975)",
                      "Miroshka TV - Learning Colors - Colorful Eggs on a Farm (2018)", "Adele - Hello (2015)"]}


NUDGE = {"The Illusionist": (-5, -11, "right"), "Someone Like You": (-6, 6, "right")}  # labels that would collide or run off the edge


def guesses(ds):
    """{question id: [each model's guessed letter]} over the plain API runs."""
    out = collections.defaultdict(list)
    for _, api, _, _ in RUNS.values():
        folder = ROOT / "results" / next(f for f in api if f.startswith(ds + "/"))
        f = next(p for p in folder.glob("*.jsonl") if p.name != "questions.jsonl")
        for r in map(json.loads, f.read_text().splitlines()):
            if "error" not in r and r.get("guess"):
                out[r["id"]].append(r["guess"])
    return out


def rates(ds):
    """title -> (picked when right, picked when wrong, chances as a wrong option)"""
    got = guesses(ds)
    right, wrong, chances = {}, collections.Counter(), collections.Counter()
    for line in (ROOT / "data" / ds / "questions.jsonl").read_text().splitlines():
        q = json.loads(line)
        g = got[q["id"]]
        right[q["answer_title"]] = np.mean([x == q["answer"] for x in g])
        for i, c in enumerate(q["choices"]):
            if chr(65 + i) != q["answer"]:
                chances[c] += len(g)
                wrong[c] += sum(x == chr(65 + i) for x in g)
    return {t: (right[t], wrong[t] / chances[t], chances[t]) for t in right if chances[t] >= MIN_CHANCES}


def main():
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.6), dpi=200, sharey=True)
    rng = np.random.default_rng(2)
    for ax, (ds, title, color) in zip(axes, SETS):
        r = rates(ds)
        hit = np.array([v[0] for v in r.values()]) * 100
        fa = np.array([v[1] for v in r.values()]) * 100
        ax.plot([0, 100], [0, 100], color="#888", linestyle="--", linewidth=1, zorder=0)
        ax.text(0.98, 0.04, "dashed line: picked just as\noften right or wrong", transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color="#666")
        ax.scatter(fa + rng.uniform(-0.7, 0.7, len(fa)), hit + rng.uniform(-1.5, 1.5, len(hit)), s=18, color=color, alpha=0.4, linewidths=0)
        for name in LABELS[ds]:
            if name in r:
                x, y = r[name][1] * 100, r[name][0] * 100
                short = name.split(" - ")[-1].rsplit(" (", 1)[0]
                short = short if len(short) <= 24 else short[:22] + "…"
                dx, dy, ha = NUDGE.get(short, (5, 4, "left"))
                ax.annotate(short, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=7.5, color="#333", ha=ha)
        fav = fa >= 20
        ax.set_title(title, fontsize=11, loc="left", fontweight="bold")
        ax.text(1.0, 1.03, f"picked when right {hit.mean():.0f}%, when wrong {fa.mean():.0f}%", transform=ax.transAxes, ha="right",
                va="bottom", fontsize=8.5, color="#444")
        ax.set_xlabel("picked when it is a wrong option (%)")
        ax.set_xlim(-3, 70)
        ax.set_ylim(-5, 105)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        print(f"{ds}: {len(r)} titles, picked when right {hit.mean():.1f}%, when wrong {fa.mean():.1f}%, correlation {np.corrcoef(hit, fa)[0, 1]:+.2f}; "
              f"{fav.sum()} favourites (picked 20%+ when wrong) hold {hit[fav].sum() / hit.sum():.0%} of the right answers")
    axes[0].set_ylabel("picked when it is the right answer (%)")
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.13, top=0.92, wspace=0.06)
    out = ROOT / "results" / "title_bias.png"
    fig.savefig(out)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
