"""Publish the questions and barcodes as a Hugging Face dataset.

    uv run --with datasets --with huggingface_hub hf_upload.py [--repo loganbolton/VideoColorBench] [--private]

One config per dataset (`films`, `youtube`), one `test` split each. Every row is a question: the
barcode image, the 10 options, the answer, and what is known about the video.
"""

import argparse
import json
from pathlib import Path

from datasets import Dataset, Features, Image, Sequence, Value
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent
FEATURES = Features({
    "id": Value("string"), "image": Image(), "choices": Sequence(Value("string")), "answer": Value("string"),
    "answer_index": Value("int32"), "answer_title": Value("string"), "title": Value("string"),
    "artist": Value("string"), "year": Value("int32"), "genre": Value("string"), "average_color": Value("string"),
    "colorfulness": Value("float32"), "seconds": Value("int32"), "source_url": Value("string"),
})


def source_url(src):
    if src.get("youtube_id"):
        return f"https://www.youtube.com/watch?v={src['youtube_id']}"
    return f"https://archive.org/details/{src['archive_id']}" if src.get("archive_id") else None


def rows(ds):
    for line in (ROOT / "data" / ds / "questions.jsonl").read_text().splitlines():
        q = json.loads(line)
        item = ROOT / "data" / ds / "items" / q["slug"]
        e = json.loads((item / "entry.json").read_text())
        yield {"id": q["id"], "image": str(item / "barcode.png"), "choices": q["choices"], "answer": q["answer"],
               "answer_index": ord(q["answer"]) - 65, "answer_title": q["answer_title"], "title": e["title"],
               "artist": e.get("artist"), "year": e["year"], "genre": q.get("genre"), "average_color": q["hex"],
               "colorfulness": q.get("colorfulness"), "seconds": round(e["frames_sampled"] / e["sample_fps"]),
               "source_url": source_url(e["source"])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="loganbolton/VideoColorBench")
    ap.add_argument("--private", action="store_true")
    args = ap.parse_args()
    for ds in ("films", "youtube"):
        data = Dataset.from_list(list(rows(ds)), features=FEATURES)
        data.push_to_hub(args.repo, config_name=ds, split="test", private=args.private)
        print(f"{ds}: {len(data)} questions")
    HfApi().upload_file(path_or_fileobj=ROOT / "hf_card.md", path_in_repo="README.md", repo_id=args.repo, repo_type="dataset")
    print(f"https://huggingface.co/datasets/{args.repo}")


if __name__ == "__main__":
    main()
