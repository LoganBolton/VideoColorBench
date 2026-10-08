"""Render the short explainer video.

    uv run python video/make_video.py <work dir> [--preview]

<work dir> must hold audio/ (from narrate.py) and frames/ (from frames.py). Writes video/videocolorbench.mp4. --preview writes a few still PNGs to <work dir>/preview instead.
"""

import json
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import extract  # noqa: E402

W, H, FPS = 1920, 1080, 30
GAP, TAIL = 1.2, 2.0  # silence after each line, and extra at the very end
BG, FG, MUT, LINE = (14, 14, 17), (240, 240, 240), (150, 150, 158), (52, 52, 60)
ACCENT, OK = (255, 200, 87), (80, 210, 130)
FONT = "/System/Library/Fonts/Avenir Next.ttc"
WEIGHT = {"bold": 0, "demi": 2, "medium": 5, "regular": 7, "heavy": 8}

WORK = Path(sys.argv[1])
LINES = json.loads((ROOT / "video" / "script.json").read_text())
DUR = json.loads((WORK / "audio" / "durations.json").read_text())


@lru_cache(None)
def font(size, weight="medium"):
    return ImageFont.truetype(FONT, size, index=WEIGHT[weight])


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * clamp(t)) for i in range(3))


def text(d, xy, s, size, weight="medium", fill=FG, anchor="la", alpha=1.0):
    d.text(xy, s, font=font(size, weight), fill=mix(BG, fill, alpha), anchor=anchor)


def header(d, tag, headline):
    text(d, (160, 70), tag, 30, "bold", ACCENT)
    text(d, (160, 112), headline, 58, "bold")


@lru_cache(None)
def barcode(dataset, slug, size):
    return Image.open(ROOT / "data" / dataset / "items" / slug / "barcode.png").convert("RGB").resize(size, Image.NEAREST)


@lru_cache(None)
def stills(slug):
    return sorted((WORK / "frames" / slug).glob("*.jpg"))


