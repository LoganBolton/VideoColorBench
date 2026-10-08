---
license: mit
task_categories:
- visual-question-answering
- multiple-choice
language:
- en
pretty_name: VideoColorBench
size_categories:
- n<1K
configs:
- config_name: films
  data_files:
  - split: test
    path: films/test-*
- config_name: youtube
  data_files:
  - split: test
    path: youtube/test-*
---

# VideoColorBench

Can a model recognize a famous video from just its colors?

Each image is a barcode of one video. Every stripe is the average color of a few seconds, in order from start to end. The model picks which video it is out of 10 options, so guessing gets 10%.

| config | questions | what's in it |
|--------|-----------|--------------|
| `films` | 100 | feature films |
| `youtube` | 100 | the most viewed YouTube videos, mostly music videos |

```python
from datasets import load_dataset
ds = load_dataset("loganbolton/VideoColorBench", "films", split="test")
ds[0]["image"], ds[0]["choices"], ds[0]["answer"]
```

## Fields

| field | meaning |
|-------|---------|
| `image` | the barcode, 1000x200 PNG |
| `choices` | the 10 options |
| `answer`, `answer_index`, `answer_title` | the right option as a letter (A to J), an index (0 to 9) and text |
| `title`, `artist`, `year`, `genre` | about the video |
| `average_color` | mean color of the whole video as hex |
| `colorfulness` | how saturated the video is, near 0 for black and white |
| `seconds` | runtime |
| `source_url` | where the video came from |

The wrong options are picked to be plausible. Same genre, close in year, similar colorfulness.

## Running a model

The code is at https://github.com/LoganBolton/VideoColorBench. Any OpenRouter model runs with one command.

```bash
export OPENROUTER_API_KEY=sk-or-...
uv run run.py --model openai/gpt-6.1-sol
```

Only barcodes are shared here, no frames from the videos.
