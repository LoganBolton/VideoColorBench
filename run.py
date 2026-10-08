"""Run one experiment from a YAML file. This is the entry point for every eval.

    uv run run.py --model openai/gpt-6.1-sol                          # any OpenRouter model, both datasets, no file needed
    uv run run.py --model z-ai/glm-5.3-flash --harness opencode       # the same model inside the opencode agent
    uv run --with datasets run.py --model openai/gpt-6.1-sol --hf     # questions and barcodes from Hugging Face
    uv run run.py experiments/sol-medium-youtube-raw.yaml
    uv run run.py experiments/claude-code-youtube.yaml --limit 3      # quick trial, same settings
    uv run run.py results/youtube/<run>/resolved.yaml                 # repeat an earlier run exactly
    uv run run.py experiments/x.yaml --resume results/youtube/<run>   # finish an interrupted run

There are two modes, set by `mode:` in the file:

    raw       one OpenRouter API request per question: image in, letter out (run_eval.py)
    harness   one Claude Code, Codex or opencode session per question, with tools and web search (run_harness.py)

See experiments/README.md for every field. Each run writes a folder under results/<dataset>/ with

    config.yaml       the file you ran, untouched
    resolved.yaml     the same settings with every pin filled in: run this to repeat the run
    manifest.json     git commit, tool and package versions, file hashes, prompt, timings
    questions.jsonl   the exact questions asked
    <model>.jsonl     one line per question: prompt, reply, guess, right or wrong, usage, cost
    raw/              (raw mode) the full API request settings and response for every question
    transcripts/      (harness mode) every event of every session: tool calls, searches, replies
    summary.json      the score
    run.log           what was printed

Pins. If the file names `questions_sha256` or `harness_version`, the run stops when
the current value differs, so an old experiment can't silently run against changed questions or a
newer CLI. Pass --allow-drift to run anyway (the drift is recorded in the manifest).
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import yaml

import run_eval
import run_harness

ROOT = Path(__file__).resolve().parent
COMMON = {"name", "mode", "dataset", "model", "limit", "concurrency", "timeout", "notes",
          "questions", "questions_sha256"}
FIELDS = {
    "raw": COMMON | {"reasoning_effort", "temperature", "max_tokens", "seed", "provider"},
    "harness": COMMON | {"harness", "harness_version", "effort", "tools", "tools_note"},
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sh(*cmd):
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def cli_version(name):
    m = re.search(r"\d+\.\d+\.\d+\S*", sh(name, "--version"))
    return m.group() if m else None


def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text())
    mode = cfg.get("mode")
    if mode not in FIELDS:
        sys.exit(f"{path}: mode must be raw or harness")
    unknown = set(cfg) - FIELDS[mode]
    if unknown:
        sys.exit(f"{path}: unknown fields for mode {mode}: {sorted(unknown)}")
    for need in ["name", "dataset", "model"] + (["harness"] if mode == "harness" else []):
        if not cfg.get(need):
            sys.exit(f"{path}: `{need}` is required")
    return cfg


class Log:
    def __init__(self, path):
        self.f = open(path, "a")

    def __call__(self, msg=""):
        print(msg, flush=True)
        self.f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}  {msg}\n")
        self.f.flush()


def raw_one(q, cfg, key, raw_dir, name):
    """One OpenRouter request. Keeps the complete response, not just the reply text."""
    prompt = run_eval.prompt_text(q)
    rec = {"model": name, "id": q["id"], "answer": q["answer"], "answer_title": q["answer_title"], "prompt": prompt}
    body = {"model": cfg["model"], "messages": run_eval.build_messages(q), "usage": {"include": True}}
    for k in ("temperature", "max_tokens", "seed"):
        if cfg.get(k) is not None:
            body[k] = cfg[k]
    if cfg.get("reasoning_effort"):
        body["reasoning"] = {"effort": cfg["reasoning_effort"]}
    if cfg.get("provider"):
        body["provider"] = {"order": [cfg["provider"]], "allow_fallbacks": False}
    start = time.time()
    try:
        resp = run_eval.call(body, cfg.get("timeout", 300), key)
        msg = resp["choices"][0]["message"]
        text = msg.get("content") or ""
        guess = run_eval.parse_answer(text, len(q["choices"]))
        rec.update(guess=guess, guess_title=q["choices"][ord(guess) - 65] if guess else None,
                   correct=guess == q["answer"], response=text, reasoning=msg.get("reasoning"),
                   usage=resp.get("usage"), cost_usd=(resp.get("usage") or {}).get("cost"),
                   served_model=resp.get("model"), provider=resp.get("provider"), generation_id=resp.get("id"))
    except Exception as e:
        resp = {"error": run_eval.redact(str(e))}
        rec.update(guess=None, correct=False, error=resp["error"])
    rec["seconds"] = round(time.time() - start, 1)
    sent = {k: v for k, v in body.items() if k != "messages"}  # the image and prompt are already on disk
    (raw_dir / f"{q['id']}.json").write_text(json.dumps({"request": sent, "response": resp}, indent=1))
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?", help="an experiments/*.yaml file, or the resolved.yaml of an earlier run")
    ap.add_argument("--model", help="an OpenRouter model id. Runs it in raw mode without an experiment file")
    ap.add_argument("--dataset", choices=["films", "youtube", "both"], default="both", help="with --model (default both)")
    ap.add_argument("--reasoning-effort", choices=["minimal", "low", "medium", "high"], help="with --model")
    ap.add_argument("--harness", choices=list(run_harness.READERS), help="with --model: run it inside this agent instead of raw mode")
    ap.add_argument("--hf", action="store_true", help="fetch the questions and barcodes from Hugging Face instead of data/")
    ap.add_argument("--timeout", type=int, help="seconds per question (overrides the file)")
    ap.add_argument("--concurrency", type=int, help="questions in parallel (overrides the file)")
    ap.add_argument("--limit", type=int, help="only the first N questions (overrides the file)")
    ap.add_argument("--resume", help="an earlier results folder of this experiment to continue")
    ap.add_argument("--allow-drift", action="store_true", help="run even if a pinned hash or version differs")
    ap.add_argument("--check", action="store_true", help="validate the file and pins, print the plan, run nothing")
    args = ap.parse_args()

    if bool(args.config) == bool(args.model):
        ap.error("give an experiment file or --model, not both")
    if args.hf:
        import hf_data
        run_eval.DATA = hf_data.fetch()
    if args.config:
        return run(load_config(args.config), args)
    if args.resume and args.dataset == "both":
        ap.error("--resume with --model needs --dataset films or youtube")
    scores = []
    for dataset in ("films", "youtube") if args.dataset == "both" else (args.dataset,):
        short = args.model.split("/")[-1].replace(":", "-")
        if args.harness:
            # opencode reaches OpenRouter models as openrouter/<id>
            model = f"openrouter/{args.model}" if args.harness == "opencode" and not args.model.startswith("openrouter/") else args.model
            cfg = {"name": f"{args.harness}-{short}-{dataset}", "mode": "harness", "harness": args.harness,
                   "dataset": dataset, "model": model}
        else:
            cfg = {"name": f"{short}-{dataset}-raw", "mode": "raw", "dataset": dataset, "model": args.model}
        if args.reasoning_effort:
            cfg["effort" if args.harness else "reasoning_effort"] = args.reasoning_effort
        scores.append(run(cfg, args))
    if len(scores) > 1 and all(scores):
        n, correct = sum(s["n"] for s in scores), sum(s["correct"] for s in scores)
        print(f"\n{args.model} overall: {correct}/{n} = {correct / n:.1%}")


def run(cfg, args):
    mode, dataset = cfg["mode"], cfg["dataset"]
    if args.limit:
        cfg["limit"] = args.limit
    if args.concurrency:
        cfg["concurrency"] = args.concurrency
    if args.timeout:
        cfg["timeout"] = args.timeout
    qpath = ROOT / cfg["questions"] if cfg.get("questions") else run_eval.DATA / dataset / "questions.jsonl"
    questions = [json.loads(line) for line in qpath.read_text().splitlines() if line.strip()]
    asked = questions[: cfg["limit"]] if cfg.get("limit") else questions

    commit = sh("git", "rev-parse", "HEAD")
    current = {"questions_sha256": sha256(qpath)}
    if mode == "harness":
        if not shutil.which(cfg["harness"]):
            sys.exit(f"{cfg['harness']} is not installed")
        current["harness_version"] = cli_version(cfg["harness"])
    drift = {k: {"pinned": cfg[k], "now": v} for k, v in current.items() if cfg.get(k) and str(cfg[k]) != str(v)}
    for k, d in drift.items():
        print(f"pin mismatch: {k} is pinned to {d['pinned']} but is now {d['now']}")
    if drift and not args.allow_drift and not args.check:
        sys.exit("stopping. Use --allow-drift to run anyway, or update the pins in the file.")

    if mode == "raw":
        name = cfg["model"]
        prompt_template = run_eval.PROMPT
        example = run_eval.prompt_text(asked[0])
    else:
        h = SimpleNamespace(harness=cfg["harness"], model=cfg["model"], effort=cfg.get("effort"),
                            tools=cfg.get("tools"), tools_note=cfg.get("tools_note"), timeout=cfg.get("timeout", 900))
        name = f"{cfg['harness']}:{cfg['model']}" + (f":{cfg['effort']}" if cfg.get("effort") else "")
        prompt_template = run_harness.harness_prompt({"dataset": dataset, "choices": ["{options}"]}, h.tools_note)
        example = run_harness.harness_prompt(asked[0], h.tools_note)
    if args.check:
        print(f"{cfg['name']}: {mode} mode, {name}, {len(asked)} {dataset} questions\n\n{example}")
        if mode == "harness":
            print("\n" + " ".join(run_harness.command(h, "<prompt>", "<temp dir>")))
        return

    key = None
    if mode == "raw":
        found = re.search(r"sk-or-[A-Za-z0-9_-]+", os.environ.get("OPENROUTER_API_KEY", ""))
        if not found:
            sys.exit("set OPENROUTER_API_KEY (get one at https://openrouter.ai/keys)")
        key = found.group()
        run_eval.check_models([cfg["model"]], key)

    out = Path(args.resume) if args.resume else ROOT / "results" / dataset / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{cfg['name']}"
    out.mkdir(parents=True, exist_ok=True)
    log = Log(out / "run.log")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not args.config:
        (out / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    elif Path(args.config).resolve() != (out / "config.yaml").resolve():
        shutil.copy(args.config, out / "config.yaml")
    shutil.copy(qpath, out / "questions.jsonl") if qpath.resolve() != (out / "questions.jsonl").resolve() else None
    resolved = {**cfg, **current}
    (out / "resolved.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False, allow_unicode=True))
    manifest = {
        "name": cfg["name"], "started": started, "config": resolved, "drift": drift,
        "command": " ".join(sys.argv), "git_commit": commit, "git_dirty": bool(sh("git", "status", "--porcelain", "--untracked-files=no")),
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {p: metadata.version(p) for p in ("pyyaml", "pillow", "numpy") if _has(p)},
        "uv_lock_sha256": sha256(ROOT / "uv.lock"),
        "code_sha256": {f: sha256(ROOT / f) for f in ("run.py", "run_eval.py", "run_harness.py")},
        "harness_version": current.get("harness_version"),
        "harness_command": run_harness.command(h, "<prompt>", "<temp dir>") if mode == "harness" else None,
        "api_url": run_eval.API if mode == "raw" else None,
        "data": "huggingface" if args.hf else "repo",
        "prompt_template": prompt_template,
        "barcode_sha256": {q["id"]: sha256(run_eval.DATA / dataset / "items" / q["slug"] / "barcode.png") for q in asked},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")

    fname = out / (re.sub(r"[/:]", "__", name) + ".jsonl")
    recs = [json.loads(line) for line in fname.read_text().splitlines()] if fname.exists() else []
    recs = [r for r in recs if "error" not in r]  # failed questions get another go
    done = {r["id"] for r in recs}
    todo = [q for q in asked if q["id"] not in done]
    log(f"{cfg['name']}: {mode} mode, {name}, {len(todo)} questions to run, {len(done)} already done")
    log(f"git {commit[:10]}{' (uncommitted changes)' if manifest['git_dirty'] else ''}, questions {current['questions_sha256'][:12]}"
        + (f", {cfg['harness']} {current['harness_version']}" if mode == "harness" else ""))

    detail = out / ("raw" if mode == "raw" else "transcripts")
    detail.mkdir(exist_ok=True)

    def one(q):
        rec = raw_one(q, cfg, key, detail, name) if mode == "raw" else run_harness.run_one(q, h, name, detail)
        mark = "error" if "error" in rec else "right" if rec["correct"] else "wrong"
        extra = f"  {rec['error'][:120]}" if "error" in rec else f"  guessed {rec.get('guess_title')}" if not rec["correct"] else ""
        log(f"  {mark:5}  {rec['seconds']:>6.0f}s  {q['answer_title']}{extra}{'  LEAK' if rec.get('leak') else ''}{'  DOWNLOAD' if rec.get('download') else ''}")
        return rec

    with ThreadPoolExecutor(cfg.get("concurrency", 8 if mode == "raw" else 4)) as ex:
        for rec in ex.map(one, todo):
            recs.append(rec)
            fname.write_text("".join(json.dumps(r) + "\n" for r in recs))

    n, correct = len(recs), sum(r["correct"] for r in recs)
    costs = [r["cost_usd"] for r in recs if r.get("cost_usd")]
    s = {"model": name, "n": n, "correct": correct, "accuracy": correct / n if n else 0.0,
         "errors": sum("error" in r for r in recs),
         "unparsed": sum(r.get("guess") is None and "error" not in r for r in recs),
         "leaks": sum(bool(r.get("leak")) for r in recs), "downloads": sum(bool(r.get("download")) for r in recs), "cost_usd": round(sum(costs), 4) if costs else None}
    (out / "summary.json").write_text(json.dumps([s], indent=2) + "\n")
    manifest["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    log(f"\n{name}: {correct}/{n} = {s['accuracy']:.1%}, {s['errors']} errors, {s['unparsed']} unparsed, "
        f"{s['leaks']} leaks, {s['downloads']} tried to download a video, cost {'$%.2f' % s['cost_usd'] if s['cost_usd'] else 'not reported'}")
    log(f"chance is {1 / len(asked[0]['choices']):.0%}. results in {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    if s["errors"] == n:
        sys.exit(1)
    return s


def _has(pkg):
    try:
        metadata.version(pkg)
        return True
    except metadata.PackageNotFoundError:
        return False


if __name__ == "__main__":
    main()