@lru_cache(64)
def still(slug, i, box):
    im = Image.open(stills(slug)[i]).convert("RGB")
    k = min(box[0] / im.width, box[1] / im.height)
    im = im.resize((round(im.width * k), round(im.height * k)), Image.BILINEAR)
    out = Image.new("RGB", box, (0, 0, 0))
    out.paste(im, ((box[0] - im.width) // 2, (box[1] - im.height) // 2))
    return out


def marker(d, x, top, bottom):
    d.rectangle([x - 4, top - 10, x + 4, bottom + 10], fill=(0, 0, 0))
    d.rectangle([x - 2, top - 10, x + 2, bottom + 10], fill=(255, 255, 255))
    d.polygon([(x, bottom + 14), (x - 16, bottom + 44), (x + 16, bottom + 44)], fill=(255, 255, 255), outline=(0, 0, 0))


def clock(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def entry(dataset, slug):
    return json.loads((ROOT / "data" / dataset / "items" / slug / "entry.json").read_text())


# ---------------------------------------------------------------- scenes

WALL = ["baby-shark-dance-2016", "all-about-that-bass-2014", "gangnam-style-2012", "phonics-song-with-two-words-2014",
        "dark-horse-2014", "roar-2013", "sorry-2015", "believer-2017"]


@lru_cache(None)
def wall():
    im = Image.new("RGB", (W, H), BG)
    for i, slug in enumerate(WALL):
        im.paste(barcode("youtube", slug, (W, H // len(WALL))), (0, i * (H // len(WALL))))
    return im


def scene_hook(t, dur):
    im = Image.new("RGB", (W, H), BG)
    reveal = round(W * ease(t / 0.8))
    im.paste(wall().crop((0, 0, reveal, H)), (0, 0))
    dim = Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), 0.72 * ease((t - 0.2) / 0.5))
    cut = dur * 0.63
    over = dim.copy()
    d = ImageDraw.Draw(over)
    if t < cut:
        d.text((W / 2, 470), "Can an AI recognise a video", font=font(104, "bold"), fill=FG, anchor="mm")
        d.text((W / 2, 610), "from just its colors?", font=font(104, "bold"), fill=ACCENT, anchor="mm")
        a = ease((t - 0.25) / 0.35) * (1 - ease((t - cut + 0.3) / 0.3))
    else:
        d.text((W / 2, 540), "VideoColorBench", font=font(170, "heavy"), fill=FG, anchor="mm")
        a = ease((t - cut) / 0.3)
    return Image.blend(dim, over, a)


def scene_build(t, dur):
    slug, box, bar = "gangnam-style-2012", (880, 495), (1600, 200)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    header(d, "HOW IT WORKS", "Every moment becomes one stripe of its average color")
    p = clamp((t - 0.4) / (dur - 2.8))  # finish early so the whole barcode stays on screen for a moment
    n = len(stills(slug))
    im.paste(still(slug, min(n - 1, int(p * n)), box), (160, 220))
    code = barcode("youtube", slug, bar)
    x = round(p * (bar[0] - 1))
    color = code.getpixel((x, 0))
    d.line([(1080, 467), (1230, 467)], fill=MUT, width=6)
    d.polygon([(1230, 449), (1262, 467), (1230, 485)], fill=MUT)
    d.rounded_rectangle([1300, 317, 1600, 617], 24, fill=color, outline=LINE, width=3)
    text(d, (1450, 650), "average color", 34, "medium", MUT, "ma")
    text(d, (1450, 696), "#{:02X}{:02X}{:02X}".format(*color), 34, "demi", FG, "ma")
    d.rounded_rectangle([160, 800, 160 + bar[0], 800 + bar[1]], 8, fill=(26, 26, 31))
    if x > 0:
        im.paste(code.crop((0, 0, x, bar[1])), (160, 800))
    marker(d, 160 + x, 800, 800 + bar[1])
    text(d, (160, 748), "PSY - Gangnam Style", 34, "demi", MUT)
    text(d, (1760, 748), "start to finish", 34, "medium", MUT, "ra")
    return im


@lru_cache(None)
def all_barcodes():
    """One tall image holding every barcode with its title, three to a row, YouTube first."""
    items = [(ds, e) for ds in ("youtube", "films") for e in json.loads((ROOT / "data" / ds / "colors.json").read_text())]
    col_w, row_h = 508, 124
    im = Image.new("RGB", (W, -(-len(items) // 3) * row_h), BG)
    d = ImageDraw.Draw(im)
    for i, (ds, e) in enumerate(items):
        x, y = 160 + (i % 3) * 546, (i // 3) * row_h
        im.paste(barcode(ds, e["slug"], (col_w, 72)), (x, y))
        label = e["label"]
        while d.textlength(label, font=font(26, "medium")) > col_w:
            label = label[:-2].rstrip() + "\u2026"
        text(d, (x, y + 78), label, 26, "medium", MUT)
    return im


def scene_all(t, dur):
    top = 210
    sheet = all_barcodes()
    im = Image.new("RGB", (W, H), BG)
    offset = round((sheet.height - (H - top) + 40) * ease((t - 0.3) / (dur - 0.6)))
    im.paste(sheet.crop((0, offset, W, offset + H - top)), (0, top))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, top - 1], fill=BG)
    header(d, "THE DATASET", "200 barcodes: 100 YouTube videos, 100 films")
    return im


def scrub(t, dur, dataset, slug, tag, note, note_color):
    box, bar = (880, 495), (1600, 220)
    e = entry(dataset, slug)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    header(d, tag, e["label"])
    p = ease(clamp((t - 0.3) / (dur - 0.6)) * 0.94 + 0.03)
    n = len(stills(slug))
    im.paste(still(slug, min(n - 1, int(p * n)), box), ((W - box[0]) // 2, 210))
    im.paste(barcode(dataset, slug, bar), (160, 740))
    x = 160 + round(p * (bar[0] - 1))
    marker(d, x, 740, 740 + bar[1])
    seconds = e["frames_sampled"] / e["sample_fps"]
    text(d, (1460, 440), clock(p * seconds), 56, "demi", FG)
    text(d, (1460, 506), "of " + clock(seconds), 34, "medium", MUT)
    text(d, (W / 2, 1030), note, 36, "demi", note_color, "mm", ease((t - 1.0) / 0.5))
    return im


def scene_nosf(t, dur):
    return scrub(t, dur, "films", "nosferatu-1922", "EXAMPLE",
                 "Tinted print: yellow for day, blue for night.", OK)


@lru_cache(None)
def question(dataset, slug):
    for line in (ROOT / "data" / dataset / "questions.jsonl").read_text().splitlines():
        q = json.loads(line)
        if q["slug"] == slug:
            return q


def scene_quiz(t, dur):
    q = question("films", "nosferatu-1922")
    bar = (1600, 200)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    header(d, "THE TASK", "Which movie is this?")
    im.paste(barcode("films", q["slug"], bar), (160, 220))
    picked = ease((t - dur * 0.6) / 0.4)
    for i, choice in enumerate(q["choices"]):
        letter = chr(65 + i)
        x, y = 160 + (i // 5) * 810, 470 + (i % 5) * 88
        a = ease((t - 0.3 - i * 0.07) / 0.3)
        if letter == q["answer"] and picked > 0:
            d.rounded_rectangle([x - 16, y - 8, x + 780, y + 70], 14, fill=mix(BG, (22, 60, 38), picked), outline=mix(BG, OK, picked), width=3)
            text(d, (x + 760, y + 31), "right answer", 30, "bold", OK, "rm", picked)
        text(d, (x, y + 4), letter, 44, "bold", ACCENT, alpha=a)
        text(d, (x + 60, y + 4), choice, 44, "medium", FG, alpha=a)
    for i, chip in enumerate(["barcode + 10 titles", "no web search", "no tools"]):
        a = ease((t - dur * 0.45 - i * 0.5) / 0.3)
        x = 160 + i * 420
        d.rounded_rectangle([x, 950, x + 390, 1020], 35, outline=mix(BG, MUT, a), width=3)
        text(d, (x + 195, 984), chip, 34, "demi", FG, "mm", a)
    return im


BASE, PX = 790, 5.0  # y of 0%, pixels per percentage point
MODELS = [  # name, logo, color, results folders (films, youtube). Same colors as the blog: blue GPT, orange Opus
    ("Opus 5.5", "claude", (224, 120, 44), "20261002-021440-opus-medium-films-raw", "20261001-223955-opus-medium-youtube-raw"),
    ("GPT-6.1 Sol", "openai", (47, 111, 222), "20261002-021127-sol-medium-films-raw", "20261001-223715-sol-medium-youtube-raw"),
    ("Qwen3.5 397B", "qwen", (122, 79, 208), "20261002-064646-qwen3.5-397b-a17b-films-raw", "20261002-073620-qwen3.5-397b-a17b-youtube-raw"),
    ("GLM-5.3 Flash", "zai", (46, 157, 87), "20261002-051152-glm-5.3-flash-films-raw", "20261002-053919-glm-5.3-flash-youtube-raw"),
    ("Qwen3.8 27B", "qwen", (122, 79, 208), "20261002-064649-qwen3.8-27b-films-raw", "20261002-065521-qwen3.8-27b-youtube-raw"),
]


@lru_cache(None)
def logo_mark(name):
    return Image.open(ROOT / "video" / "logos" / f"{name}.png").convert("RGBA").resize((46, 46), Image.LANCZOS)


@lru_cache(None)
def score(dataset, run):
    s = json.loads((ROOT / "results" / dataset / run / "summary.json").read_text())[0]
    return s["correct"], s["n"]


def pct(*pairs):
    return 100 * sum(c for c, _ in pairs) / sum(n for _, n in pairs)


def scene_chart(grow, chance=0.0, closing=0.0):
    """grow is how far each model's bar has risen (0..1)."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    header(d, "RESULTS", "Five models, 200 questions, one API call each")
    for p in range(0, 101, 20):
        y = BASE - p * PX
        d.line([(300, y), (1640, y)], fill=LINE, width=2)
        text(d, (280, y), f"{p}%", 30, "medium", MUT, "rm")
    text(d, (300, BASE - 100 * PX - 34), "accuracy", 30, "demi", MUT, "ls")
    for i, ((name, logo, color, films, youtube), g) in enumerate(zip(MODELS, grow)):
        cx = 300 + 134 + 268 * i
        value = pct(score("films", films), score("youtube", youtube))  # both sets pooled
        x0, top = cx - 80, BASE - value * PX * g
        if BASE - top >= 12:
            d.rounded_rectangle([x0, top, x0 + 160, BASE], 10, fill=color)
            d.rectangle([x0, BASE - 10, x0 + 160, BASE], fill=color)
            text(d, (cx, top - 12), f"{value * g:.1f}%", 44, "bold", FG, "mb")
        tile = (26, 26, 26) if logo == "openai" else color
        d.rounded_rectangle([cx - 36, BASE + 22, cx + 36, BASE + 94], 16, fill=tile, outline=LINE, width=2)
        mark = logo_mark(logo)
        im.paste(mark, (cx - 23, BASE + 35), mark)
        text(d, (cx, BASE + 108), name, 31, "bold", FG, "ma")
    if chance > 0:
        y, end = BASE - 10 * PX, 300 + 1340 * chance
        x = 300
        while x < end:
            d.line([(x, y), (min(x + 22, end), y)], fill=FG, width=5)
            x += 36
        for k, word in enumerate(("random", "chance")):
            text(d, (1656, y - 24 + k * 48), word, 44, "bold", FG, "lm", ease(chance * 2 - 1))
    if closing > 0:
        d.rounded_rectangle([360, 976, 1560, 1056], 40, fill=mix(BG, (30, 30, 36), closing))
        text(d, (W / 2, 1014), "Better than guessing. Far from solved.", 46, "bold", ACCENT, "mm", closing)
    return im


def scene_results(t, dur):
    grow = [ease((t - dur * (0.12 + 0.1 * i)) / (dur * 0.15)) for i in range(len(MODELS))]
    return scene_chart(grow, chance=ease((t - dur * 0.84) / 0.6))


def scene_end(t, dur):
    return scene_chart([1] * len(MODELS), chance=1, closing=ease((t - dur * 0.45) / 0.5))


SCENES = {"hook": scene_hook, "build": scene_build, "all": scene_all, "nosf": scene_nosf, "quiz": scene_quiz,
          "results": scene_results, "end": scene_end}
FADE_IN = {"build", "all", "nosf", "quiz", "results"}  # segments that start a new layout


def timeline():
    out, start = [], 0.0
    for i, line in enumerate(LINES):
        length = DUR[line["id"]] + GAP + line.get("pause", 0) + (TAIL if i == len(LINES) - 1 else 0)
        out.append((line["id"], start, length))
        start += length
    return out, start


def frame_at(segments, now):
    for name, start, length in segments:
        if now < start + length or name == segments[-1][0]:
            im = SCENES[name](now - start, length)
            if name in FADE_IN and now - start < 0.25:
                im = Image.blend(Image.new("RGB", (W, H), BG), im, ease((now - start) / 0.25))
            return im


def write_audio(segments, total, path):
    with wave.open(str(path), "wb") as out:
        for i, (name, start, length) in enumerate(segments):
            with wave.open(str(WORK / "audio" / f"{name}.wav")) as w:
                if i == 0:
                    out.setparams(w.getparams())
                rate, width = w.getframerate(), w.getsampwidth() * w.getnchannels()
                out.writeframes(w.readframes(w.getnframes()))
                out.writeframes(b"\0" * width * (round((start + length) * rate) - out.tell()))


def main():
    segments, total = timeline()
    print(f"{total:.1f}s", flush=True)
    if "--preview" in sys.argv:
        (WORK / "preview").mkdir(exist_ok=True)
        for name, start, length in segments:
            for k, frac in enumerate((0.3, 0.9)):
                frame_at(segments, start + length * frac).resize((960, 540)).save(WORK / "preview" / f"{name}-{k}.png")
        return
    extract.FFMPEG = extract.find_ffmpeg()
    audio = WORK / "narration.wav"
    write_audio(segments, total, audio)
    out = ROOT / "video" / "videocolorbench.mp4"
    ff = subprocess.Popen(
        [extract.FFMPEG, "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-i", str(audio), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE,
    )
    for i in range(round(total * FPS)):
        ff.stdin.write(frame_at(segments, i / FPS).tobytes())
    ff.stdin.close()
    ff.wait()
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
