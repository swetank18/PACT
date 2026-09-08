"""Narration, timeline and subtitles — all derived from `script/scenes.json`.

Timing runs one way only: the voice-over is generated first, its real duration
is measured, and every scene's length, every subtitle cue and the final cut are
computed from that. Nothing here is typed in by hand, so re-running after a
script edit re-times the whole video consistently.

The voice is espeak-ng, which is the only synthesiser on this machine. It is a
placeholder: `video/README_VIDEO.md` documents how to swap in a real voice
without changing any timing, because the timeline is regenerated from whatever
audio the synthesiser produces.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCENES = json.loads((ROOT / "script" / "scenes.json").read_text())
VO_DIR = ROOT / "audio" / "vo"
VO_DIR.mkdir(parents=True, exist_ok=True)

#: espeak-ng at 160 wpm lands close to a measured product-video pace. The chain
#: after it trims the synthesiser's brittle top end, evens out its level, and
#: leaves headroom for the bed.
WPM = 160
POST = (
    "highpass=f=95,lowpass=f=7600,"
    "acompressor=threshold=-18dB:ratio=3:attack=8:release=180,"
    "loudnorm=I=-17:TP=-2.5:LRA=9"
)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def synthesise(scene: dict) -> Path:
    raw = VO_DIR / f"{scene['id']}.raw.wav"
    out = VO_DIR / f"{scene['id']}.wav"
    subprocess.run(
        ["espeak-ng", "-v", "en-us", "-s", str(WPM), "-p", "38", "-a", "150",
         "-w", str(raw), scene["narration"]],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", POST,
         "-ar", "48000", "-ac", "2", str(out)],
        check=True,
    )
    raw.unlink()
    return out


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?:])\s+", text.strip())
    merged: list[str] = []
    for part in parts:
        # A fragment too short to read on its own rides with the previous cue.
        if merged and len(part) < 28:
            merged[-1] += " " + part
        else:
            merged.append(part)
    return merged


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def wrap(text: str, width: int = 46) -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    # Two lines maximum on screen; longer cues are already split by sentence.
    return "\n".join(lines[:2]) if len(lines) <= 2 else "\n".join(
        [" ".join(lines[: len(lines) // 2]), " ".join(lines[len(lines) // 2:])]
    )


def main() -> None:
    timeline, cues, clock = [], [], 0.0

    for scene in SCENES["scenes"]:
        vo = synthesise(scene)
        vo_len = duration(vo)
        start = clock
        speech_at = start + scene["pad_in"]
        length = scene["pad_in"] + vo_len + scene["pad_out"]
        clock += length

        timeline.append({
            **{k: scene[k] for k in ("id", "act", "kind", "onscreen")},
            "template": scene.get("template"),
            "mark": scene.get("mark"),
            "start": round(start, 3),
            "duration": round(length, 3),
            "speech_at": round(speech_at, 3),
            "vo_duration": round(vo_len, 3),
            "vo_file": str(vo.relative_to(ROOT)),
            "narration": scene["narration"],
        })

        # Subtitle cues: sentence-level, split across the measured speech span
        # in proportion to how much text each sentence carries.
        parts = sentences(scene["narration"])
        total = sum(len(p) for p in parts)
        at = speech_at
        for part in parts:
            share = vo_len * len(part) / total
            cues.append((at, at + share, part))
            at += share

    (ROOT / "script" / "timeline.json").write_text(
        json.dumps({"total": round(clock, 3), "scenes": timeline}, indent=2) + "\n"
    )

    srt = []
    for i, (start, end, text) in enumerate(cues, 1):
        srt.append(f"{i}\n{srt_time(start)} --> {srt_time(end - 0.06)}\n{wrap(text)}\n")
    (ROOT / "subtitles" / "project.srt").write_text("\n".join(srt))

    print(f"{len(timeline)} scenes, {len(cues)} subtitle cues")
    print(f"total runtime {int(clock // 60)}m {clock % 60:04.1f}s")
    for s in timeline:
        print(f"  {s['id']}  {s['kind']:6} {s['start']:7.2f}  +{s['duration']:5.2f}")


if __name__ == "__main__":
    main()
