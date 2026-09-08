#!/usr/bin/env python3
"""
The PACT explainer deck — the whole system, at technical depth.

    python3 scripts/gen_explainer_deck.py

Why this exists alongside the other two decks. `docs/HackSummit-PACT.pptx` is
the submission: seven slides in the organisers' template, capped at ten by their
rulebook. `docs/PACT-pitch.pptx` is the five-minute persuasion. Neither has room
to say *how the thing works* — the ten checks in their order, why a ceiling is
reserved rather than counted, what the envelope deliberately cannot carry, what
the saga does when the refund itself fails.

This deck is that explanation, for someone who has to integrate it, review it,
or decide whether to trust it.

Why a generator rather than a hand-built file. Same reason as
`gen_hacksummit_deck.py`: `PACT-pitch.pptx` was hand-rendered and its generator
was never committed, so a number that moves in `eval/results/results.md` cannot
be carried into it without opening PowerPoint. Every figure below is quoted
next to the file it came from, and regenerating is the whole edit.

Sources, all of them in the tree:

    contracts/reason_codes.py   CHECK_ORDER, the ten checks and their order
    contracts/schemas.py        the Headroom envelope's exact field set
    merchant/saga.py            the state machine and the compensation rules
    eval/results/results.md     the four arms, the sweep, the attack table
    docs/soak.md                two hours of load, and the one failure it found

Fonts. Arial for body and Courier New for code, both present on any laptop this
might be opened on. A missing font on a borrowed machine is a live-demo
failure, and this deck is the backup for one.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "PACT-explainer.pptx"

# ----------------------------------------------------------------- palette ---
#
# The same colours as gen_hacksummit_deck.py, so the three decks read as one
# family. The verdict colours are PACT's own, from docs/user-ui-spec.md: they
# mean something specific on screen and a deck that recolours them teaches the
# reader the wrong key.

NAVY = RGBColor(0x00, 0x1B, 0x35)   # headings
SLATE = RGBColor(0x33, 0x50, 0x6B)  # body
MUTED = RGBColor(0x64, 0x74, 0x8B)  # captions, eyebrows
RULE = RGBColor(0xD8, 0xDF, 0xE8)   # hairlines, card borders
WASH = RGBColor(0xF4, 0xF7, 0xFA)   # card fill
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

ALLOW = RGBColor(0x10, 0x98, 0x81)   # emerald: allowed, healthy, proven
BLOCK = RGBColor(0xEF, 0x44, 0x44)   # crimson: blocked, refused
STEPUP = RGBColor(0xF5, 0x9E, 0x0B)  # amber: step-up, the honest caveat
LINK = RGBColor(0x1D, 0x6F, 0xC4)    # accent blue

BODY = "Arial"
MONO = "Courier New"

# ------------------------------------------------------------------ canvas ---
#
# 13.333in x 7.5in — ordinary 16:9, unlike the Hack Summit template's 20in
# canvas, so type here is quoted at its true size and S is 1.

LEFT = Inches(0.72)
RIGHT = Inches(12.61)
WIDTH = RIGHT - LEFT

EYEBROW_Y = Inches(0.42)
TITLE_Y = Inches(0.72)
HAIRLINE_Y = Inches(1.50)
TOP = Inches(1.72)     # where a slide's content may start
BOTTOM = Inches(6.86)  # and where it must stop
FOOTER_Y = Inches(6.99)


# ----------------------------------------------------------------- helpers ---


def textbox(slide, x, y, w, h, *, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    tf.paragraphs[0].alignment = align
    return tf


def write(tf, lines, *, first=False):
    """
    lines: (text, size_pt, bold, colour, space_before_pt[, font]) tuples.

    The first paragraph of a fresh text frame already exists, so it is reused
    rather than appended — otherwise every block starts with a blank line.
    """
    for i, spec in enumerate(lines):
        text, size, bold, colour, before = spec[:5]
        font = spec[5] if len(spec) > 5 else BODY
        para = tf.paragraphs[0] if (first and i == 0) else tf.add_paragraph()
        para.alignment = tf.paragraphs[0].alignment
        para.space_before = Pt(before)
        run = para.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = colour
        run.font.name = font
    return tf


def card(slide, x, y, w, h, *, fill=WASH, line=RULE, radius=0.045):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    shape.shadow.inherit = False
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    shape.adjustments[0] = radius
    shape.text_frame.text = ""
    return shape


def bar(slide, x, y, w, h, colour, *, radius=0.5):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    shape.shadow.inherit = False
    shape.fill.solid()
    shape.fill.fore_color.rgb = colour
    shape.line.fill.background()
    shape.adjustments[0] = radius
    return shape


def hairline(slide, y, *, colour=RULE):
    bar(slide, LEFT, y, WIDTH, Pt(1), colour, radius=0)


def new_slide(prs, eyebrow, title, number):
    """Every slide opens the same way: eyebrow, title, hairline, page number."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    tf = textbox(slide, LEFT, EYEBROW_Y, WIDTH, Inches(0.24))
    write(tf, [(eyebrow.upper(), 10.5, True, MUTED, 0)], first=True)

    tf = textbox(slide, LEFT, TITLE_Y, WIDTH, Inches(0.60))
    write(tf, [(title, 28, True, NAVY, 0)], first=True)

    hairline(slide, HAIRLINE_Y)

    tf = textbox(slide, LEFT, FOOTER_Y, WIDTH, Inches(0.24), align=PP_ALIGN.RIGHT)
    write(tf, [(f"PACT · {number}", 9.5, False, MUTED, 0)], first=True)
    return slide


