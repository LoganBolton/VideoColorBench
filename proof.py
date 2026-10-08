"""Render data/<dataset>/proof.html, a page for proofreading every question.

    uv run proof.py [--dataset films|youtube]

Each card shows the color barcode, the source the video came from, and all 10 options with the
correct one marked.
"""

import argparse
import base64
import html
import io
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent


def data_uri(path, size=None):
    img = Image.open(path).convert("RGB")
    if size:
        img = img.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def source_link(src):
    if src.get("archive_id"):
        return f"https://archive.org/details/{src['archive_id']}", f"archive.org/{src['archive_id']}"
    if src.get("youtube_id"):
        return f"https://www.youtube.com/watch?v={src['youtube_id']}", src.get("youtube_title") or src["youtube_id"]
    return None, src.get("local_file", "local file")


CSS = """
/* Layout: one card per question, the color barcode across the top, options below */
:root {
  --bg: #f3f2ef; --panel: #fbfaf8; --fg: #1d1c1a; --muted: #6b6862; --line: #dcd9d3;
  --accent: #b5651d; --accent-soft: #f4e4d2; --ok: #2f7a4a;
  --display: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --body: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #161513; --panel: #1f1d1b; --fg: #ecebe7; --muted: #a29e96; --line: #33302c;
  --accent: #e39a52; --accent-soft: #3a2a1b; --ok: #6cc28c; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #161513; --panel: #1f1d1b; --fg: #ecebe7; --muted: #a29e96; --line: #33302c;
  --accent: #e39a52; --accent-soft: #3a2a1b; --ok: #6cc28c; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font-family: var(--body); font-size: 15px; line-height: 1.45; }
.wrap { max-width: 1120px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 64px; }
header { display: grid; gap: 6px; margin-bottom: 18px; }
h1 { font-family: var(--display); font-weight: 800; font-size: clamp(1.6rem, 4vw, 2.3rem); letter-spacing: -0.02em; margin: 0; text-wrap: balance; }
.lede { color: var(--muted); margin: 0; max-width: 65ch; }
.bar { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 2; background: var(--bg); display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center; padding-block: 10px; border-bottom: 1px solid var(--line); margin-bottom: 18px; }
.bar input[type=search] { flex: 1 1 220px; min-width: 0; font: inherit; padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--fg); }
.bar label { display: flex; gap: 6px; align-items: center; color: var(--muted); font-size: 0.9rem; }
.count { font-family: var(--mono); font-size: 0.85rem; color: var(--muted); }
.list { display: grid; gap: 14px; }
.card { display: grid; gap: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
.barcode { width: 100%; height: 64px; border-radius: 5px; display: block; }
.info { min-width: 0; display: grid; gap: 8px; align-content: start; }
h2 { font-family: var(--display); font-weight: 700; font-size: 1.15rem; margin: 0; text-wrap: balance; }
.meta { display: flex; flex-wrap: wrap; gap: 6px; font-size: 0.8rem; }
.chip { border: 1px solid var(--line); border-radius: 99px; padding: 1px 9px; color: var(--muted); font-family: var(--mono); }
.meta a { color: var(--accent); overflow-wrap: anywhere; }
ol.opts { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 14px; }
@media (max-width: 520px) { ol.opts { grid-template-columns: minmax(0, 1fr); } }
ol.opts li { display: flex; gap: 8px; padding: 3px 6px; border-radius: 5px; min-width: 0; }
ol.opts b { font-family: var(--mono); font-weight: 500; color: var(--muted); }
body.show li.right { background: var(--accent-soft); }
body.show li.right b { color: var(--accent); font-weight: 700; }
body.show li.right::after { content: "answer"; margin-left: auto; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent); align-self: center; }
.card[hidden] { display: none; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 14px; }
.stat { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; }
.stat b { display: block; font-family: var(--display); font-weight: 800; font-size: 1.5rem; letter-spacing: -0.02em; }
.stat span { color: var(--muted); font-size: 0.8rem; }
.pop { display: flex; align-items: center; gap: 10px; font-size: 0.85rem; }
.pop b { font-family: var(--mono); font-weight: 700; color: var(--accent); white-space: nowrap; }
.pop i { flex: 1 1 auto; height: 6px; border-radius: 99px; background: var(--line); overflow: hidden; }
.pop i u { display: block; height: 100%; background: var(--accent); border-radius: 99px; }
.pop em { font-style: normal; color: var(--muted); white-space: nowrap; }
.bar select { font: inherit; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--fg); }
a:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""

JS = """
const q = document.getElementById('q'), show = document.getElementById('show'), cards = [...document.querySelectorAll('.card')], count = document.getElementById('count');
function update() {
  const t = q.value.trim().toLowerCase(); let n = 0;
  for (const c of cards) { const hit = !t || c.dataset.search.includes(t); c.hidden = !hit; n += hit; }
  count.textContent = n + ' of ' + cards.length + ' questions';
  document.body.classList.toggle('show', show.checked);
}
q.addEventListener('input', update); show.addEventListener('change', update); update();
const sort = document.getElementById('sort'), list = document.querySelector('.list');
if (sort) sort.addEventListener('change', () => {
  const key = sort.value;
  [...cards].sort((a, b) => key === 'pop' ? b.dataset.pop - a.dataset.pop : a.dataset.order - b.dataset.order).forEach(c => list.appendChild(c));
});
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["films", "youtube"], default="films")
    args = ap.parse_args()
    data = ROOT / "data" / args.dataset
    colors = {m["slug"]: m for m in json.loads((data / "colors.json").read_text())}
    questions = [json.loads(line) for line in (data / "questions.jsonl").read_text().splitlines() if line.strip()]
    questions.sort(key=lambda q: (colors[q["slug"]].get("year") or 0, q["answer_title"]))

    pop_path = data / "popularity.json"
    pop = json.loads(pop_path.read_text()) if pop_path.exists() else None
    values = pop["values"] if pop else {}
    top = max(values.values()) if values else 1
    rank = {slug: i + 1 for i, slug in enumerate(sorted(values, key=values.get, reverse=True))}

    cards = []
    for order, q in enumerate(questions):
        m = colors[q["slug"]]
        item = data / "items" / q["slug"]
        href, src_text = source_link(m.get("source", {}))
        src = f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{html.escape(src_text)}</a>' if href else html.escape(src_text)
        opts = "".join(
            f'<li class="{"right" if chr(65 + i) == q["answer"] else ""}"><b>{chr(65 + i)}</b><span>{html.escape(c)}</span></li>'
            for i, c in enumerate(q["choices"])
        )
        search = " ".join([q["answer_title"], *q["choices"], q.get("genre") or "", q["hex"]]).lower()
        n = values.get(q["slug"])
        pop_row = (f'<div class="pop"><b>{n:,} {html.escape(pop["measure"])}</b>'
                   f'<i><u style="width:{100 * (n / top) ** 0.5:.1f}%"></u></i><em>#{rank[q["slug"]]} of {len(values)}</em></div>') if n else ""
        cards.append(f"""
<article class="card" data-search="{html.escape(search)}" data-order="{order}" data-pop="{n or 0}">
  <img class="barcode" src="{data_uri(item / 'barcode.png', (1000, 60))}" alt="Color barcode of {html.escape(q['answer_title'])}">
  <div class="info">
    <h2>{html.escape(q['answer_title'])}</h2>
    {pop_row}
    <div class="meta">
      <span class="chip">{q['hex']}</span>
      <span class="chip">{html.escape(q.get('genre') or 'no genre')}</span>
      <span class="chip">colorfulness {q.get('colorfulness')}</span>
      <span class="chip">{m.get('frames_sampled')} frames</span>
      <span>{src}</span>
    </div>
    <ol class="opts">{opts}</ol>
  </div>
</article>""")

    kind = "films" if args.dataset == "films" else "YouTube videos"
    stats = sort = ""
    if values:
        v = sorted(values.values())
        median = (v[(len(v) - 1) // 2] + v[len(v) // 2]) // 2
        big = lambda x: f"{x / 1e9:.1f}B" if x >= 1e9 else f"{x / 1e6:.1f}M" if x >= 1e6 else f"{x / 1e3:.0f}k"
        cells = [(big(median), f"median {pop['measure']}"), (big(sum(v) // len(v)), "mean"), (big(v[-1]), "most popular"),
                 (big(v[0]), "least popular")]
        stats = '<div class="stats">' + "".join(f'<div class="stat"><b>{a}</b><span>{html.escape(b)}</span></div>' for a, b in cells) + "</div>"
        stats += f'<p class="lede">Popularity is {html.escape(pop["measure"])} ({html.escape(pop["source"])}). Bars use a square-root scale.</p>'
        sort = ('<select id="sort" aria-label="Sort"><option value="year">Oldest first</option>'
                '<option value="pop">Most popular first</option></select>')
    page = f"""<title>VideoColorBench {"Films" if args.dataset == "films" else "YouTube"} Proof</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;700;800&family=IBM+Plex+Mono:wght@400;500;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header>
    <h1>VideoColorBench proof sheet</h1>
    <p class="lede">Every question in the {kind} set. Each bar is the video's frames from start to finish, every frame shrunk to its average color. Check that each source is the right video and the 9 wrong options are reasonable alternatives.</p>
  </header>
  {stats}
  <div class="bar">
    <input id="q" type="search" placeholder="Filter by title, genre or hex" aria-label="Filter questions">
    {sort}
    <label><input id="show" type="checkbox" checked> Show answers</label>
    <span class="count" id="count"></span>
  </div>
  <div class="list">{''.join(cards)}</div>
</div>
<script>{JS}</script>
"""
    out = data / "proof.html"
    out.write_text(page)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
