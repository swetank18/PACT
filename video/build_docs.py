"""Writes the script and the scene plan from the timeline that was actually cut.

Every timestamp in the delivered documents is the one the film uses, because
both are generated from `script/timeline.json` after the voice-over has been
measured. A script whose timings are typed by hand starts drifting from the
video on the first re-record; this one cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TL = json.loads((ROOT / "script" / "timeline.json").read_text())

#: Per-scene direction. Narration, on-screen text and timing come from the
#: timeline; this is everything the timeline does not know.
NOTES: dict[str, dict[str, str]] = {
    "S01": {
        "visual": "Cold open. Statement type on the dark ground, then two cards land: a green PAYMENT · AUTHORISED beside a dashed red AUTHORITY · UNKNOWN.",
        "action": "Motion graphics. Cards arrive on the words 'pays' and 'allowed to spend'.",
        "camera": "Static. The type does the work; no push-in.",
        "transition": "Hard cut in from black.",
        "audio": "Room tone establishes under the first line.",
        "purpose": "State the gap in one frame: settlement is solved, authority is not.",
    },
    "S02": {
        "visual": "Three cards, each a named failure mode, arriving in sequence.",
        "action": "Cards rise as each is named in the narration.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Make the problem concrete and specific before any product appears.",
    },
    "S03": {
        "visual": "Product identity: the wordmark, the one-line value proposition, three capability chips.",
        "action": "Wordmark, then tagline, then chips.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Name the product and its claim in a single frame a viewer can screenshot.",
    },
    "S04": {
        "visual": "The architecture: six nodes from the signing device to the settlement rail, the gate emphasised, then all ten of its checks as a row of chips, read from CHECK_ORDER.",
        "action": "Nodes arrive left to right in narration order; check chips fill in as they are listed.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Give the viewer the mental model the rest of the film assumes.",
    },
    "S05": {
        "visual": "Two columns — what the headroom envelope carries, and what it must never carry — closing on the ₹8,900 contrast.",
        "action": "Fields arrive per column, then the contrast line.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Show that the privacy boundary is a design decision, not an omission.",
    },
    "S06": {
        "visual": "Two ledgers side by side. The reserved one holds its cap; the counted one overruns with a hatched red bar.",
        "action": "Bars fill on the sentence that names each number.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Prove the concurrency claim with the repository's own comparison rather than an assertion.",
    },
    "S07": {
        "visual": "core/ and rails/ as two blocks with a crossed connection between them.",
        "action": "Blocks, then the refusal glyph, then the test that enforces it.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Explain why the gate stays portable across settlement rails.",
    },
    "S08": {
        "visual": "Four suggested add-ons, each stamped ALLOW by the real gate, then the line that turns the limit into a conversion instrument.",
        "action": "Chips stamp in sequence.",
        "camera": "Static.",
        "transition": "Cut to the product.",
        "audio": "Narration only.",
        "purpose": "Land the commercial argument immediately before the live demo shows it happening.",
    },
    "S09": {
        "visual": "The running console. The grant surface, a real Ed25519 signature, the mandate chip, then intent typed into the checkout composer.",
        "action": "Pointer moves to Grant and sign; the mandate appears; the buyer's intent is typed a character at a time and sent.",
        "camera": "Full-frame UI at 1:1. Caption chip upper left.",
        "transition": "Cut from motion graphics to product.",
        "audio": "Narration only.",
        "purpose": "Show the signature happening on the device, not asserted in a diagram.",
    },
    "S10": {
        "visual": "An order, a gate decision, and the check list rendering in order.",
        "action": "Beat 1 is triggered; the board fills.",
        "camera": "Full-frame UI.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Establish the happy path end to end before any contrast is drawn.",
    },
    "S11": {
        "visual": "An add-on offered against remaining headroom, accepted; average order value rises.",
        "action": "Beat 2.",
        "camera": "Full-frame UI.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "This is the product claim, demonstrated: an offer that is approvable before it is made.",
    },
    "S12": {
        "visual": "The same offer made blind, refused with CEILING_PER_TXN.",
        "action": "Beat 3, which is expected to fail and is asserted on failing.",
        "camera": "Full-frame UI.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "The contrast the whole pitch rests on, shown rather than described.",
    },
    "S13": {
        "visual": "Four attacks and four machine-readable refusals.",
        "action": "Beat 4.",
        "camera": "Full-frame UI.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Show that refusals carry codes a caller can act on, not prose.",
    },
    "S14": {
        "visual": "Capture succeeds, fulfilment fails, compensations run in reverse, an alternative is offered and signed.",
        "action": "Beat 5, running its full saga at the deployed step delay.",
        "camera": "Full-frame UI.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Demonstrate the failure path — the part most demos skip — including who must sign the replacement.",
    },
    "S15": {
        "visual": "The firewall surface: the same decision from the buyer's side, walked check by check onto the one that failed.",
        "action": "Pointer opens a blocked row and replays the decision.",
        "camera": "Full-frame UI.",
        "transition": "Cut back to motion graphics.",
        "audio": "Narration only.",
        "purpose": "Close the loop between the merchant's view and the account holder's.",
    },
    "S16": {
        "visual": "The four-arm experiment as stacked bars, then the two figures that carry the argument.",
        "action": "Rows arrive per arm; the two large figures land last.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Put a measured result behind the claim, generated by the harness rather than typed.",
    },
    "S17": {
        "visual": "Three limitations, stated in the same type as the results.",
        "action": "Rows arrive as each is named.",
        "camera": "Static.",
        "transition": "Cut.",
        "audio": "Narration only.",
        "purpose": "Credibility. The crossover, the modelled arm and the untested rail are named, not buried.",
    },
    "S18": {
        "visual": "Wordmark, the one-line proposition, the repository and the deployed instance.",
        "action": "Wordmark, line, then the URLs.",
        "camera": "Static.",
        "transition": "Hold, then fade out with the audio.",
        "audio": "Narration, then room tone fades.",
        "purpose": "Leave one sentence and two addresses on screen.",
    },
}


def clock(t: float) -> str:
    return f"{int(t // 60):02}:{t % 60:05.2f}"


def main() -> None:
    total = TL["total"]
    script = [
        "# PACT — video script",
        "",
        f"Generated from `script/timeline.json`. Runtime **{int(total // 60)}m {total % 60:04.1f}s**, "
        "1920×1080, 30 fps, 18 scenes.",
        "",
        "Timings are measured, not intended: each scene lasts exactly as long as its "
        "narration plus its lead-in and tail, so what is written here is what was cut.",
        "",
        "---",
        "",
    ]
    plan = [
        "# PACT — scene plan",
        "",
        "| # | Act | Kind | In | Out | Length | Source |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for scene in TL["scenes"]:
        sid, note = scene["id"], NOTES[scene["id"]]
        start, end = scene["start"], scene["start"] + scene["duration"]
        source = (f"`assets/scenes/{sid}.html`" if scene["kind"] == "motion"
                  else f"take mark `{scene['mark']}`")
        plan.append(
            f"| {sid} | {scene['act']} | {scene['kind']} | {clock(start)} | {clock(end)} "
            f"| {scene['duration']:.2f}s | {source} |")

        script += [
            f"## {sid} — {scene['act']}",
            "",
            f"**TIME** {clock(start)}–{clock(end)}  ·  **DURATION** {scene['duration']:.2f}s  "
            f"·  **VOICE IN** {clock(scene['speech_at'])}",
            "",
            "**NARRATION**",
            "",
            f"> {scene['narration']}",
            "",
            f"**ON-SCREEN** {' · '.join(scene['onscreen'])}",
            "",
            f"**VISUAL** {note['visual']}",
            "",
            f"**SCREEN ACTION** {note['action']}",
            "",
            f"**CAMERA** {note['camera']}",
            "",
            f"**TRANSITION** {note['transition']}",
            "",
            f"**AUDIO** {note['audio']}",
            "",
            f"**TECHNICAL PURPOSE** {note['purpose']}",
            "",
            "---",
            "",
        ]

    plan += [
        "",
        "## How the cut is decided",
        "",
        "Motion scenes are filmed from their own HTML page, which holds every animation "
        "at its first frame until the renderer plays it, so a cue lands on the word that "
        "explains it.",
        "",
        "Product scenes are cut from one continuous take. The recorder wrote a start and "
        "end offset for every segment it drove; where a narration line outlasts its "
        "segment, the compositor first borrows real footage from the idle moments either "
        "side of the mark, and only holds the last frame if the take has nothing left.",
        "",
    ]

    (ROOT / "script" / "video_script.md").write_text("\n".join(script))
    (ROOT / "script" / "scene_plan.md").write_text("\n".join(plan) + "\n")
    print("wrote script/video_script.md and script/scene_plan.md")


if __name__ == "__main__":
    main()