def lede(slide, text, *, size=15.5, height=0.52):
    """The one sentence under the title that most slides open with."""
    tf = textbox(slide, LEFT, TOP, WIDTH, Inches(height))
    write(tf, [(text, size, False, SLATE, 0)], first=True)
    return TOP + Inches(height) + Inches(0.16)


def footnote(slide, text, *, colour=MUTED, size=10.5, height=0.52):
    """Anchored to the foot of the safe area, not to whatever ran above it."""
    tf = textbox(slide, LEFT, BOTTOM - Inches(height), WIDTH, Inches(height),
                 anchor=MSO_ANCHOR.BOTTOM)
    write(tf, [(text, size, False, colour, 0)], first=True)


def stat_card(slide, x, y, w, h, value, label, colour):
    card(slide, x, y, w, h, fill=WHITE, line=RULE)
    bar(slide, x + Inches(0.24), y + Inches(0.26), Inches(0.62), Inches(0.06), colour)
    tf = textbox(slide, x + Inches(0.24), y + Inches(0.46), w - Inches(0.48),
                 h - Inches(0.6))
    write(tf, [(value, 23, True, NAVY, 0), (label, 10.5, False, MUTED, 4)], first=True)


def panel(slide, x, y, w, h, heading, rows, *, colour=LINK, fill=WASH, body=11):
    """
    A titled card with a coloured rule and a short list under it.

    rows: strings, or (text, colour) pairs when a line carries a verdict.
    """
    card(slide, x, y, w, h, fill=fill, line=RULE)
    bar(slide, x + Inches(0.26), y + Inches(0.28), Inches(0.62), Inches(0.06), colour)
    tf = textbox(slide, x + Inches(0.26), y + Inches(0.48), w - Inches(0.52),
                 h - Inches(0.66))
    lines = [(heading, 13, True, NAVY, 0)]
    for r in rows:
        text, col = (r, SLATE) if isinstance(r, str) else r
        lines.append((text, body, False, col, 6))
    write(tf, lines, first=True)
    return tf


def columns(n, *, gap=0.24, left=LEFT, width=WIDTH):
    """Left edges and a common width for n evenly gapped columns."""
    g = Inches(gap)
    w = int((width - g * (n - 1)) / n)
    return [left + (w + g) * i for i in range(n)], w


# ------------------------------------------------------------------ slides ---


