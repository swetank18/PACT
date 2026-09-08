"""Captions for the product shots, in the same type as the motion scenes.

An overlay is a transparent 1920x1080 page with one chip in the upper left, so
a viewer who joins mid-scene knows which claim the footage is evidence for. They
are drawn in the browser rather than with ffmpeg's text filter because the film
should not change typeface when it cuts to the product.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets" / "overlays"
OUT.mkdir(parents=True, exist_ok=True)
TL = json.loads((ROOT / "script" / "timeline.json").read_text())

# Relative to the repository, not to one machine's home directory — see the
# same note in build_scenes.py.
FONTS = Path(__file__).resolve().parent.parent / "console" / "node_modules" / "@fontsource-variable"
INTER = FONTS / "inter" / "files" / "inter-latin-wght-normal.woff2"
MONO = FONTS / "jetbrains-mono" / "files" / "jetbrains-mono-latin-wght-normal.woff2"

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
@font-face{font-family:"InterV";src:url("file://__INTER__") format("woff2");font-weight:100 900;font-display:block}
@font-face{font-family:"MonoV";src:url("file://__MONO__") format("woff2");font-weight:100 800;font-display:block}
*{margin:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;background:transparent}
.wrap{position:absolute;left:64px;top:56px;display:flex;flex-direction:column;gap:12px;align-items:flex-start}
.chip{
  display:inline-flex;align-items:center;gap:14px;
  background:rgba(8,11,15,.82);border:1px solid rgba(95,211,155,.34);
  box-shadow:0 10px 40px rgba(0,0,0,.45);
  border-radius:12px;padding:14px 22px;backdrop-filter:blur(6px);
}
.dot{width:9px;height:9px;border-radius:50%;background:#5fd39b;box-shadow:0 0 12px #5fd39b}
.act{font-family:"MonoV",monospace;font-size:15px;letter-spacing:.24em;color:#6f7b87;text-transform:uppercase}
.title{font-family:"InterV",sans-serif;font-size:25px;font-weight:600;color:#eef1f4;letter-spacing:-.01em}
.sub{font-family:"MonoV",monospace;font-size:19px;color:#5fd39b}
.sub.plain{color:#94a0ac}
</style></head><body>
<div class="wrap">
  <div class="chip"><span class="dot"></span>
    <span class="title">__TITLE__</span>__SUB__</div>
  <div class="act">__ACT__</div>
</div></body></html>"""


def main() -> None:
    made = 0
    for scene in TL["scenes"]:
        if scene["kind"] != "screen":
            continue
        lines = scene["onscreen"]
        title = lines[0]
        # A second line only when it is a reason code or a mechanism worth
        # reading; three or more are codes, and they are shown as a row.
        if len(lines) == 2:
            sub = f'<span class="sub">{lines[1]}</span>'
        elif len(lines) > 2:
            sub = "".join(f'<span class="sub">{x}</span>' for x in lines[1:])
        else:
            sub = ""
        html = (PAGE.replace("__INTER__", str(INTER)).replace("__MONO__", str(MONO))
                    .replace("__TITLE__", title).replace("__SUB__", sub)
                    .replace("__ACT__", scene["act"]))
        (OUT / f"{scene['id']}.html").write_text(html)
        made += 1
    print(f"{made} overlay pages written to {OUT}")


if __name__ == "__main__":
    main()
