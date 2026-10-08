"""Raw mode: show a model a barcode and ask which movie (or YouTube video) it is, via OpenRouter.

The prompt, the request and the answer parsing live here. run.py is the entry point.

The model gets the barcode image (every frame, left to right, shrunk to its average color) and 10
options, so random guessing scores about 10%.
"""

import base64
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"  # run.py --hf points this at a copy fetched from Hugging Face
API = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
KIND = {"films": "movie", "youtube": "YouTube video"}
SECRET = re.compile(r"sk-or-[A-Za-z0-9_-]+")
PROMPT = """Each vertical stripe in this image is a few seconds of a {kind}, shrunk down to its average color. The stripes run in order from the beginning of the {kind} to the end.

Which {kind} is it?

{options}

Please respond with your best guess. End your reply with "Answer: X", where X is the letter."""


def prompt_text(q):
    kind = KIND[q.get("dataset", "films")]
    options = "\n".join(f"{chr(65 + i)}. {c}" for i, c in enumerate(q["choices"]))
    return PROMPT.format(kind=kind, options=options)


def barcode_data_url(q):
    path = DATA / q.get("dataset", "films") / "items" / q["slug"] / "barcode.png"
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def build_messages(q):
    return [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": barcode_data_url(q)}},
        {"type": "text", "text": prompt_text(q)},
    ]}]


def redact(text):
    """Blank out API keys before text is written to results, which are public.

    An HTTP error can quote the request's headers, and an agent can print its environment.
    """
    for name, value in os.environ.items():
        if name.endswith(("_API_KEY", "_TOKEN", "_SECRET")) and len(value.strip()) >= 16:
            text = text.replace(value.strip(), f"[{name}]")
    return SECRET.sub("sk-or-[redacted]", text)


def call(body, timeout, key):
    data = json.dumps(body).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Title": "VideoColorBench",
    }
    delay = 2.0
    for attempt in range(6):
        try:
            req = urllib.request.Request(API, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            if "error" in resp:
                raise RuntimeError(json.dumps(resp["error"]))
            return resp
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, RuntimeError):
            if attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def parse_answer(text, n):
    valid = "".join(chr(65 + i) for i in range(n))
    hits = re.findall(rf"answer\s*[:\-]?\s*\**\s*\(?([{valid}])\b", text or "", re.I)
    if hits:
        return hits[-1].upper()
    lone = re.fullmatch(rf"\s*\(?([{valid}])[\).]?\s*", text or "")
    return lone.group(1).upper() if lone else None


def check_models(models, key):
    """Exit with suggestions if any model id isn't on OpenRouter."""
    url = API.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            known = {m["id"] for m in json.load(r)["data"]}
    except Exception as e:
        print(f"could not fetch the model list ({e}), skipping the check", flush=True)
        return
    bad = [m for m in models if m not in known]
    for m in bad:
        close = difflib.get_close_matches(m, sorted(known), n=8, cutoff=0.3)
        family = sorted(k for k in known if k.split("/")[0] == m.split("/")[0] and any(t in k for t in re.split(r"[-/.]", m) if len(t) > 2))[:15]
        print(f"unknown model {m!r}. close matches: {close or 'none'}. same provider: {family or 'none'}")
    if bad:
        sys.exit(1)
