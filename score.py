"""Rescore saved eval results against the current question set.

    uv run score.py results/films/20261001-194048

Only questions still in data/<dataset>/questions.jsonl count, so dropping a bad question from the
set and rescoring doesn't need a new model run. Rewrites summary.json in the results folder.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="a results/<dataset>/<timestamp> folder")
    ap.add_argument("--dataset", help="films or youtube (default: taken from the results path)")
    args = ap.parse_args()
    out = Path(args.results)
    dataset = args.dataset or out.parent.name
    qpath = ROOT / "data" / dataset / "questions.jsonl"
    live = {json.loads(line)["id"] for line in qpath.read_text().splitlines() if line.strip()}

    summary = []
    for f in sorted(out.glob("*.jsonl")):
        if f.name == "questions.jsonl":  # the run's copy of the questions, not answers
            continue
        recs = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        kept = [r for r in recs if r["id"] in live]
        n = len(kept)
        correct = sum(r["correct"] for r in kept)
        summary.append({
            "model": recs[0]["model"] if recs else f.stem,
            "n": n,
            "correct": correct,
            "accuracy": correct / n if n else 0.0,
            "errors": sum("error" in r for r in kept),
            "unparsed": sum(r.get("guess") is None and "error" not in r for r in kept),
            "leaks": sum(bool(r.get("leak")) for r in kept),
            "downloads": sum(bool(r.get("download")) for r in kept),
            "cost_usd": round(sum(r.get("cost_usd") or 0 for r in kept), 4) or None,
            "dropped": len(recs) - n,
        })
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for s in summary:
        print(f"{s['model']}: {s['correct']}/{s['n']} = {s['accuracy']:.1%}  ({s['dropped']} dropped questions ignored)")


if __name__ == "__main__":
    main()
