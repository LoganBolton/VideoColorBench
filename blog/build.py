"""Build the blog post into the website repo.

    uv run blog/build.py ~/Github/loganbolton.github.io/blog/videocolorbench [--videos ~/MovieColorBench-videos]

Writes index.html plus the data it loads: every question with its barcode, the four runs' answers,
full transcripts for the example questions, the frame sheets for the hover viewer and the quiz, the
teaser video and the results tables. The page text lives in blog/page.html.
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import extract  # noqa: E402
import viewer  # noqa: E402

RUNS = {  # key -> (label, films folder, youtube folder)
    "sol": ("GPT-6.1 Sol", "20261002-021127-sol-medium-films-raw", "20261001-223715-sol-medium-youtube-raw"),
    "codex": ("GPT-6.1 Sol + Codex", "20261002-021113-codex-sol-films", "20261001-183432-codex-sol-youtube"),
    "opus": ("Opus 5.5", "20261002-021440-opus-medium-films-raw", "20261001-223955-opus-medium-youtube-raw"),
    "cc": ("Opus 5.5 + Claude Code", "20261002-021113-claude-code-opus-films", "20261001-183201-claude-code-opus-youtube"),
}
AGENTS = [  # (label, logo, films folder, youtube folder, base model's label), the table of agent harness runs, shown in this order
    ("Opus 5.5 + Claude Code", "claude", *RUNS["cc"][1:], "Opus 5.5"),
    ("GPT-6.1 Sol + Codex", "openai", *RUNS["codex"][1:], "GPT-6.1 Sol"),
    ("GLM-5.3 Flash + OpenCode", "zai", "20261002-052850-opencode-glm-5.3-flash-films", "20261002-052830-opencode-glm-5.3-flash-youtube", "GLM-5.3 Flash"),
]
TABLE = [  # the same for the main table: plain API calls only
    ("GPT-6.1 Sol", "openai", *RUNS["sol"][1:]), ("Opus 5.5", "claude", *RUNS["opus"][1:]),
    ("GPT-6 Astra", "openai", "20261002-152027-gpt-6-astra-films-raw", "20261002-152102-gpt-6-astra-youtube-raw"),
    ("GLM-5.3 Flash", "zai", "20261002-051152-glm-5.3-flash-films-raw", "20261002-053919-glm-5.3-flash-youtube-raw"),
    ("Qwen3.5 397B", "qwen", "20261002-064646-qwen3.5-397b-a17b-films-raw", "20261002-073620-qwen3.5-397b-a17b-youtube-raw"),
    ("Qwen3.8 27B", "qwen", "20261002-064649-qwen3.8-27b-films-raw", "20261002-065521-qwen3.8-27b-youtube-raw"),
]
# every run on the "all questions" page: key -> (label, logo, text on its tag, films folder, youtube folder)
ALL_RUNS = {
    "sol": ("GPT-6.1 Sol", "openai", "", *RUNS["sol"][1:]),
    "codex": ("GPT-6.1 Sol + Codex", "openai", "+ Codex", *RUNS["codex"][1:]),
    "astra": ("GPT-6 Astra", "openai", "Astra", "20261002-152027-gpt-6-astra-films-raw", "20261002-152102-gpt-6-astra-youtube-raw"),
    "opus": ("Opus 5.5", "claude", "", *RUNS["opus"][1:]),
    "cc": ("Opus 5.5 + Claude Code", "claude", "+ Claude Code", *RUNS["cc"][1:]),
    "glm": ("GLM-5.3 Flash", "zai", "", "20261002-051152-glm-5.3-flash-films-raw", "20261002-053919-glm-5.3-flash-youtube-raw"),
    "opencode": ("GLM-5.3 Flash + OpenCode", "zai", "+ OpenCode", "20261002-052850-opencode-glm-5.3-flash-films", "20261002-052830-opencode-glm-5.3-flash-youtube"),
    "qwen35": ("Qwen3.5 397B", "qwen", "3.5 397B", "20261002-064646-qwen3.5-397b-a17b-films-raw", "20261002-073620-qwen3.5-397b-a17b-youtube-raw"),
    "qwen38": ("Qwen3.8 27B", "qwen", "3.8 27B", "20261002-064649-qwen3.8-27b-films-raw", "20261002-065521-qwen3.8-27b-youtube-raw"),
}
VIEWER = ["films:nosferatu-1922", "youtube:bohemian-rhapsody-1975", "youtube:november-rain-1992"]
# the quiz near the top: the answer and the four titles in the order shown, None where the answer goes.
# "quiz:" answers are not among the 200; their barcode.png and entry.json are in blog/quiz/<slug>/.
QUIZ = [
    ("quiz:hotline-bling-2015", ["Linkin Park - Numb", "Adele - Hello", None, "Luis Fonsi ft. Daddy Yankee - Despacito"]),
    ("youtube:all-about-that-bass-2014", [None, "Guns N' Roses - Sweet Child O' Mine", "Michael Jackson - Billie Jean", "The Weeknd ft. Daft Punk - Starboy"]),
    ("youtube:smells-like-teen-spirit-1991", ["Taylor Swift - Shake It Off", "PSY - Gangnam Style", "Katy Perry - Roar", None]),
    ("youtube:baby-shark-dance-2016", ["Queen - Bohemian Rhapsody", None, "Adele - Someone Like You", "Eminem - Without Me"]),
]
BONUS = [  # asked after the quiz as bonus questions, and scored apart from it
    ("quiz:but-what-is-a-neural-network-2017", ["Kurzgesagt - The Egg", None, "MrBeast - $456,000 Squid Game In Real Life!", "CGP Grey - The Rules for Rulers"]),
    ("quiz:let-s-build-gpt-2023", ["Google DeepMind - AlphaGo: The Movie", "Lex Fridman Podcast - Sam Altman", None, "Veritasium - The Most Misunderstood Concept in Physics"]),
]
EXAMPLES = {"youtube/bohemian-rhapsody-1975": "opus"}  # question -> the one run whose answer is shown
THUMBS, THUMB_W, COLS = 240, 200, 20


def scrub(text, limit=4000):
    """Drop machine-specific paths from transcript text and cap its length."""
    text = re.sub(r"/private/var/folders/\S*?/barcode-\w+", ".", str(text))
    text = re.sub(r"/private/tmp/claude-\d+/\S*?/scratchpad", "/tmp", text)
    text = text.replace("/Users/log/Github/MovieColorBench", "~/project").replace("/Users/log", "~")
    return text if len(text) <= limit else text[:limit] + f"\n[{len(text) - limit} more characters]"


def codex_steps(events):
    steps = []
    for e in events:
        it = e.get("item") or {}
        if e.get("type") != "item.completed":
            continue
        if it.get("type") == "agent_message":
            steps.append({"k": "msg", "t": scrub(it.get("text", ""))})
        elif it.get("type") == "web_search":
            hits = "\n\n".join(f"{r.get('domain', '')} | {r.get('title', '')}\n{(r.get('snippet') or '')[:300]}"
                               for r in (it.get("results") or [])[:5])
            steps.append({"k": "tool", "name": "web search", "input": scrub(it.get("query", "")), "output": scrub(hits)})
        elif it.get("type") == "command_execution":
            steps.append({"k": "tool", "name": "shell", "input": scrub(it.get("command", "")),
                          "output": scrub(it.get("aggregated_output") or "")})
    return steps


def claude_steps(events):
    results = {}
    for e in events:
        if e.get("type") == "user":
            for b in e["message"]["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "[image]") if x.get("type") == "text" else "[image]" for x in c)
                    results[b["tool_use_id"]] = scrub(c or "")
    steps = []
    for e in events:
        if e.get("type") != "assistant":
            continue
        for b in e["message"]["content"]:
            if b["type"] == "text" and b["text"].strip():
                steps.append({"k": "msg", "t": scrub(b["text"])})
            elif b["type"] == "tool_use":
                inp = b["input"]
                shown = inp.get("command") or inp.get("query") or inp.get("file_path") or inp.get("url") or json.dumps(inp)
                steps.append({"k": "tool", "name": b["name"], "input": scrub(shown), "output": results.get(b["id"], "")})
    return steps


def load_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def build_questions(out):
    questions = []
    shutil.rmtree(out / "t", ignore_errors=True)
    (out / "t").mkdir()
    for ds_index, ds in enumerate(("films", "youtube")):
        (out / "barcodes" / ds).mkdir(parents=True, exist_ok=True)
        recs = {}
        for key, run in ALL_RUNS.items():
            folder = ROOT / "results" / ds / run[3 + ds_index]
            f = next(p for p in folder.glob("*.jsonl") if p.name != "questions.jsonl")
            recs[key] = ({r["id"]: r for r in map(json.loads, f.read_text().splitlines())}, folder)
        for line in (ROOT / "data" / ds / "questions.jsonl").read_text().splitlines():
            q = json.loads(line)
            shutil.copy(ROOT / "data" / ds / "items" / q["slug"] / "barcode.png", out / "barcodes" / ds / f"{q['slug']}.png")
            runs, detail = {}, {}
            for key, (by_id, folder) in recs.items():
                r = by_id.get(q["id"])
                if r is None or "error" in r:  # a run that stopped early has no answer here
                    runs[key] = {"g": None, "ok": False, "n": 0}
                    continue
                runs[key] = {"g": r.get("guess"), "ok": bool(r["correct"]), "n": len(r.get("tools") or [])}
                detail[key] = {"text": scrub(r.get("response") or "", 6000)}
                if key in ("codex", "cc"):
                    events = load_events(folder / "transcripts" / f"{q['id']}.jsonl")
                    detail[key]["steps"] = (codex_steps if key == "codex" else claude_steps)(events)
                    detail[key]["seconds"] = r.get("seconds")
            key = f"{ds}/{q['slug']}"
            if key in EXAMPLES:
                (out / "t" / f"{ds}__{q['slug']}.js").write_text(f"MCB.detail({json.dumps(key)}, {json.dumps(detail)});\n")
            questions.append({"id": q["slug"], "ds": ds, "title": q["answer_title"], "choices": q["choices"],
                              "answer": q["answer"], "runs": runs})
    return questions


def build_table(runs):
    rows = []
    for label, logo, films, youtube, *base in runs:
        f, y = (json.loads((ROOT / "results" / ds / run / "summary.json").read_text())[0]
                for ds, run in (("films", films), ("youtube", youtube)))
        fn, yn = f["n"] - f["errors"], y["n"] - y["errors"]  # questions that got an answer
        rows.append({"label": label, "logo": logo, "films": 100 * f["correct"] / fn, "youtube": 100 * y["correct"] / yn,
                     "overall": 100 * (f["correct"] + y["correct"]) / (fn + yn), "answered": fn + yn,
                     "base": base[0] if base else None})
    return rows


def build_results():
    """The API rows, best first, each followed by its agent harness run with the change from the model alone."""
    agents = {r["base"]: r for r in build_table(AGENTS)}
    rows = []
    for r in sorted(build_table(TABLE), key=lambda r: -r["overall"]):
        rows.append(r)
        a = agents.get(r["label"])
        if a:
            a["delta"] = {k: a[k] - r[k] for k in ("films", "youtube", "overall")}
            a["label"] = "+ " + a["label"].split(" + ")[1]
            rows.append(a)
    return rows


def video_for(ds, slug, entry, videos, tmp):
    local = next(iter(sorted((videos / ds).glob(f"{slug}.*"))), None) if videos else None
    return local or viewer.download(ds, entry, tmp)


def item_dir(ds, slug):
    return Path(__file__).with_name("quiz") / slug if ds == "quiz" else ROOT / "data" / ds / "items" / slug


def frame_item(out, pick, videos):
    """One video's sheet of frames, made if it is not there yet, and what the page needs to scrub through it."""
    (out / "frames").mkdir(exist_ok=True)
    ds, slug = pick.split(":")
    entry = json.loads((item_dir(ds, slug) / "entry.json").read_text())
    seconds = entry["frames_sampled"] / entry["sample_fps"]
    n = min(THUMBS, entry["frames_sampled"])
    rows = math.ceil(n / COLS)
    sheet = out / "frames" / f"{slug}.jpg"
    if not sheet.exists():
        print(f"frames for {entry['label']}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            video = video_for(ds, slug, entry, videos, tmp)
            vf = f"fps={n / seconds:.8f},scale=iw*sar:ih,scale={THUMB_W}:-2,tile={COLS}x{rows}"
            subprocess.run([extract.FFMPEG, "-nostdin", "-v", "error", "-i", str(video), "-vf", vf,
                            "-frames:v", "1", "-q:v", "7", str(sheet)], check=True)
    w, h = Image.open(sheet).size
    return {"id": slug, "ds": ds, "title": entry["label"], "n": n, "cols": COLS, "w": w // COLS, "h": h // rows, "dur": seconds}


def build_quiz(out, videos, questions):
    """Each quiz question: its frames, its four titles, and how many of the plain API runs got it (None if it is not one of the 200)."""
    asked = {f"{q['ds']}:{q['id']}": q for q in questions}
    base = [k for k, run in ALL_RUNS.items() if not run[2].startswith("+")]
    (out / "barcodes" / "quiz").mkdir(parents=True, exist_ok=True)
    items = []
    for pick, titles in QUIZ + BONUS:
        item = frame_item(out, pick, videos)
        if item["ds"] == "quiz":
            shutil.copy(item_dir("quiz", item["id"]) / "barcode.png", out / "barcodes" / "quiz" / f"{item['id']}.png")
        q = asked.get(pick)
        item.update(opts=[t or re.sub(r" \(\d{4}\)$", "", item["title"]) for t in titles], answer=titles.index(None),
                    solved=sum(q["runs"][k]["ok"] for k in base) if q else None, bonus=(pick, titles) in BONUS)
        items.append(item)
    return items


def cover(out, questions):
    """A 1200x630 stack of barcodes for link previews."""
    img = Image.new("RGB", (1200, 630))
    picks = [q for q in questions if q["ds"] == "youtube"][::5][:21]
    for i, q in enumerate(picks):
        bar = Image.open(out / "barcodes" / q["ds"] / f"{q['id']}.png").convert("RGB").resize((1200, 30))
        img.paste(bar, (0, i * 30))
    img.save(out / "cover.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="folder to write the post into")
    ap.add_argument("--videos", help="folder of already downloaded videos, <dataset>/<slug>.<ext>")
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    extract.FFMPEG = extract.find_ffmpeg()

    questions = build_questions(out)
    videos = Path(args.videos).expanduser() if args.videos else None
    frames = [frame_item(out, pick, videos) for pick in VIEWER]
    quiz = build_quiz(out, videos, questions)
    cover(out, questions)
    plot = Image.open(ROOT / "results" / "overall.png").convert("RGB")
    # the footnote under the chart is written in the page instead, so cut the image off above it
    inked = [plot.crop((0, y, plot.width, y + 1)).getextrema() != ((255, 255),) * 3 for y in range(plot.height)]
    last = max(y for y, on in enumerate(inked) if on)
    top = next(y for y in range(last, 0, -1) if not inked[y])  # first blank row above the footnote
    plot.crop((0, 0, plot.width, top - 4)).save(out / "results.png")
    shutil.copy(ROOT / "video" / "videocolorbench.mp4", out / "teaser.mp4")
    labels = {k: v[0] for k, v in ALL_RUNS.items()}
    tags = {k: {"logo": v[1], "text": v[2]} for k, v in ALL_RUNS.items()}
    (out / "data.js").write_text(f"MCB.init({json.dumps({'questions': questions, 'viewer': frames, 'quiz': quiz, 'runs': labels, 'tags': tags, 'examples': list(EXAMPLES.items()), 'table': build_results()})});\n")
    shutil.copy(Path(__file__).with_name("page.html"), out / "index.html")
    shutil.copy(Path(__file__).with_name("questions.html"), out / "questions.html")
    shutil.copy(Path(__file__).with_name("huggingface_logo.png"), out / "huggingface_logo.png")
    shutil.copy(Path(__file__).with_name("example-2001.png"), out / "example-2001.png")
    for i in range(1, 4):
        shutil.copy(Path(__file__).with_name(f"example-2001-{i}.jpg"), out / f"example-2001-{i}.jpg")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"wrote {out} ({len(questions)} questions, {len(frames)} viewer items, {len(quiz)} quiz questions, {size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
