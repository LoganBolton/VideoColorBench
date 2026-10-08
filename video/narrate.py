"""Speak each line of script.json with Kokoro-82M (hexgrad/Kokoro-82M on Hugging Face, runs on CPU).

    uv run --no-project --python 3.12 --with "kokoro>=0.9" --with soundfile python <repo>/video/narrate.py <work dir>/audio

Run it from outside the repo, since inside it uv resolves an old, unbuildable set of packages.

Writes <id>.wav per line plus durations.json.
"""

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

VOICE, RATE = "am_michael", 24000

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
lines = json.loads((Path(__file__).parent / "script.json").read_text())
pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cpu")
durations = {}
for line in lines:
    audio = np.concatenate([chunk.audio.numpy() for chunk in pipe(line["text"], voice=VOICE, speed=line.get("speed", 1.0))])
    sf.write(out / f"{line['id']}.wav", audio, RATE)
    durations[line["id"]] = len(audio) / RATE
    print(f"{line['id']:6} {durations[line['id']]:.2f}s", flush=True)
(out / "durations.json").write_text(json.dumps(durations, indent=1))
print(f"total {sum(durations.values()):.2f}s")
