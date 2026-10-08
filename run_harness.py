"""Harness mode: the same questions as run_eval.py, answered by a coding agent that can use tools.

Each question runs in its own Claude Code, Codex or opencode session, which can open and crop the
barcode, run code and search the web before answering. The CLI must already be installed and logged
in (opencode reads OPENROUTER_API_KEY, so any OpenRouter model works with it). run.py is the entry point.

Every session starts in an empty temporary folder holding only barcode.png, so the answer key in
this repo isn't in its working directory. An agent could still find the repo online, so any session
whose transcript mentions it is marked "leak". Agents may look at thumbnails and preview frames of
the candidates but may not download the videos: the downloaders are blocked and a session that
tries is marked "download" and scored as wrong.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import run_eval
from run_eval import ROOT, parse_answer, prompt_text

TOOLS_NOTE = """

The image is barcode.png in the current directory. You are encouraged to use tools such as image inspection or web search to help get your answer: open the image, crop or zoom into parts of it, run code on it, and search the web. Do not download or stream any of the candidate videos. Looking at thumbnails or preview frames of them is fine. Do not look for this benchmark's answer key or its repository."""
LEAK = re.compile(r"moviecolorbench|videocolorbench|loganbolton", re.I)
# downloading the candidates and recomputing their barcodes is matching files, not recognising a video
DOWNLOAD = re.compile(r"yt-dlp|yt_dlp|youtube-dl|youtube_dl|pytube|pytubefix|googlevideo\.com|archive\.org/download|ffmpeg|ffprobe", re.I)
BLOCKED = ("yt-dlp", "youtube-dl", "ffmpeg", "ffprobe")
CLAUDE_TOOLS = "Read,Bash,WebSearch,WebFetch"


def block_dir():
    """A folder of stand-ins that goes first on PATH, so the video tools refuse to run."""
    d = Path(tempfile.mkdtemp(prefix="blocked-"))
    for name in BLOCKED:
        (d / name).write_text("#!/bin/sh\necho 'blocked: downloading or decoding videos is not allowed in this benchmark' >&2\nexit 1\n")
        (d / name).chmod(0o755)
    return str(d)


def tool_inputs(obj):
    """Every tool input in a transcript, whatever the agent calls them."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("input", "command", "arguments"):
                yield json.dumps(v)
            else:
                yield from tool_inputs(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from tool_inputs(x)


def harness_prompt(q, note=None):
    return prompt_text(q).replace("this image", "the image barcode.png") + (TOOLS_NOTE if note is None else "\n\n" + note.strip())


def command(args, prompt, workdir):
    if args.harness == "claude":
        tools = ",".join(getattr(args, "tools", None) or CLAUDE_TOOLS.split(","))
        # the prompt and image go on stdin as one message (see stdin_for), the same way Codex gets them with -i
        cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
               "--setting-sources", "", "--strict-mcp-config", "--tools", tools,
               "--allowedTools", tools, "--permission-mode", "dontAsk"]
        if args.model:
            cmd += ["--model", args.model]
        if args.effort:
            cmd += ["--effort", args.effort]
        return cmd
    if args.harness == "opencode":
        # --standalone: a private server per session. --auto: no permission prompts, there is nobody to answer them
        cmd = ["opencode", "run", "--standalone", "--auto", "--format", "json"]
        if args.model:
            cmd += ["-m", args.model + (f"#{args.effort}" if args.effort else "")]
        return cmd + ["-f", str(Path(workdir).resolve() / "barcode.png"), prompt]
    cmd = ["codex", "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
           "--sandbox", "workspace-write", "-c", 'web_search="live"', "-C", str(workdir)]
    if args.model:
        cmd += ["-m", args.model]
    if args.effort:
        cmd += ["-c", f'model_reasoning_effort="{args.effort}"']
    # the prompt goes on stdin: -i takes a list of files and would swallow a trailing prompt argument
    return cmd + ["-i", str(Path(workdir) / "barcode.png"), "-"]


def stdin_for(args, prompt, workdir):
    if args.harness == "codex":
        return prompt
    if args.harness == "opencode":
        return ""  # the prompt is an argument and the image is attached with -f
    image = base64.b64encode((Path(workdir) / "barcode.png").read_bytes()).decode()
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
               {"type": "text", "text": prompt}]
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"


def trim(obj, limit=200_000):
    """Cut absurdly long strings so one runaway tool output can't bloat a transcript."""
    if isinstance(obj, str):
        return obj if len(obj) <= limit else obj[:limit] + f"... [{len(obj) - limit} more chars]"
    if isinstance(obj, list):
        return [trim(x, limit) for x in obj]
    if isinstance(obj, dict):
        return {k: trim(v, limit) for k, v in obj.items()}
    return obj


