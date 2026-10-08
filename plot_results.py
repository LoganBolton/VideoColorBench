"""Draw results/overall.png: each model's accuracy over all 200 questions, with its agent harness run outlined on the same bar.

    uv run --with matplotlib plot_results.py
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
LOGO = {"Opus 5.5": "claude", "GPT-6.1 Sol": "openai", "GPT-6 Astra": "openai", "Qwen3.5 397B": "qwen",
        "GLM-5.3 Flash": "zai", "Qwen3.8 27B": "qwen"}
RUNS = {  # model -> (color, API results folders, agent name, agent results folders)
    "Opus 5.5": ("#e0782c", ["films/20261002-021440-opus-medium-films-raw", "youtube/20261001-223955-opus-medium-youtube-raw"],
                 "Claude Code", ["films/20261002-021113-claude-code-opus-films", "youtube/20261001-183201-claude-code-opus-youtube"]),
    "GPT-6.1 Sol": ("#2f6fde", ["films/20261002-021127-sol-medium-films-raw", "youtube/20261001-223715-sol-medium-youtube-raw"],
                    "Codex", ["films/20261002-021113-codex-sol-films", "youtube/20261001-183432-codex-sol-youtube"]),
    "GPT-6 Astra": ("#2f6fde", ["films/20261002-152027-gpt-6-astra-films-raw", "youtube/20261002-152102-gpt-6-astra-youtube-raw"], None, None),
    "Qwen3.5 397B": ("#7a4fd0", ["films/20261002-064646-qwen3.5-397b-a17b-films-raw", "youtube/20261002-073620-qwen3.5-397b-a17b-youtube-raw"], None, None),
    "GLM-5.3 Flash": ("#2e9d57", ["films/20261002-051152-glm-5.3-flash-films-raw", "youtube/20261002-053919-glm-5.3-flash-youtube-raw"],
                      "opencode", ["films/20261002-052850-opencode-glm-5.3-flash-films", "youtube/20261002-052830-opencode-glm-5.3-flash-youtube"]),
    "Qwen3.8 27B": ("#7a4fd0", ["films/20261002-064649-qwen3.8-27b-films-raw", "youtube/20261002-065521-qwen3.8-27b-youtube-raw"], None, None),
}


def accuracy(folders):
    """Percent right over the questions that got an answer, and how many did."""
    rows = [json.loads((ROOT / "results" / f / "summary.json").read_text())[0] for f in folders]
    answered = sum(r["n"] - r["errors"] for r in rows)
    return 100 * sum(r["correct"] for r in rows) / answered, answered


def darker(color, k=0.55):
    return "#" + "".join(f"{round(int(color[i:i + 2], 16) * k):02x}" for i in (1, 3, 5))


def logo_tile(name, color, size=160):
    """The white logo on a rounded square, as on the blog."""
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, size - 1, size - 1], size // 4, fill="#1a1a1a" if name == "openai" else color)
    mark = Image.open(ROOT / "video" / "logos" / f"{name}.png").convert("RGBA").resize((size * 3 // 5,) * 2, Image.LANCZOS)
    tile.alpha_composite(mark, (size // 5, size // 5))
    return tile


def main():
    fig, ax = plt.subplots(figsize=(8.4, 4.4), dpi=200)
    partial = None
    for i, (model, (color, api, agent, agent_runs)) in enumerate(RUNS.items()):
        base, _ = accuracy(api)
        ax.bar(i, base, width=0.62, color=color)
        ax.add_artist(AnnotationBbox(OffsetImage(logo_tile(LOGO[model], color), zoom=0.13), (i, -7), frameon=False, annotation_clip=False))
        ax.text(i, -13, model.replace(" ", "\n", 1), ha="center", va="top", fontsize=9, linespacing=1.25)
        if agent:
            # the agent run is a dashed outline on the same column: above the bar for a gain, inside it for a loss
            score, answered = accuracy(agent_runs)
            star = "*" if answered < 200 else ""
            if star:
                partial = f"*only {answered}/200 evaluated because I ran out of money mid run"
            ax.bar(i, abs(score - base), bottom=min(base, score), width=0.62, fill=False, linestyle="--", linewidth=1.1,
                   edgecolor=color if score > base else darker(color))  # a loss is drawn inside the bar
            y = max(score, base) + 1.5
            ax.text(i, y + 4.6, f"+ {agent}{star}", ha="center", va="bottom", fontsize=8.5, color="#555")
            ax.text(i, y, f"{score - base:+.1f} pp".replace("-", "−"), ha="center", va="bottom", fontsize=8.5,
                    color="#2e8b57" if score > base else "#c0392b")
        ax.text(i, 1.5, f"{base:.1f}%", ha="center", va="bottom", fontsize=9, color="white", alpha=0.9)  # under the chance line
    ax.axhline(10, color="#888", linestyle="--", linewidth=1, zorder=0)
    ax.text(len(RUNS) - 0.4, 10, " random chance", ha="left", va="center", fontsize=9, color="#666")
    if partial:
        ax.text(len(RUNS) - 0.4, -26, partial, ha="right", va="top", fontsize=8.5, color="#555")
    ax.set_ylim(0, 100)
    ax.set_xlim(-0.6, len(RUNS) - 0.4)
    ax.set_xticks([])
    ax.set_ylabel("accuracy (%)")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.subplots_adjust(bottom=0.27, right=0.86, top=0.96)
    out = ROOT / "results" / "overall.png"
    fig.savefig(out)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
