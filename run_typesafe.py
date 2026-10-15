"""TypeSafe mode: ask Jev, TypeSafe's System One model, through its own API (not OpenRouter).

Jev doesn't write text, it answers typed questions. Each barcode goes in the `state` as an image
and the 10 titles are the options of one Choice question; the guess is the option with the
highest probability. Options are keyed by a slug of the title, not A to J: with letter keys Jev
picks A about 55% of the time whatever the image, while with title keys the first option shown
gets picked about 14% of the time. Images are a "danger zone" feature, turned on with
the TypeSafe-Danger-Zone-Images header (https://docs.typesafe.ai/images).

Jev rejects images under 256 pixels on a side or wider than about 3:1 (the ratio limit isn't in
the docs: 1000 x 334 is accepted, 1000 x 256 is not). The barcodes are 1000 x 200, so each one is
stretched to 1000 x 334 with nearest-neighbour scaling. The stripes are vertical, so this adds
and removes nothing.
"""

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image

import run_eval

API = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1").rstrip("/") + "/systemone"
MIN_SIDE = 256
MAX_RATIO = 3
INSTRUCTIONS = """Each vertical stripe in `barcode` is a few seconds of a {kind}, shrunk down to its average color. The stripes run in order from the beginning of the {kind} to the end.

Which {kind} is it?"""


def instructions(q):
    return INSTRUCTIONS.format(kind=run_eval.KIND[q.get("dataset", "films")])


def option_keys(q):
    """Option name for each choice, in order: a slug of the title, e.g. queen_bohemian_rhapsody_1975."""
    return [re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_")[:60] or f"option_{i}" for i, c in enumerate(q["choices"])]


def barcode_png(q):
    path = run_eval.DATA / q.get("dataset", "films") / "items" / q["slug"] / "barcode.png"
    img = Image.open(path).convert("RGB")
    w, h = img.size
    size = (max(w, MIN_SIDE, -(-h // MAX_RATIO)), max(h, MIN_SIDE, -(-w // MAX_RATIO)))
    if size != (w, h):
        img = img.resize(size, Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), img.size


def build_body(q, model):
    ref = f"barcode-{uuid.uuid4()}"
    png, size = barcode_png(q)
    body = {
        "model": model,
        "state": {"barcode": ref},
        "assets": {ref: {"type": "image", "format": "png", "data_base64": base64.b64encode(png).decode()}},
        "questions": {"title": {
            "type": "choice",
            "instructions": instructions(q),
            "criteria": dict(zip(option_keys(q), q["choices"])),
        }},
    }
    return body, size


def call(body, timeout, key):
    data = json.dumps(body).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "TypeSafe-Danger-Zone-Images": "true",
    }
    delay = 2.0
    for attempt in range(6):
        try:
            req = urllib.request.Request(API, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def run_one(q, cfg, key, raw_dir, name):
    """One System One request. Keeps the full answer, including every option's probability."""
    body, size = build_body(q, cfg["model"])
    rec = {"model": name, "id": q["id"], "answer": q["answer"], "answer_title": q["answer_title"],
           "prompt": instructions(q), "image_size": list(size)}
    start = time.time()
    try:
        resp = call(body, cfg.get("timeout", 300), key)
        ans = resp["answers"]["title"]
        letter = {k: chr(65 + i) for i, k in enumerate(option_keys(q))}  # back to the A-J letters the scores use
        probs = {letter[k]: p for k, p in (ans.get("probabilities") or {}).items() if k in letter}
        guess = letter.get(ans.get("choice"))
        rec.update(guess=guess, guess_title=q["choices"][ord(guess) - 65] if guess else None,
                   correct=guess == q["answer"], response=ans.get("choice"), probabilities=probs,
                   confidence=ans.get("confidence"), p_answer=probs.get(q["answer"]),
                   usage=resp.get("usage"), cost_usd=None, served_model=resp.get("model"))
    except Exception as e:
        resp = {"error": run_eval.redact(str(e))}
        rec.update(guess=None, correct=False, error=resp["error"])
    rec["seconds"] = round(time.time() - start, 1)
    sent = {k: v for k, v in body.items() if k != "assets"}  # the image is already on disk
    (raw_dir / f"{q['id']}.json").write_text(json.dumps({"request": sent, "response": resp}, indent=1))
    return rec
