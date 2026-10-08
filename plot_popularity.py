"""Draw results/popularity.png: are better known films and videos easier to recognise?

    uv run --with matplotlib plot_popularity.py

Popularity is IMDb votes for films and YouTube views for videos (data/<dataset>/popularity.json). Each
dot is one question, placed at the share of the six plain API models that got it right. The line is
the same share averaged over five equal groups, from least to most popular.
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from plot_results import RUNS

ROOT = Path(__file__).resolve().parent
TICKS = [2e4, 5e4, 1e5, 2e5, 5e5, 1e9, 2e9, 5e9, 1e10, 2e10]
SETS = [("films", "Films", "IMDb votes", "#b5651d"), ("youtube", "YouTube videos", "YouTube views", "#2f6fde")]


def api_runs(ds):
    """One {question id: right or wrong} dict per model's plain API run."""
    out = []
    for _, api, _, _ in RUNS.values():
        folder = ROOT / "results" / next(f for f in api if f.startswith(ds + "/"))
        f = next(p for p in folder.glob("*.jsonl") if p.name != "questions.jsonl")
        out.append({r["id"]: bool(r["correct"]) for r in map(json.loads, f.read_text().splitlines()) if "error" not in r})
    return out


def ranks(x):
    order = np.argsort(x)
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    for v in np.unique(x):  # ties share their average rank
        r[x == v] = r[x == v].mean()
    return r


def spearman(x, y, trials=20000):
    """Rank correlation and a two-sided permutation p-value."""
    rx, ry = ranks(x), ranks(y)
    rho = np.corrcoef(rx, ry)[0, 1]
    rng = np.random.default_rng(0)
    null = np.array([np.corrcoef(rx, rng.permutation(ry))[0, 1] for _ in range(trials)])
    return rho, (np.abs(null) >= abs(rho)).mean()


def short(v):
    return f"{v / 1e9:.3g}B" if v >= 1e9 else f"{v / 1e6:.0f}M" if v >= 1e6 else f"{v / 1e3:.0f}k"


fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.2), dpi=200, sharey=True)
rng = np.random.default_rng(1)
for ax, (ds, title, measure, color) in zip(axes, SETS):
    pop = json.loads((ROOT / "data" / ds / "popularity.json").read_text())["values"]
    runs = api_runs(ds)
    ids = [json.loads(line)["id"] for line in (ROOT / "data" / ds / "questions.jsonl").read_text().splitlines()]
    ids = [i for i in ids if pop.get(i)]
    x = np.array([pop[i] for i in ids], dtype=float)
    share = np.array([100 * np.mean([r[i] for r in runs if i in r]) for i in ids])
    rho, p = spearman(x, share)

    ax.scatter(x, share + rng.uniform(-1.6, 1.6, len(x)), s=14, color=color, alpha=0.28, linewidths=0)
    groups = np.array_split(np.argsort(x), 5)
    gx = [np.exp(np.log(x[g]).mean()) for g in groups]
    gy = [share[g].mean() for g in groups]
    err = [1.96 * share[g].std(ddof=1) / np.sqrt(len(g)) for g in groups]
    ax.errorbar(gx, gy, yerr=err, color=color, marker="o", markersize=6, linewidth=2, capsize=3)
    for a, b, e in zip(gx, gy, err):
        ax.text(a, b + e + 2, f"{b:.0f}%", ha="center", va="bottom", fontsize=8.5, color=color, fontweight="bold")
    ax.axhline(10, color="#888", linestyle="--", linewidth=1, zorder=0)
    ax.set_xscale("log")
    ticks = [t for t in TICKS if x.min() * 0.8 <= t <= x.max() * 1.2]
    ax.set_xticks(ticks, [short(t) for t in ticks])
    ax.set_xticks([], minor=True)
    ax.set_xlabel(f"{measure} (log scale)")
    ax.set_title(title, fontsize=11, loc="left", fontweight="bold")
    ptxt = "p < 0.001" if p < 0.001 else f"p = {p:.2f}" if p >= 0.01 else f"p = {p:.3f}"
    ax.text(1.0, 1.03, f"Spearman ρ = {rho:+.2f}, {ptxt}", transform=ax.transAxes, ha="right", va="bottom", fontsize=8.5, color="#444")
    ax.set_ylim(-4, 104)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    print(f"{ds}: rho {rho:+.3f}, p {p:.4f}, groups " + ", ".join(f"{short(a)} {b:.0f}%" for a, b in zip(gx, gy)))
axes[0].set_ylabel("share of the six API models that got it right (%)")
axes[1].text(1.0, 10, " chance", transform=axes[1].get_yaxis_transform(), ha="left", va="center", fontsize=8.5, color="#666")
fig.subplots_adjust(left=0.08, right=0.94, bottom=0.14, top=0.92, wspace=0.08)
out = ROOT / "results" / "popularity.png"
fig.savefig(out)
print(f"wrote {out.relative_to(ROOT)}")
