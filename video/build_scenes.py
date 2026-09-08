"""The motion-graphics scenes, generated as HTML from the measured timeline.

Every animation is scheduled against the narration rather than against a guess:
`step(f)` returns the moment at which the voice is `f` of the way through this
scene's line, so a bar fills as the sentence that explains it is spoken. Re-run
`build_audio.py` with a different voice and every cue moves with it.

Written as HTML because the recorder is already a browser: the same Chromium
that films the product films these, so type rendering, colour and motion are
identical either side of a cut.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
OUT = ROOT / "assets" / "scenes"
OUT.mkdir(parents=True, exist_ok=True)
TIMELINE = json.loads((ROOT / "script" / "timeline.json").read_text())

sys.path.insert(0, str(REPO))
from contracts.reason_codes import CHECK_ORDER  # noqa: E402

# Relative to the repository, not to one machine's home directory. This was an
# absolute path, which made README_VIDEO.md's "running these five commands on a
# clean checkout produces the same film" true only on the laptop it was written
# on — everywhere else the fonts silently fall back and the render is a
# different film.
FONTS = REPO / "console" / "node_modules" / "@fontsource-variable"
INTER = FONTS / "inter" / "files" / "inter-latin-wght-normal.woff2"
MONO = FONTS / "jetbrains-mono" / "files" / "jetbrains-mono-latin-wght-normal.woff2"

CSS = """
@font-face { font-family:"InterV"; src:url("file://__INTER__") format("woff2");
             font-weight:100 900; font-display:block; }
@font-face { font-family:"MonoV"; src:url("file://__MONO__") format("woff2");
             font-weight:100 800; font-display:block; }

:root{
  --bg:#06080b; --panel:#0e1319; --line:#1e262f; --ink:#e8ecef; --mute:#8d97a2;
  --dim:#5f6a76; --allow:#5fd39b; --block:#ff7a70; --amber:#f3c375; --brand:#8aa6ff;
  --sans:"InterV",system-ui,sans-serif; --mono:"MonoV",ui-monospace,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1920px;height:1080px;overflow:hidden}
body{
  background:
    radial-gradient(1200px 700px at 14% -10%, rgba(95,211,155,.07), transparent 60%),
    radial-gradient(900px 600px at 92% 110%, rgba(138,166,255,.06), transparent 60%),
    var(--bg);
  color:var(--ink); font-family:var(--sans);
  font-variant-numeric:tabular-nums; -webkit-font-smoothing:antialiased;
}
.frame{position:absolute;inset:0;padding:86px 120px 200px}
.eyebrow{font-family:var(--mono);font-size:19px;letter-spacing:.28em;text-transform:uppercase;
  color:var(--dim)}