def read_claude(events):
    text, tools, usage, cost = "", [], None, None
    for e in events:
        if e.get("type") == "assistant":
            for block in e.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools.append(block.get("name"))
                elif block.get("type") == "text":
                    text = block.get("text") or text
        elif e.get("type") == "result":
            text = e.get("result") or text
            usage, cost = e.get("usage"), e.get("total_cost_usd")
            if e.get("is_error"):
                raise RuntimeError(str(e.get("result") or e.get("subtype"))[:500])
    return text, tools, usage, cost


def read_codex(events):
    text, tools, usage = "", [], None
    for e in events:
        item = e.get("item") or {}
        if e.get("type") == "item.completed":
            if item.get("type") == "agent_message":
                text = item.get("text") or text
            elif item.get("type") not in (None, "reasoning", "error"):
                tools.append(item["type"])
        elif e.get("type") == "turn.completed":
            usage = e.get("usage")
        elif e.get("type") in ("error", "turn.failed"):
            raise RuntimeError(json.dumps(e.get("error") or e.get("message") or e)[:500])
    return text, tools, usage, None


def read_opencode(events):
    text, tools, usage, cost = "", [], {"input": 0, "output": 0, "reasoning": 0}, 0.0
    for e in events:
        part = e.get("part") or {}
        if e.get("type") == "text":
            text = part.get("text") or text
        elif e.get("type") == "tool_use":
            tools.append(part.get("tool"))
        elif e.get("type") == "step_finish":
            cost += part.get("cost") or 0
            for k in usage:
                usage[k] += (part.get("tokens") or {}).get(k) or 0
        elif e.get("type") == "error":
            raise RuntimeError(json.dumps(e.get("error") or e)[:500])
    return text, tools, usage, cost or None


READERS = {"claude": read_claude, "codex": read_codex, "opencode": read_opencode}


def run_one(q, args, name, transcripts):
    prompt = harness_prompt(q, getattr(args, "tools_note", None))
    rec = {"model": name, "id": q["id"], "answer": q["answer"], "answer_title": q["answer_title"], "prompt": prompt}
    start = time.time()
    with tempfile.TemporaryDirectory(prefix="barcode-") as tmp:
        shutil.copy(run_eval.DATA / q["dataset"] / "items" / q["slug"] / "barcode.png", Path(tmp) / "barcode.png")
        env = {**os.environ, "PWD": tmp, "PATH": block_dir() + os.pathsep + os.environ.get("PATH", "")}  # opencode takes its working folder from PWD, not the real one
        if args.harness == "opencode":  # keep the user's own opencode config and AGENTS.md out of the session
            env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="opencode-config-")
        try:
            r = subprocess.run(command(args, prompt, tmp), cwd=tmp, capture_output=True, text=True, timeout=args.timeout,
                               input=stdin_for(args, prompt, tmp), env=env)
            raw = r.stdout
        except subprocess.TimeoutExpired as e:
            r, raw = None, (e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else e.stdout or "")
    raw = run_eval.redact(raw)
    events = []
    for line in raw.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    (transcripts / f"{q['id']}.jsonl").write_text("".join(json.dumps(trim(e)) + "\n" for e in events))
    if r is not None and r.stderr.strip():
        (transcripts / f"{q['id']}.stderr.txt").write_text(run_eval.redact(r.stderr))
    try:
        if r is None:
            raise RuntimeError(f"timed out after {args.timeout:.0f}s")
        text, tools, usage, cost = READERS[args.harness](events)
        if not text:
            raise RuntimeError(f"no reply (exit {r.returncode}): {run_eval.redact(r.stderr.strip()[-400:])}")
        guess = parse_answer(text, len(q["choices"]))
        rec.update(guess=guess, guess_title=q["choices"][ord(guess) - 65] if guess else None,
                   correct=guess == q["answer"], response=text, usage=usage, cost_usd=cost, tools=tools)
    except Exception as e:
        rec.update(guess=None, correct=False, error=str(e))
    rec.update(seconds=round(time.time() - start, 1), leak=bool(LEAK.search(raw.replace(str(ROOT), "").replace(json.dumps(str(ROOT))[1:-1], ""))),  # not this checkout's own path
               download=any(DOWNLOAD.search(s) for s in tool_inputs(events)))
    if rec["download"]:  # tried to fetch a video: the answer doesn't count
        rec["correct"] = False
    return rec
