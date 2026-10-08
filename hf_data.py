"""Fetch the questions and barcodes from Hugging Face, laid out like data/.

    uv run --with datasets hf_data.py [--repo loganbolton/VideoColorBench]

Writes hf_data/<dataset>/questions.jsonl and hf_data/<dataset>/items/<id>/barcode.png. `run.py --hf` does
this for you. The barcodes are saved byte for byte as uploaded.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "loganbolton/VideoColorBench"


def fetch(repo=REPO, out=ROOT / "hf_data"):
    try:
        from datasets import Image, load_dataset
    except ImportError:
        sys.exit("--hf needs the datasets package: uv run --with datasets run.py ...")
    for ds in ("films", "youtube"):
        rows = load_dataset(repo, ds, split="test").cast_column("image", Image(decode=False))
        (out / ds).mkdir(parents=True, exist_ok=True)
        lines = []
        for r in rows:
            item = out / ds / "items" / r["id"]
            item.mkdir(parents=True, exist_ok=True)
            (item / "barcode.png").write_bytes(r["image"]["bytes"])
            q = {k: r[k] for k in ("id", "choices", "answer", "answer_title", "genre", "colorfulness")}
            lines.append(json.dumps({**q, "dataset": ds, "slug": r["id"]}, ensure_ascii=False))
        (out / ds / "questions.jsonl").write_text("\n".join(lines) + "\n")
        print(f"{ds}: {len(lines)} questions from {repo}", flush=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO)
    print(fetch(ap.parse_args().repo))