.mark{position:absolute;left:120px;bottom:118px;font-family:var(--mono);font-size:18px;
  letter-spacing:.3em;color:#3d4650}
.rule{position:absolute;left:120px;right:120px;top:150px;height:1px;background:var(--line)}

h1{font-size:88px;line-height:1.04;letter-spacing:-.03em;font-weight:600;text-wrap:balance}
h2{font-size:52px;line-height:1.1;letter-spacing:-.02em;font-weight:600}
.lede{font-size:30px;line-height:1.45;color:#c3cbd4;max-width:1180px}
.mono{font-family:var(--mono)}
.small{font-size:22px;color:var(--mute);line-height:1.5}
.ok{color:var(--allow)} .bad{color:var(--block)} .am{color:var(--amber)} .br{color:var(--brand)}

/* Held at their from-state until something starts them: the renderer plays
   them on the frame it starts counting, so recorded timing matches the timeline. */
*{animation-play-state:paused}

/* every element rests visible; motion only carries it in */
@keyframes rise{from{opacity:0;transform:translateY(26px)}to{opacity:1;transform:none}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes wipe{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@keyframes growx{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes pop{0%{opacity:0;transform:scale(.92)}60%{transform:scale(1.02)}100%{opacity:1;transform:scale(1)}}
@keyframes flow{from{offset-distance:0%}to{offset-distance:100%}}
/* Chromium's screencast emits a frame when the compositor commits one, and a
   page holding still commits nothing. A motion scene is mostly holding still —
   between two staggered chips nothing moves for the better part of a second —
   so the recording came out with those gaps compressed to a handful of frames,
   and compose.py, cutting by wall clock, stretched them back out unevenly. The
   measured effect on S04 was severe: cues written for 8.2s-10.7s landed at
   16.3s-23.4s of a 24.4s clip, so a row of ten chips finished arriving as the
   scene ended.

   One off-screen element rotating forever fixes it at the source. A transform
   animation runs on the compositor, so it commits a frame every tick whatever
   else the page is doing, and the recording keeps real time. It is one device
   pixel, outside the frame, and it renders nothing. */
@keyframes tick{to{transform:rotate(360deg)}}
.ticker{position:fixed;top:-8px;left:-8px;width:1px;height:1px;
  animation:tick 1s linear infinite;will-change:transform;opacity:.01}

.a-rise{animation:rise .62s cubic-bezier(.2,.7,.3,1) both}
.a-fade{animation:fade .5s ease both}
.a-wipe{animation:wipe .7s cubic-bezier(.3,.7,.2,1) both}
.a-pop{animation:pop .5s cubic-bezier(.2,.7,.3,1) both}
.a-growx{animation:growx .8s cubic-bezier(.25,.7,.25,1) both;transform-origin:left center}

.card{background:linear-gradient(180deg,rgba(20,26,33,.95),rgba(12,16,21,.95));
  border:1px solid var(--line);border-radius:14px;padding:30px 32px}
.tag{display:inline-block;font-family:var(--mono);font-size:17px;letter-spacing:.16em;
  text-transform:uppercase;padding:7px 14px;border-radius:999px;border:1px solid var(--line);
  color:var(--mute)}
.tag.ok{color:var(--allow);border-color:rgba(95,211,155,.4);background:rgba(95,211,155,.09)}
.tag.bad{color:var(--block);border-color:rgba(255,122,112,.4);background:rgba(255,122,112,.09)}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:26px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:34px}
""".replace("__INTER__", str(INTER)).replace("__MONO__", str(MONO))

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>__ID__</title><style>__CSS__</style></head>
<body><div class="ticker"></div><div class="frame">__BODY__</div>
<div class="mark">PACT</div>
<script>
  // Opened by hand, the scene plays itself once the fonts are in. Under the
  // renderer, __HOLD__ is set and the recorder starts it on a known frame.
  document.fonts.ready.then(() => {
    if (!window.__HOLD__) document.getAnimations().forEach((a) => a.play());
  });
</script>
</body></html>"""


def build(scene: dict) -> str:
    """Return the body HTML for one scene, with every delay tied to the voice."""
    # speech_at is absolute film time; a CSS delay is measured from the start
    # of this scene, so the lead-in is what separates them.
    vo = scene["vo_duration"]
    lead = scene["speech_at"] - scene["start"]

    def at(f: float) -> str:
        """Seconds from scene start at which the voice is `f` through the line.

        `at(0)` is the exception: a scene's first element leads the voice in
        rather than landing with it, so the cut never opens on an empty frame.
        """
        if f <= 0:
            return f"{max(0.12, lead - 0.45):.2f}s"
        return f"{lead + vo * f:.2f}s"

    eyebrow = f'<div class="eyebrow">{scene["act"]}</div><div class="rule"></div>'
    t = scene["template"]

    if t == "cold-open":
        return eyebrow + f"""
<div style="margin-top:104px">
  <h1 class="a-rise" style="animation-delay:{at(.0)};max-width:1360px">
    An agent can pay on your behalf.<br>
    <span class="mute" style="color:var(--mute)">Nothing it carries says how much
    it is <span class="ok">allowed</span> to spend.</span></h1>
  <div style="display:flex;gap:28px;margin-top:96px;align-items:stretch">
    <div class="card a-pop" style="animation-delay:{at(.34)};width:520px">
      <div class="tag ok">PAYMENT</div>
      <div style="font-size:44px;margin-top:20px;font-weight:600" class="ok">AUTHORISED</div>
      <div class="small mono" style="margin-top:12px">rail settled &middot; ₹4,180</div>
    </div>
    <div class="card a-pop" style="animation-delay:{at(.62)};width:520px;
         border-style:dashed;border-color:rgba(255,122,112,.5)">
      <div class="tag bad">AUTHORITY</div>
      <div style="font-size:44px;margin-top:20px;font-weight:600" class="bad">UNKNOWN</div>
      <div class="small mono" style="margin-top:12px">the merchant never asked, and could not</div>
    </div>
  </div>
</div>"""

    if t == "three-failures":
        items = [
            ("The upsell is made blind",
             "Recommend first, find out afterwards. The offer trips a ceiling the merchant could not see."),
            ("A client-side cap is not a control",
             "It holds against the agent's own mistakes. A compromised agent does not run it."),
            ("The gate becomes pure friction",
             "Bolted onto a blind merchant, every authority check can only subtract."),
        ]
        cards = "".join(
            f"""<div class="card a-rise" style="animation-delay:{at(.06 + i * .28)}">
                  <div class="mono" style="font-size:17px;color:var(--block);letter-spacing:.2em">
                    FAILURE {i + 1}</div>
                  <div style="font-size:31px;font-weight:600;margin:18px 0 14px;line-height:1.2">{h}</div>
                  <div class="small">{p}</div></div>"""
            for i, (h, p) in enumerate(items))
        return eyebrow + f"""
<div style="margin-top:96px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">Three things follow from the gap.</h2>
  <div class="grid3" style="margin-top:64px">{cards}</div>
</div>"""

    if t == "identity":
        chips = ["signed mandate", "authority gate", "headroom-aware merchant"]
        c = "".join(
            f'<span class="tag ok a-pop" style="animation-delay:{at(.62 + i * .12)};margin-right:16px">{x}</span>'
            for i, x in enumerate(chips))
        return eyebrow + f"""
<div style="margin-top:150px">
  <div class="a-rise" style="animation-delay:{at(.0)};font-size:150px;font-weight:700;
       letter-spacing:-.05em;line-height:1">PACT<span class="ok">.</span></div>
  <div class="lede a-rise" style="animation-delay:{at(.16)};margin-top:34px;font-size:42px;
       color:var(--ink);max-width:1420px">
    The merchant reads what the buyer is allowed to spend, <em>before</em> it quotes.</div>
  <div style="margin-top:60px">{c}</div>
</div>"""

    if t == "architecture":
        nodes = [
            ("HUMAN", "signs the mandate", "device", "var(--brand)"),
            ("AGENT", "carries it, never the key", "buyer/", "var(--ink)"),
            ("MERCHANT", "reads headroom, then prices", "merchant/", "var(--ink)"),
            ("GATE", "decides on authority alone", "core/gate", "var(--allow)"),
            ("LEDGER", "reserves inside one txn", "core/ledger", "var(--ink)"),
            ("RAIL", "moves the money", "rails/", "var(--mute)"),
        ]
        cells = ""
        for i, (name, sub, path, col) in enumerate(nodes):
            hi = "border-color:rgba(95,211,155,.55);box-shadow:0 0 0 1px rgba(95,211,155,.18)" if name == "GATE" else ""
            cells += f"""<div class="card a-rise" style="animation-delay:{at(.03 + i * .105)};{hi};padding:24px 22px">
              <div class="mono" style="font-size:16px;letter-spacing:.2em;color:{col}">{name}</div>
              <div style="font-size:21px;margin-top:12px;line-height:1.3">{sub}</div>
              <div class="mono" style="font-size:15px;color:var(--dim);margin-top:14px">{path}</div>
            </div>"""
            if i < len(nodes) - 1:
                cells += f"""<div class="a-fade" style="animation-delay:{at(.09 + i * .105)};
                  align-self:center;color:var(--dim);font-size:26px">&rarr;</div>"""
        # Ten chips, revealed fast rather than paced out. A cue lands later on
        # screen than `at()` says and by a factor rather than an offset, because
        # Chromium's screencast stops emitting frames while the page is static —
        # the same behaviour compose.py already compensates for at the tail of a
        # scene — so a slow stagger, which is mostly static gaps, drifts most.
        # At one chip per 0.8s the last two arrived in the final half second of
        # a 24s scene. The whole row now lands inside about three seconds, which
        # is both easier to read and too short for the drift to matter.
        #
        # Read from the gate rather than retyped. The row is headed "THE GATE'S
        # CHECKS, IN ORDER", which is a claim of completeness, and the hand-kept
        # list under it had eight of the ten: mandate_signature was folded into
        # "signature" and mandate_state was simply missing. Derived, it cannot
        # say eight while CHECK_ORDER says ten, and adding a check to the gate
        # puts it on screen.
        checks = [name.replace("_", " ") for name in CHECK_ORDER]
        chips = "".join(
            f'<span class="mono a-fade" style="animation-delay:{at(.34 + i * .012)};font-size:18px;'
            f'padding:8px 14px;border:1px solid var(--line);border-radius:8px;'
            f'color:var(--allow);background:rgba(95,211,155,.06)">{c}</span>'
            for i, c in enumerate(checks))
        return eyebrow + f"""
<div style="margin-top:74px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">One signature. One gate. One envelope.</h2>
  <div style="display:flex;align-items:stretch;gap:14px;margin-top:58px">{cells}</div>
  <div style="margin-top:64px">
    <div class="mono a-fade" style="animation-delay:{at(.30)};font-size:17px;letter-spacing:.22em;
         color:var(--dim);margin-bottom:20px">THE GATE'S CHECKS, IN ORDER</div>
    <div style="display:flex;flex-wrap:wrap;gap:12px;max-width:1560px">{chips}</div>
  </div>
</div>"""

    if t == "envelope":
        yes = ["headroom_paise — what remains", "purchases remaining",
               "per-transaction ceiling", "allowed categories", "expiry"]
        no = ["the delegator's identity", "the intent text",
              "the total budget", "the spend history"]
        col = lambda items, cls, sign, base: "".join(
            f"""<div class="mono a-rise" style="animation-delay:{at(base + i * .07)};font-size:24px;
                 padding:15px 0;border-bottom:1px dashed var(--line)">
                 <span class="{cls}">{sign}</span> {x}</div>"""
            for i, x in enumerate(items))
        return eyebrow + f"""
<div style="margin-top:78px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">The envelope is deliberately thin.</h2>
  <div class="grid2" style="margin-top:52px">
    <div class="card"><div class="tag ok">IN THE ENVELOPE</div>
      <div style="margin-top:18px">{col(yes, "ok", "+", .1)}</div></div>
    <div class="card"><div class="tag bad">NEVER IN IT</div>
      <div style="margin-top:18px">{col(no, "bad", "&minus;", .48)}</div></div>
  </div>
  <div class="a-rise" style="animation-delay:{at(.78)};margin-top:44px;font-size:27px;color:#c3cbd4">
    <span class="ok mono">₹8,900 remaining</span> tells a merchant what it can sell.
    <span class="bad mono">₹8,900 of ₹15,000</span> tells it how rich the buyer is.</div>
</div>"""

    if t == "race":
        bar = lambda pct, cls, delay: (
            f'<div style="height:26px;background:#141a21;border-radius:6px;overflow:hidden">'
            f'<div class="a-growx" style="animation-delay:{delay};width:{pct}%;height:100%;'
            f'background:{cls}"></div></div>')
        return eyebrow + f"""
<div style="margin-top:78px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">Ceilings are reserved, not counted.</h2>
  <div class="mono a-fade" style="animation-delay:{at(.2)};margin-top:26px;font-size:25px;color:var(--allow)">
    SELECT SUM(reserved) &hellip; inside the same BEGIN IMMEDIATE that inserts the row</div>
  <div class="grid2" style="margin-top:56px">
    <div class="card a-rise" style="animation-delay:{at(.42)}">
      <div class="tag ok">RESERVED</div>
      <div style="font-size:40px;font-weight:600;margin:22px 0 8px">5 approved</div>
      <div class="small mono">₹5,000 settled against a ₹5,000 cap</div>
      <div style="margin-top:24px">{bar(100, "linear-gradient(90deg,#3f7f63,#5fd39b)", at(.5))}</div>
      <div class="small mono" style="margin-top:12px">cap held</div>
    </div>
    <div class="card a-rise" style="animation-delay:{at(.6)}">
      <div class="tag bad">COUNTED</div>
      <div style="font-size:40px;font-weight:600;margin:22px 0 8px">20 approved</div>
      <div class="small mono">₹20,000 settled against the same ₹5,000 cap</div>
      <div style="margin-top:24px">{bar(100, "repeating-linear-gradient(45deg,#7a3a36,#7a3a36 8px,#c2554d 8px,#c2554d 16px)", at(.72))}</div>
      <div class="small mono bad" style="margin-top:12px">overspent by ₹15,000</div>
    </div>
  </div>
  <div class="small a-fade" style="animation-delay:{at(.9)};margin-top:34px">
    Both implementations ship. <span class="mono">tests/test_race.py</span> runs them side by side.</div>
</div>"""

    if t == "invariant":
        return eyebrow + f"""
<div style="margin-top:96px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">Authority and settlement never touch.</h2>
  <div style="display:flex;align-items:center;gap:44px;margin-top:70px">
    <div class="card a-rise" style="animation-delay:{at(.2)};flex:1">
      <div class="mono ok" style="font-size:20px;letter-spacing:.2em">core/</div>
      <div style="font-size:30px;margin-top:16px;font-weight:600">decides authority</div>
      <div class="small" style="margin-top:12px">gate, mandate, ledger, audit</div></div>
    <div class="a-pop" style="animation-delay:{at(.46)};text-align:center;min-width:200px">
      <div style="font-size:56px" class="bad">&#10007;</div>
      <div class="mono small" style="margin-top:10px">no import<br>no vendor string</div></div>
    <div class="card a-rise" style="animation-delay:{at(.32)};flex:1">
      <div class="mono" style="font-size:20px;letter-spacing:.2em;color:var(--mute)">rails/</div>
      <div style="font-size:30px;margin-top:16px;font-weight:600">moves the money</div>
      <div class="small" style="margin-top:12px">razorpay, mock_upi</div></div>
  </div>
  <div class="mono a-fade" style="animation-delay:{at(.72)};margin-top:56px;font-size:23px;color:#c3cbd4">
    tests/test_invariants.py &mdash; walks the syntax tree, and a second test
    refuses a vendor name on any executable line.</div>
</div>"""

    if t == "approvable":
        chips = ["USB-C hub", "desk lamp", "notebook A5", "cable 2m"]
        row = "".join(
            f"""<div class="card a-pop" style="animation-delay:{at(.34 + i * .09)};padding:20px 24px;
                 display:flex;align-items:center;gap:18px">
                 <span style="font-size:24px">{c}</span>
                 <span class="tag ok" style="margin-left:auto">ALLOW</span></div>"""
            for i, c in enumerate(chips))
        return eyebrow + f"""
<div style="margin-top:82px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">Every offer is provably approvable.</h2>
  <div class="lede a-rise" style="animation-delay:{at(.14)};margin-top:26px">
    Each add-on the merchant suggests is put through the real gate in the test suite.</div>
  <div style="display:flex;flex-direction:column;gap:16px;margin-top:44px;max-width:900px">{row}</div>
  <div class="a-rise" style="animation-delay:{at(.78)};margin-top:40px;font-size:28px">
    <span class="bad mono">one BLOCK</span> fails the build &mdash; which is what turns a
    spending limit into a <span class="ok">conversion instrument</span>.</div>
</div>"""

    if t == "results":
        arms = [("A", "human checkout only", 51.9, 0, "₹99,512"),
                ("B", "agent, no authority checks", 89.3, 10.7, "₹1,71,184"),
                ("C", "naive client-side cap", 68.6, 3.0, "₹1,31,517"),
                ("D", "PACT", 85.9, 0, "₹1,64,688")]
        rows = ""
        for i, (arm, label, net, loss, val) in enumerate(arms):
            hi = "color:var(--allow)" if arm == "D" else ""
            rows += f"""<div class="a-rise" style="animation-delay:{at(.24 + i * .1)};
              display:grid;grid-template-columns:330px 1fr 200px;gap:26px;align-items:center;
              padding:16px 0;border-bottom:1px solid var(--line)">
              <div><span class="mono" style="font-size:24px;{hi}">ARM {arm}</span>
                   <div class="small" style="margin-top:4px">{label}</div></div>
              <div style="display:flex;height:22px;background:#121821;border-radius:5px;overflow:hidden">
                <div style="width:{net}%;background:linear-gradient(90deg,#3f7f63,#5fd39b)"></div>
                <div style="width:{loss}%;background:repeating-linear-gradient(45deg,#7a3a36,#7a3a36 6px,#a24a44 6px,#a24a44 12px)"></div>
              </div>
              <div class="mono" style="font-size:24px;text-align:right;{hi}">{val}</div></div>"""
        return eyebrow + f"""
<div style="margin-top:70px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">Measured, not asserted.</h2>
  <div class="mono a-fade" style="animation-delay:{at(.12)};margin-top:18px;font-size:21px;color:var(--dim)">
    4 ARMS &middot; 200 SESSIONS EACH &middot; 3 SEEDS &middot; 8% ADVERSARIAL &middot; NET PER 100 SESSIONS</div>
  <div style="margin-top:34px">{rows}</div>
  <div style="display:flex;gap:56px;margin-top:44px">
    <div class="a-rise" style="animation-delay:{at(.78)}">
      <div style="font-size:52px;font-weight:600" class="ok">0.0%</div>
      <div class="small">false block rate &mdash; against arm C's 27.8%</div></div>
    <div class="a-rise" style="animation-delay:{at(.86)}">
      <div style="font-size:52px;font-weight:600" class="ok">₹0</div>
      <div class="small">lost to adversarial traffic, at every rate swept</div></div>
  </div>
</div>"""

    if t == "honesty":
        pts = [("crossover ≈ 18%", "below it, an ungated channel still nets more under this loss model"),
               ("arm A is modelled", "the no-agent baseline assumes 34% completion, stated not buried"),
               ("the live rail is untested", "no test keys existed here; the client runs against a fake built from the API notes")]
        rows = "".join(
            f"""<div class="a-rise" style="animation-delay:{at(.34 + i * .16)};display:flex;gap:26px;
                 padding:22px 0;border-bottom:1px solid var(--line);align-items:baseline">
                 <div class="mono am" style="font-size:26px;min-width:390px">{h}</div>
                 <div class="small" style="font-size:23px">{p}</div></div>"""
            for i, (h, p) in enumerate(pts))
        return eyebrow + f"""
<div style="margin-top:96px">
  <h2 class="a-rise" style="animation-delay:{at(.0)}">And the numbers that do not flatter us.</h2>
  <div style="margin-top:44px;max-width:1560px">{rows}</div>
  <div class="a-fade" style="animation-delay:{at(.88)};margin-top:40px;font-size:26px;color:#c3cbd4">
    All three are in <span class="mono">eval/results/results.md</span>, which the harness generates.</div>
</div>"""

    if t == "outro":
        return eyebrow + f"""
<div style="margin-top:170px">
  <div class="a-rise" style="animation-delay:{at(.0)};font-size:158px;font-weight:700;
       letter-spacing:-.05em;line-height:1">PACT<span class="ok">.</span></div>
  <div class="a-rise" style="animation-delay:{at(.22)};margin-top:34px;font-size:40px;
       max-width:1500px;line-height:1.3">
    A signed delegation the agent carries, a gate that decides on authority alone,
    and a merchant that only ever offers what will be approved.</div>
  <div class="mono a-fade" style="animation-delay:{at(.72)};margin-top:74px;font-size:26px;color:var(--mute)">
    github.com/swetank18/PACT<span style="color:var(--dim)">&nbsp;&nbsp;&middot;&nbsp;&nbsp;</span>pact-9btr.onrender.com</div>
</div>"""

    raise SystemExit(f"unknown template: {t}")


def main() -> None:
    made = []
    for scene in TIMELINE["scenes"]:
        if scene["kind"] != "motion":
            continue
        html = (PAGE.replace("__ID__", scene["id"])
                    .replace("__CSS__", CSS)
                    .replace("__BODY__", build(scene)))
        path = OUT / f"{scene['id']}.html"
        path.write_text(html)
        made.append(f"{scene['id']} ({scene['template']}, {scene['duration']:.1f}s)")
    print(f"{len(made)} motion scenes written to {OUT}")
    for m in made:
        print("  " + m)


if __name__ == "__main__":
    main()