def s01_title(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    tf = textbox(slide, LEFT, Inches(1.12), WIDTH, Inches(0.3))
    write(tf, [("RAZORPAY  ·  AI GROWTH & AGENTIC COMMERCE", 11.5, True, MUTED, 0)],
          first=True)

    tf = textbox(slide, LEFT, Inches(1.62), WIDTH, Inches(1.15))
    write(tf, [("PACT", 62, True, NAVY, 0)], first=True)

    tf = textbox(slide, LEFT, Inches(2.86), Inches(9.6), Inches(0.95))
    write(tf, [
        ("The merchant reads what the buyer is allowed to spend — before it quotes.",
         21, True, NAVY, 0),
        ("A signed delegation the buyer's agent carries, a gate that decides on "
         "authority alone, and a merchant that only ever offers what will be approved.",
         13.5, False, SLATE, 9),
    ], first=True)

    xs, w = columns(4)
    stats = [
        ("0.0%", "false block rate", ALLOW),
        ("13 / 13", "attack variants blocked", ALLOW),
        ("₹0", "lost to adversarial traffic", ALLOW),
        ("251", "tests, green on every push", LINK),
    ]
    for x, (value, label, colour) in zip(xs, stats):
        stat_card(slide, x, Inches(4.62), w, Inches(1.28), value, label, colour)

    footnote(slide, "Every figure in this deck is generated. Revenue and block rates "
                    "from eval/results/results.md · load and memory from docs/soak.md "
                    "· the checks from contracts/reason_codes.py.")


def s02_gap(prs):
    slide = new_slide(prs, "the gap", "Agentic commerce standardised the payment. "
                                     "Not the authority.", 2)
    y = lede(slide, "Every protocol in this space answers \"how does an agent pay?\". "
                    "None of them lets the merchant read the buyer's authority "
                    "envelope before it makes an offer.")

    # The blind flow, as four beats across the slide.
    xs, w = columns(4, gap=0.2)
    beats = [
        ("Buyer's agent", "carries a mandate the merchant cannot read"),
        ("Merchant", "quotes and upsells against no information"),
        ("Authority", "opaque until the payment is attempted"),
        ("Decline", "found out at the end, after the offer"),
    ]
    for x, (head, sub) in zip(xs, beats):
        card(slide, x, y, w, Inches(1.12), fill=WASH, line=RULE)
        tf = textbox(slide, x + Inches(0.22), y + Inches(0.22), w - Inches(0.44),
                     Inches(0.8))
        write(tf, [(head, 12.5, True, NAVY, 0), (sub, 10, False, MUTED, 4)], first=True)

    tf = textbox(slide, LEFT, y + Inches(1.30), WIDTH, Inches(0.36))
    write(tf, [("The offer is a guess. The merchant learns the answer only when the "
                "payment fails.", 12.5, True, SLATE, 0)], first=True)

    xs, w = columns(3)
    harms = [
        ("Legitimate sales refused", BLOCK,
         ["A hard cap has no way to ask a human.", "It just says no — 27.8% of the",
          "time, in arm C of the simulation."]),
        ("Unauthorised agent spend", BLOCK,
         ["A cap that lives in the agent is not", "run by a compromised agent.",
          "Arm C still lost ₹5,786 per 100."]),
        ("Every cross-sell is a guess", STEPUP,
         ["So merchants do not make one.", "The safest upsell against an",
          "unreadable limit is none at all."]),
    ]
    ry = y + Inches(1.78)
    for x, (head, colour, rows) in zip(xs, harms):
        panel(slide, x, ry, w, Inches(1.86), head, rows, colour=colour, body=10.5)

    footnote(slide, "Arm C is a naive client-side cap with a naive upsell — the "
                    "obvious build. eval/results/results.md")


def s03_fixes(prs):
    slide = new_slide(prs, "why the obvious fixes fail",
                      "Both halves of the usual answer are broken.", 3)
    y = lede(slide, "The two things a team reaches for first — a limit inside the "
                    "agent, and authority checks bolted onto a merchant that still "
                    "cannot see — fail in opposite directions.")

    xs, w = columns(2, gap=0.32)
    panel(slide, xs[0], y, w, Inches(2.42), "A cap inside the agent", [
        "It holds against the agent's own mistakes, and nothing else.",
        "A compromised agent simply does not run it — the control and the "
        "thing being controlled are the same process.",
        ("Arm C: ₹5,786.16 lost per 100 sessions, to attacks its cap was "
         "written to stop.", BLOCK),
    ], colour=BLOCK, body=11)

    panel(slide, xs[1], y, w, Inches(2.42), "Checks bolted onto a blind merchant", [
        "If the merchant cannot read authority before it offers, every check "
        "added afterwards can only subtract.",
        "It refuses sales it already made the customer want, and it never "
        "makes the one it could have.",
        ("Arm C: 27.8% false block rate — more than one refusal in four was "
         "a legitimate buyer.", BLOCK),
    ], colour=BLOCK, body=11)

    ry = y + Inches(2.66)
    card(slide, LEFT, ry, WIDTH, Inches(1.30), fill=WHITE, line=ALLOW)
    bar(slide, LEFT + Inches(0.3), ry + Inches(0.28), Inches(0.62), Inches(0.06), ALLOW)
    tf = textbox(slide, LEFT + Inches(0.3), ry + Inches(0.48), WIDTH - Inches(0.6),
                 Inches(0.74))
    write(tf, [
        ("What is left is the third option: move the control to the server, and "
         "read it forward.", 14, True, NAVY, 0),
        ("Authority is checked where the agent cannot reach it, and it is read "
         "before the offer is built rather than enforced after it is made. "
         "Arm D: 0.0% false block, ₹0 lost.", 11, False, SLATE, 6),
    ], first=True)


def s04_idea(prs):
    slide = new_slide(prs, "the idea", "Read the envelope first. Then make an offer "
                                      "that cannot fail.", 4)
    y = lede(slide, "Five steps. The human signs once, on their own device, and the "
                    "key never leaves it.")

    xs, w = columns(5, gap=0.18)
    steps = [
        ("1", "Human signs", "A mandate signed in the browser. Ed25519, on the "
                             "buyer's own device."),
        ("2", "Agent carries", "The agent receives the signed mandate, and never "
                               "the key."),
        ("3", "Merchant reads", "Headroom before the quote: what is LEFT to spend, "
                                "not what was granted."),
        ("4", "Gate decides", "Ten checks on authority alone. ALLOW · STEP-UP · "
                              "BLOCK."),
        ("5", "Token settles", "A settlement token, redeemable exactly once, "
                               "amount-bound to its quote."),
    ]
    for x, (n, head, sub) in zip(xs, steps):
        card(slide, x, y, w, Inches(2.16), fill=WASH, line=RULE)
        tf = textbox(slide, x + Inches(0.2), y + Inches(0.22), w - Inches(0.4),
                     Inches(1.8))
        write(tf, [
            (n, 22, True, LINK, 0),
            (head, 12.5, True, NAVY, 4),
            (sub, 10, False, SLATE, 6),
        ], first=True)

    ry = y + Inches(2.40)
    card(slide, LEFT, ry, WIDTH, Inches(1.42), fill=WHITE, line=RULE)
    tf = textbox(slide, LEFT + Inches(0.3), ry + Inches(0.24), WIDTH - Inches(0.6),
                 Inches(1.0))
    write(tf, [
        ("The consequence, and the whole commercial argument", 13, True, NAVY, 0),
        ("Because the merchant can see what is left, every offer it makes is one "
         "the gate will approve. The upsell is filtered against remaining authority "
         "before it is ever shown — which turns a spending limit from friction into "
         "a conversion instrument, instead of a thing that refuses customers at the "
         "end of a checkout.", 11, False, SLATE, 6),
    ], first=True)


def s05_architecture(prs):
    slide = new_slide(prs, "architecture", "Three planes, and one rule that keeps "
                                          "them honest.", 5)
    y = lede(slide, "The gate decides authority. A rail moves money. Neither knows "
                    "the other exists.")

    xs, w = columns(3)
    planes = [
        ("TRUST PLANE", "core/", LINK,
         ["Signed mandate (Ed25519 + JCS)", "Ten checks, short-circuiting",
          "Ledger and atomic reservations", "Append-only audit trail",
          "Compensating rollback saga"]),
        ("GROWTH PLANE", "merchant/", ALLOW,
         ["Headroom-aware upsell", "Provably approvable offers",
          "Step-up recovery", "Alternative on stockout",
          "Attach measured, not assumed"]),
        ("MERCHANT PLANE", "merchant/ + console/", MUTED,
         ["Catalog and deterministic quotes", "Checkout and settlement",
          "MCP tools for agents", "Three console surfaces",
          "SSE live decision stream"]),
    ]
    for x, (name, path, colour, rows) in zip(xs, planes):
        card(slide, x, y, w, Inches(2.62), fill=WASH, line=RULE)
        bar(slide, x + Inches(0.26), y + Inches(0.28), Inches(0.62), Inches(0.06), colour)
        tf = textbox(slide, x + Inches(0.26), y + Inches(0.48), w - Inches(0.52),
                     Inches(2.0))
        lines = [(name, 12, True, NAVY, 0), (path, 10.5, False, MUTED, 2, MONO)]
        lines += [(r, 10.5, False, SLATE, 7) for r in rows]
        write(tf, lines, first=True)

    ry = y + Inches(2.86)
    card(slide, LEFT, ry, WIDTH, Inches(1.24), fill=WHITE, line=RULE)
    tf = textbox(slide, LEFT + Inches(0.3), ry + Inches(0.22), WIDTH - Inches(0.6),
                 Inches(0.9))
    write(tf, [
        ("RAIL ADAPTER BOUNDARY   —   Razorpay (test keys only)  ·  mock UPI Circle "
         "(no keys needed)", 12, True, NAVY, 0),
        ("Nothing in core/ imports a rail. tests/test_invariants.py enforces it with "
         "an AST walk, and a second test closes the loophole: no executable line in "
         "core/ may even name a vendor. Swapping the rail is a config change, not a "
         "refactor.", 11, False, SLATE, 6),
    ], first=True)


def s06_mandate(prs):
    slide = new_slide(prs, "the mandate", "A delegation the buyer signs, and the "
                                         "agent cannot forge.", 6)
    y = lede(slide, "The mandate is the root of every decision downstream. It is "
                    "signed in the buyer's browser and verified on the server, byte "
                    "for byte.")

    xs, w = columns(2, gap=0.32)
    panel(slide, xs[0], y, w, Inches(2.56), "How it is signed", [
        "Ed25519, generated and held in the buyer's own browser.",
        "RFC 8785 JCS canonicalisation, so the bytes the browser signs and the "
        "bytes the server verifies are identical — no field ordering or "
        "whitespace ambiguity.",
        "The console verifies browser/server signature parity at boot and shows "
        "it in the header, so a mismatch is visible before a demo, not during one.",
        ("The agent receives the signed mandate. It never receives the key.", ALLOW),
    ], colour=LINK, body=10.5)

    panel(slide, xs[1], y, w, Inches(2.56), "What it grants", [
        "A total budget, in paise.",
        "A per-transaction ceiling.",
        "A count of purchases allowed.",
        "A merchant allowlist and a category allowlist.",
        "A validity window, and a revocation state the buyer controls.",
        ("Everything the gate later decides is derived from these fields and the "
         "ledger — never from anything the agent asserts.", NAVY),
    ], colour=ALLOW, body=10.5)

    footnote(slide, "contracts/crypto.py · console signs with @noble/ed25519 · "
                    "the demo's grant screen is step 1 of the walkthrough.")


def s07_envelope(prs):
    slide = new_slide(prs, "the headroom envelope",
                      "What the merchant is told, and what it is never told.", 7)
    y = lede(slide, "The envelope is deliberately thin. It answers one question — "
                    "how much authority is left — and it is structurally incapable "
                    "of answering any other.")

    xs, w = columns(2, gap=0.32)
    panel(slide, xs[0], y, w, Inches(2.72), "Disclosed", [
        "headroom_paise — what is LEFT",
        "max_per_txn_paise",
        "payments_remaining",
        "categories_allowed",
        "merchant_in_scope",
        "valid_until · as_of · mandate_id",
    ], colour=ALLOW, body=11)

    panel(slide, xs[1], y, w, Inches(2.72), "Absent, and must stay absent", [
        "the delegator's identity",
        "the intent text",
        "the total budget",
        "the spend history",
        "",
        "test_headroom.py asserts the field set itself — not one instance's "
        "values — so a field added by accident fails the build.",
    ], colour=BLOCK, body=11)

    ry = y + Inches(2.96)
    card(slide, LEFT, ry, WIDTH, Inches(1.10), fill=WHITE, line=RULE)
    tf = textbox(slide, LEFT + Inches(0.3), ry + Inches(0.22), WIDTH - Inches(0.6),
                 Inches(0.8))
    write(tf, [
        ("Why \"what is left\" and not \"what was granted\"", 12.5, True, NAVY, 0),
        ("A merchant told \"₹8,900 remaining\" learns what it can sell. A merchant "
         "told \"₹8,900 of ₹15,000\" learns how rich the buyer is. The first is "
         "commercially useful; the second is a privacy leak with no upside, so the "
         "schema cannot express it.", 11, False, SLATE, 6),
    ], first=True)


def s08_gate(prs):
    slide = new_slide(prs, "the gate", "Ten checks, cheapest and most certain "
                                      "first.", 8)
    y = lede(slide, "Short-circuiting, each one timed, each a plain function of a "
                    "context. The order is frozen in contracts/reason_codes.py and "
                    "it is the design, not an implementation detail.")

    checks = [
        ("1", "request_signature", "Ed25519 by the delegate pubkey"),
        ("2", "mandate_signature", "verified at registration, re-asserted here"),
        ("3", "mandate_state", "exists, and not revoked"),
        ("4", "validity_window", "inside the window the buyer granted"),
        ("5", "freshness", "within 60s of clock skew"),
        ("6", "replay", "nonce INSERT — the uniqueness violation IS the replay"),
        ("7", "scope", "merchant allowlist and category allowlist"),
        ("8", "ceiling", "atomic reservation, not a count"),
        ("8b", "quote_binding", "the amount must equal the quote it references"),
        ("9", "intent", "the model auditor — last, because it is the only "
                        "network call"),
    ]
    xs, w = columns(2, gap=0.32)
    rows_per = 5
    for col, x in enumerate(xs):
        for i, (n, name, what) in enumerate(checks[col * rows_per:(col + 1) * rows_per]):
            ry = y + Inches(0.74) * i
            card(slide, x, ry, w, Inches(0.64), fill=WASH if i % 2 == 0 else WHITE,
                 line=RULE, radius=0.08)
            tf = textbox(slide, x + Inches(0.20), ry + Inches(0.15), Inches(0.42),
                         Inches(0.34))
            write(tf, [(n, 11.5, True, LINK, 0)], first=True)
            tf = textbox(slide, x + Inches(0.66), ry + Inches(0.12), w - Inches(0.86),
                         Inches(0.46))
            write(tf, [(name, 11, True, NAVY, 0, MONO),
                       (what, 9.5, False, MUTED, 1)], first=True)

    footnote(slide, "Ten, in CHECK_ORDER, and the firewall drawer counts the same "
                    "ten on screen. checks.py used to number quote_binding 8b and "
                    "call them nine; the convention lost. Cheapest first means a "
                    "forged signature costs one verify, not a network round trip.")


def s09_verdicts(prs):
    slide = new_slide(prs, "verdicts", "Fail closed, and refuse with a code.", 9)
    y = lede(slide, "Three verdicts, and a default that is not a special case: "
                    "anything not explicitly allowed to ask a human, blocks.")

    xs, w = columns(3)
    verdicts = [
        ("ALLOW", ALLOW, ["All ten checks passed.", "A settlement token is issued, "
                          "bound to the quote's amount and redeemable exactly once."]),
        ("STEP_UP", STEPUP, ["Exactly two codes: INTENT_MISMATCH and "
                             "AUDITOR_UNAVAILABLE.",
                             "A probabilistic signal must never block a legitimate "
                             "sale outright — it asks the human instead."]),
        ("BLOCK", BLOCK, ["Everything else, including anything unexpected.",
                          "Fail closed is the default path, not an error handler "
                          "bolted on the side."]),
    ]
    for x, (name, colour, rows) in zip(xs, verdicts):
        panel(slide, x, y, w, Inches(2.08), name, rows, colour=colour, body=10.5)

    ry = y + Inches(2.32)
    tf = textbox(slide, LEFT, ry, WIDTH, Inches(0.34))
    write(tf, [("Refusals are typed, so an agent can act on them rather than parse "
                "prose:", 12.5, True, NAVY, 0)], first=True)

    codes = [
        "CEILING_PER_TXN", "CEILING_TOTAL", "CEILING_COUNT", "NONCE_REPLAY",
        "SCOPE_MERCHANT_NOT_ALLOWED", "SCOPE_CATEGORY_MISMATCH",
        "QUOTE_AMOUNT_MISMATCH", "REQUEST_SIG_INVALID", "MANDATE_EXPIRED",
        "MANDATE_REVOKED", "INTENT_INJECTION_SUSPECTED", "TOKEN_ALREADY_USED",
    ]
    cy = ry + Inches(0.46)
    xs, w = columns(4, gap=0.16)
    for i, code in enumerate(codes):
        x = xs[i % 4]
        yy = cy + Inches(0.42) * (i // 4)
        card(slide, x, yy, w, Inches(0.34), fill=WHITE, line=RULE, radius=0.12)
        tf = textbox(slide, x + Inches(0.14), yy + Inches(0.07), w - Inches(0.28),
                     Inches(0.24))
        write(tf, [(code, 9, False, SLATE, 0, MONO)], first=True)


def s10_ledger(prs):
    slide = new_slide(prs, "the ledger", "A ceiling is reserved, not counted.", 10)
    y = lede(slide, "The difference sounds like a detail and is the whole "
                    "correctness argument. Counting spend and then writing the row "
                    "is a race; reserving inside the write is not.")

    xs, w = columns(2, gap=0.32)
    panel(slide, xs[0], y, w, Inches(2.30), "Naive — count, then write", [
        "Read the spend so far.",
        "Compare it against the ceiling.",
        "Write the new row.",
        ("Two concurrent requests both read the old total, both pass, and both "
         "write. The cap is exceeded and nothing in the code is wrong on its own "
         "line.", BLOCK),
    ], colour=BLOCK, body=10.5)

    panel(slide, xs[1], y, w, Inches(2.30), "PACT — reserve inside the write", [
        "BEGIN IMMEDIATE takes the write lock up front.",
        "The reservation and the ceiling test are the same transaction.",
        "SQLite in WAL mode; a contended write is a 503 the caller can retry.",
        ("atk_03 concurrent slicing: BLOCK CEILING_TOTAL. The cap is never "
         "exceeded, under load, in the test suite.", ALLOW),
    ], colour=ALLOW, body=10.5)

    ry = y + Inches(2.54)
    card(slide, LEFT, ry, WIDTH, Inches(1.24), fill=WHITE, line=RULE)
    tf = textbox(slide, LEFT + Inches(0.3), ry + Inches(0.22), WIDTH - Inches(0.6),
                 Inches(0.9))
    write(tf, [
        ("The comparison is kept in the tree, not just described", 12.5, True, NAVY, 0),
        ("Both implementations ship, and a test drives them concurrently: the naive "
         "one exceeds the ceiling and the atomic one does not. It is cheaper to "
         "keep the wrong version and prove it wrong than to ask a reviewer to take "
         "the right one on trust. Two hours of soak settled 49,501 orders whose "
         "total agrees with the harness to the paise.", 11, False, SLATE, 6),
    ], first=True)


def s11_saga(prs):
    slide = new_slide(prs, "when it goes wrong",
                      "The failure path is the product, too.", 11)
    y = lede(slide, "Payment captured, then the warehouse says the item is gone. "
                    "Every forward step has exactly one compensating action, and "
                    "compensations run in reverse.")

    states = ["PAYMENT_CAPTURED", "ROLLING_BACK", "REFUND_ISSUED",
              "BUDGET_RELEASED", "ROLLED_BACK", "RECOVERED"]
    xs, w = columns(6, gap=0.14)
    for i, (x, st) in enumerate(zip(xs, states)):
        colour = ALLOW if st == "RECOVERED" else (STEPUP if i else LINK)
        card(slide, x, y, w, Inches(0.72), fill=WASH, line=RULE, radius=0.1)
        bar(slide, x + Inches(0.16), y + Inches(0.16), Inches(0.34), Inches(0.05), colour)
        tf = textbox(slide, x + Inches(0.16), y + Inches(0.32), w - Inches(0.32),
                     Inches(0.36))
        write(tf, [(st, 8.5, True, NAVY, 0, MONO)], first=True)

    ry = y + Inches(0.96)
    xs, w = columns(2, gap=0.32)
    panel(slide, xs[0], ry, w, Inches(2.52), "The budget is released, not just refunded", [
        "A refund that returns the money but leaves the reservation standing has "
        "quietly spent the buyer's authority on nothing.",
        "So the compensation releases the ledger reservation as its own persisted, "
        "idempotent step.",
    ], colour=ALLOW, body=10.5)

    panel(slide, xs[1], ry, w, Inches(2.52), "And when the refund itself fails", [
        "Three retries with backoff, and then the order parks in "
        "NEEDS_ATTENTION and is raised in the console.",
        ("A failed refund is never silently swallowed. Answering \"what if the "
         "compensation fails\" is worth more than the happy path working.", NAVY),
    ], colour=BLOCK, body=10.5)

    footnote(slide, "RECOVERED is the growth claim: after a rollback the merchant "
                    "offers the nearest in-stock alternative inside remaining "
                    "headroom, and the buyer signs for it. A failure becomes revenue "
                    "instead of a loss. merchant/saga.py")


def s12_experiment(prs):
    slide = new_slide(prs, "the experiment", "Four arms, 200 sessions each, three "
                                            "seeds.", 12)
    y = lede(slide, "A single run of a stochastic agent is not a result. The "
                    "comparison that matters is C against D — both fully simulated, "
                    "8% of sessions adversarial in every arm.")

    rows = [
        ("Arm", "Configuration", "GMV / 100", "False block", "Losses / 100",
         "Net / 100"),
        ("A", "no agent channel (modelled)", "₹99,511.96", "0.0%", "₹0",
         "₹99,511.96"),
        ("B", "agent transactable, no checks", "₹1,91,614.30", "0.0%",
         "₹20,430.59", "₹1,71,183.71"),
        ("C", "naive client-side cap", "₹1,37,303.21", "27.8%", "₹5,786.16",
         "₹1,31,517.05"),
        ("D", "PACT: gate, headroom upsell", "₹1,64,688.24", "0.0%", "₹0",
         "₹1,64,688.24"),
    ]
    widths = [0.9, 3.7, 1.9, 1.5, 1.85, 2.04]
    table_h = Inches(0.46) * len(rows)
    shape = slide.shapes.add_table(len(rows), len(widths), LEFT, y, WIDTH, table_h)
    table = shape.table
    for i, cw in enumerate(widths):
        table.columns[i].width = Inches(cw)

    for r, row in enumerate(rows):
        table.rows[r].height = Inches(0.46)
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.margin_left = Inches(0.12)
            cell.margin_right = Inches(0.12)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = NAVY
            elif rows[r][0] == "D":
                cell.fill.fore_color.rgb = RGBColor(0xE9, 0xF7, 0xF2)
            else:
                cell.fill.fore_color.rgb = WHITE if r % 2 else WASH
            tf = cell.text_frame
            tf.word_wrap = True
            para = tf.paragraphs[0]
            para.alignment = PP_ALIGN.LEFT if c < 2 else PP_ALIGN.RIGHT
            run = para.add_run()
            run.text = val
            run.font.size = Pt(11)
            run.font.name = BODY
            run.font.bold = (r == 0) or (c == 0) or (rows[r][0] == "D" and c >= 2)
            if r == 0:
                run.font.color.rgb = WHITE
            elif rows[r][0] == "C" and c == 3:
                run.font.color.rgb = BLOCK
            elif rows[r][0] == "D" and c in (3, 4):
                run.font.color.rgb = ALLOW
            else:
                run.font.color.rgb = NAVY if c == 0 else SLATE

    ry = y + table_h + Inches(0.26)
    card(slide, LEFT, ry, WIDTH, Inches(1.22), fill=WHITE, line=RULE)
    tf = textbox(slide, LEFT + Inches(0.3), ry + Inches(0.22), WIDTH - Inches(0.6),
                 Inches(0.88))
    write(tf, [
        ("Arm C is the row worth pausing on", 12.5, True, NAVY, 0),
        ("Its cap is real and it works — against the agent's own mistakes. But it "
         "lives in the agent, so a compromised agent does not run it, and it still "
         "lost ₹5,786 per 100 sessions while refusing 27.8% of legitimate buyers. A "
         "client-side control is not a control, and that difference has nothing to "
         "do with revenue.", 11, False, SLATE, 6),
    ], first=True)


def s13_honest(prs):
    slide = new_slide(prs, "the honest number",
                      "Where the gate starts paying for itself.", 13)
    y = lede(slide, "Arm B converts more than arm D, because nothing stops it — "
                    "including the things that should. So sweep the assumption "
                    "rather than pick one that flatters us.")

    rows = [("Adversarial rate", "B net / 100", "D net / 100", "Better"),
            ("0%", "₹3,18,214.16", "₹2,86,403.92", "B"),
            ("10%", "₹3,03,574.88", "₹2,86,403.92", "B"),
            ("20%", "₹2,81,976.68", "₹2,86,403.92", "D"),
            ("35%", "₹2,58,058.84", "₹2,86,403.92", "D"),
            ("50%", "₹2,36,099.92", "₹2,86,403.92", "D")]

    xs, w = columns(2, gap=0.36)
    tw = w
    table_h = Inches(0.52) * len(rows)
    shape = slide.shapes.add_table(len(rows), 4, xs[0], y, tw, table_h)
    table = shape.table
    for i, cw in enumerate([1.7, 1.6, 1.6, 0.94]):
        table.columns[i].width = Inches(cw)
    for r, row in enumerate(rows):
        table.rows[r].height = Inches(0.52)
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.1)
            cell.margin_top = cell.margin_bottom = Inches(0.02)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = (
                NAVY if r == 0 else (WHITE if r % 2 else WASH))
            para = cell.text_frame.paragraphs[0]
            para.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.RIGHT
            run = para.add_run()
            run.text = val
            run.font.size = Pt(10.5)
            run.font.name = BODY
            run.font.bold = (r == 0) or c == 3
            if r == 0:
                run.font.color.rgb = WHITE
            elif c == 3:
                run.font.color.rgb = ALLOW if val == "D" else MUTED
            else:
                run.font.color.rgb = SLATE

    panel(slide, xs[1], y, w, Inches(3.12), "What we will not claim", [
        "Above roughly 20% adversarial traffic, PACT nets more than an ungated "
        "agent channel.",
        "Below it, an ungated channel nets more, and we are not going to claim a "
        "revenue win the measurement does not support.",
        ("Below that line the argument is the 0.0% false block rate against a "
         "cap's 27.8%, the cost of handling disputes, and whether a merchant "
         "wants to be known for accepting unauthorised agent payments.", NAVY),
    ], colour=STEPUP, body=10.5)

    footnote(slide, "The 8% adversarial rate used in the main table is an "
                    "assumption, stated rather than buried, and swept across six "
                    "points here. --hostile-rate reruns the whole thing with "
                    "another one.")


def s14_adversarial(prs):
    slide = new_slide(prs, "adversarial", "Thirteen of thirteen applicable variants "
                                         "blocked.", 14)
    y = lede(slide, "Eight attack families, each run against the live gate and each "
                    "asserting a specific reason code — not merely that something "
                    "was refused.")

    families = [
        ("Prompt injection", "INTENT_INJECTION_SUSPECTED",
         "verification deposit · role reassignment"),
        ("Replay", "NONCE_REPLAY", "a valid captured request, resubmitted"),
        ("Slicing", "CEILING_COUNT · CEILING_TOTAL", "sequential and concurrent"),
        ("Lookalike payee", "SCOPE_MERCHANT_NOT_ALLOWED",
         "razorpayy · xrazorpay · razorpay.com"),
        ("Price hallucination", "QUOTE_AMOUNT_MISMATCH",
         "inflated · deflated · one paisa off"),
        ("Forged signature", "REQUEST_SIG_INVALID", "wrong delegate key"),
        ("Expired mandate", "MANDATE_EXPIRED", "outside the validity window"),
        ("Auditor self-injection", "INTENT_INJECTION_SUSPECTED",
         "reported N/A — see below"),
    ]
    xs, w = columns(2, gap=0.32)
    for i, (name, code, detail) in enumerate(families):
        x = xs[i % 2]
        ry = y + Inches(0.78) * (i // 2)
        card(slide, x, ry, w, Inches(0.66), fill=WASH if i % 4 < 2 else WHITE,
             line=RULE, radius=0.1)
        bar(slide, x + Inches(0.18), ry + Inches(0.28), Inches(0.1), Inches(0.1),
            STEPUP if "N/A" in detail else BLOCK, radius=0.5)
        tf = textbox(slide, x + Inches(0.42), ry + Inches(0.13), Inches(2.3),
                     Inches(0.44))
        write(tf, [(name, 10.5, True, NAVY, 0), (detail, 8.5, False, MUTED, 1)],
              first=True)
        tf = textbox(slide, x + Inches(2.8), ry + Inches(0.24), w - Inches(3.0),
                     Inches(0.28))
        write(tf, [(code, 8.5, False, SLATE, 0, MONO)], first=True)

    footnote(slide, "Two auditor self-injection variants report N/A rather than as a "
                    "pass: the deterministic scan caught the string, but the attack "
                    "targets a model auditor that was not running in this "
                    "configuration. Counting them as wins would overstate the "
                    "result. eval/results/results.md")


def s15_verified(prs):
    slide = new_slide(prs, "what is verified", "Measured, not asserted.", 15)
    y = lede(slide, "Two hours of steady load against one instance, watching the "
                    "shape of the process rather than its speed — plus the suite "
                    "that runs on every push.")

    xs, w = columns(4)
    stats = [
        ("251", "tests — 196 pytest, 55 vitest", LINK),
        ("49,501", "purchases in two hours, 6.9/s", ALLOW),
        ("0", "transport or server errors", ALLOW),
        ("1.02×", "p50 latency drift, 66→67 ms", ALLOW),
    ]
    for x, (v, l, c) in zip(xs, stats):
        stat_card(slide, x, y, w, Inches(1.20), v, l, c)

    ry = y + Inches(1.44)
    xs, w = columns(2, gap=0.32)
    panel(slide, xs[0], ry, w, Inches(2.24), "What held", [
        "593,961 SSE frames to one subscriber held open for two hours, with zero "
        "reconnects.",
        "File descriptors ended lower than they started. Threads flat at 27.",
        ("The ledger's 49,501 orders total ₹4,86,50,572.82 — agreeing with what "
         "the harness settled, to the paise.", ALLOW),
    ], colour=ALLOW, body=10.5)

    panel(slide, xs[1], ry, w, Inches(2.24), "What did not, and was fixed", [
        "RSS climbed +21 MB/hour and did not flatten — a 512 MB machine reached "
        "in about 17 hours. A burst test cannot see this.",
        "Cause: the simulated rail was keeping every intent it ever created.",
        ("Fixed and confirmed on a run that crosses the fix while it is running: "
         "620 bytes per purchase before, 7 after.", ALLOW),
    ], colour=STEPUP, body=10.5)

    footnote(slide, "The soak table in docs/soak.md is left as it was measured on "
                    "the day. It is the run that found the problem, and rewriting it "
                    "would delete the evidence.")


def s16_boundary(prs):
    slide = new_slide(prs, "the honest boundary", "What this does not yet do.", 16)
    y = lede(slide, "Stated here rather than discovered by a reviewer. None of it "
                    "is hidden in a footnote elsewhere.")

    xs, w = columns(2, gap=0.32)
    panel(slide, xs[0], y, w, Inches(2.86), "Not proven", [
        "The Razorpay client has never run against the live API — no test keys "
        "existed in the build environment. The mock UPI rail is what every "
        "number here was measured on.",
        "Arm A is modelled, not simulated: a 34% completion and 11% addon rate, "
        "stated as assumptions rather than buried.",
        "Two attack variants target a model auditor that was not running.",
    ], colour=STEPUP, body=10.5)

    panel(slide, xs[1], y, w, Inches(2.86), "Known limits", [
        "The 8% adversarial rate is an assumption. Below roughly 20%, an ungated "
        "channel still nets more under this loss model.",
        "The app needs a persistent process — SQLite in WAL mode and a held-open "
        "SSE stream — so it is not serverless-deployable as it stands.",
        "One instance, one volume. Horizontal scale would need the ledger moved "
        "off SQLite; nothing in the design prevents it, but nothing has "
        "measured it either.",
    ], colour=STEPUP, body=10.5)

    footnote(slide, "A spending control that overstates what it has proven is the "
                    "one thing this system cannot afford to be.")


def s17_close(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    tf = textbox(slide, LEFT, Inches(1.90), WIDTH, Inches(1.0))
    write(tf, [("PACT", 54, True, NAVY, 0)], first=True)

    tf = textbox(slide, LEFT, Inches(3.00), Inches(10.6), Inches(1.4))
    write(tf, [
        ("A signed delegation the agent carries, a gate that decides on authority "
         "alone, and a merchant that only ever offers what will be approved.",
         20, True, NAVY, 0),
        ("Agentic commerce standardised how an agent pays. This is the part that "
         "says what it is allowed to spend.", 13.5, False, SLATE, 10),
    ], first=True)

    hairline(slide, Inches(4.86))

    tf = textbox(slide, LEFT, Inches(5.10), WIDTH, Inches(0.7))
    write(tf, [
        ("github.com/swetank18/PACT     ·     pact-9btr.onrender.com", 13, False,
         LINK, 0, MONO),
        ("docs/RUNBOOK.md for the live demo · eval/README.md for the simulation · "
         "docs/soak.md for the load run", 10.5, False, MUTED, 8),
    ], first=True)


# -------------------------------------------------------------------- main ---

BUILDERS = [
    s01_title, s02_gap, s03_fixes, s04_idea, s05_architecture, s06_mandate,
    s07_envelope, s08_gate, s09_verdicts, s10_ledger, s11_saga, s12_experiment,
    s13_honest, s14_adversarial, s15_verified, s16_boundary, s17_close,
]


def main() -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    for build in BUILDERS:
        build(prs)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(OUT)
    print(f"wrote {OUT.relative_to(REPO)}  ({len(prs.slides._sldIdLst)} slides)")


if __name__ == "__main__":
    main()
