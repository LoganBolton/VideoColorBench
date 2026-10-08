# Experiments

One YAML file per experiment. Run it with

```bash
uv run run.py experiments/claude-code-youtube.yaml
```

Add `--check` to validate the file and print the prompt without running anything, or `--limit 3`
for a quick trial.

## Fields

| field | modes | meaning |
|-------|-------|---------|
| `name` | both | used in the results folder name |
| `mode` | both | `raw` (one API request per question) or `harness` (an agent session per question) |
| `dataset` | both | `films` or `youtube` |
| `model` | both | raw: the OpenRouter id, e.g. `openai/gpt-6.1-sol`. harness: the name the CLI takes, e.g. `claude-opus-5-5` |
| `questions_sha256` | both | pin: hash of the questions file. The run stops if the questions have changed |
| `questions` | both | a different questions file (default `data/<dataset>/questions.jsonl`) |
| `limit`, `concurrency`, `timeout` | both | first N questions, parallel questions, seconds per question |
| `notes` | both | free text, copied into the results |
| `reasoning_effort` | raw | `minimal`, `low`, `medium` or `high` |
| `temperature`, `max_tokens`, `seed` | raw | sent as given, left out of the request if absent |
| `provider` | raw | pin one OpenRouter provider, with fallbacks off |
| `harness` | harness | `claude` (Claude Code), `codex` or `opencode` (any OpenRouter model, as `openrouter/<id>`) |
| `harness_version` | harness | pin: the CLI version. The run stops if a different version is installed |
| `effort` | harness | reasoning effort, passed to the CLI |
| `tools` | harness | Claude Code only: the tools it may use (default Read, Bash, WebSearch, WebFetch) |
| `tools_note` | harness | replaces the paragraph that tells the agent what it may do |

Use full model names (`claude-opus-5-5`), not aliases like `opus`, which move to newer models over time.

## What a run keeps

Each run gets its own folder, `results/<dataset>/<timestamp>-<name>/`:

- `config.yaml`, the file as you ran it, and `resolved.yaml`, the same with every pin filled in.
  `uv run run.py results/.../resolved.yaml` repeats the run and refuses if anything pinned has moved
  (`--allow-drift` to run anyway; the difference is written to the manifest).
- `manifest.json`: git commit and whether there were uncommitted changes, Python and package
  versions, CLI version and the exact command line, hashes of the code, `uv.lock`, the questions
  file and every barcode image, the prompt template, start and finish times.
- `questions.jsonl`: a copy of the questions asked.
- `<model>.jsonl`: one line per question with the full prompt, the reply, the guess, whether it
  was right, token usage, cost, seconds taken. Harness lines also list the tools used and `leak`.
- `raw/<id>.json` (raw mode): the request settings and the complete API response, including the
  reasoning text when the provider returns it, the provider and the exact model version served.
- `transcripts/<id>.jsonl` (harness mode): every event of the session, so each tool call, web
  search, cropped image and intermediate message can be read back.
- `summary.json` and `run.log`.

If a run is interrupted, `--resume results/<dataset>/<folder>` continues it; questions that
errored are tried again.

## Harness mode notes

- Each question runs in a fresh temporary folder that holds only `barcode.png`, with user
  settings, memory and MCP servers switched off. Both agents get the barcode attached to the
  first message as well as on disk. (The first Claude Code run, `20261001-183201`, predates this:
  there the agent had to open the file itself.)
- This repository is public and contains the answers. The prompt tells the agent not to look for
  them, and any session whose transcript mentions the repository is marked `leak: true` and
  counted in the summary, so those questions can be excluded.
- Agents may not download the candidate videos and rebuild their barcodes, since that is matching
  files rather than recognising a video. Looking at thumbnails and preview frames is allowed. The prompt says so, `yt-dlp`, `youtube-dl`, `ffmpeg` and
  `ffprobe` are replaced by stand-ins that refuse to run, and any session whose tool calls mention
  a video downloader is marked `download: true` and scored as wrong. The Claude Code and Codex runs
  from 1 and 2 October 2026 predate this rule.
- Codex with a ChatGPT login only offers some models (`codex debug models` lists them).
- The same model can't be pinned harder than its name: providers may update what a name serves.
  The transcripts and `raw/` files record what actually answered.
- One earlier run, `20261001-235334-claude-code-opus-youtube-videos`, gave the agent the ten
  candidate videos on disk. It scored 99/100 by recomputing each video's barcode, which measures
  nothing about recognising a video from its colors, so that option was removed. The run's
  `config.yaml` (with `videos: true`) is kept for the record and no longer runs.
