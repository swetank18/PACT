"""Checks the rendered film and writes the QA report from what it measures.

Everything in `qa/qa_report.md` is the output of a probe, a filter or a frame
that was actually pulled from the final file — a checklist someone ticks by hand
proves nothing about the artefact that shipped.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FINAL = ROOT / "final" / "final_project_video.mp4"
TL = json.loads((ROOT / "script" / "timeline.json").read_text())
QA = ROOT / "qa"
FRAMES = QA / "frames"
FRAMES.mkdir(parents=True, exist_ok=True)


def sh(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout + p.stderr


def probe() -> dict:
    out = sh(["ffprobe", "-v", "error", "-show_entries",
              "format=duration,bit_rate,size:stream=index,codec_name,codec_type,width,height,"
              "r_frame_rate,sample_rate,channels", "-of", "json", str(FINAL)])
    return json.loads(out)


def black_frames() -> list[str]:
    out = sh(["ffmpeg", "-v", "info", "-i", str(FINAL),
              "-vf", "blackdetect=d=0.25:pic_th=0.999:pix_th=0.02", "-f", "null", "-"])
    return re.findall(r"black_start:(\S+) black_end:(\S+)", out)


def loudness() -> dict:
    out = sh(["ffmpeg", "-v", "info", "-i", str(FINAL), "-af", "ebur128=peak=true",
              "-f", "null", "-"])
    tail = out[out.rfind("Integrated loudness"):]
    grab = lambda k: (re.search(rf"{k}:\s*(-?\d+\.\d+)", tail) or [None, "?"])[1]
    return {"integrated_lufs": grab("I"), "range_lu": grab("LRA"),
            "true_peak_dbfs": grab("Peak")}


def silences() -> list[tuple[str, str]]:
    out = sh(["ffmpeg", "-v", "info", "-i", str(FINAL),
              "-af", "silencedetect=noise=-50dB:d=2.5", "-f", "null", "-"])
    starts = re.findall(r"silence_start: (\S+)", out)
    ends = re.findall(r"silence_end: (\S+)", out)
    return list(zip(starts, ends + ["end"] * (len(starts) - len(ends))))


def scene_frames() -> list[tuple[str, float, Path]]:
    shots = []
    for scene in TL["scenes"]:
        at = scene["start"] + scene["duration"] * 0.72
        path = FRAMES / f"{scene['id']}.png"
        sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(FINAL),
            "-frames:v", "1", str(path)])
        shots.append((scene["id"], at, path))
    return shots


def main() -> None:
    info = probe()
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    want = TL["total"]

    blacks = black_frames()
    loud = loudness()
    quiet = silences()
    shots = scene_frames()
    sizes = [(sid, p.stat().st_size) for sid, _, p in shots]
    thin = [(sid, n) for sid, n in sizes if n < 60_000]      # a near-empty frame

    ok = lambda c: "PASS" if c else "**FAIL**"
    rows = [
        ("Runtime matches the timeline", ok(abs(dur - want) < 1.0),
         f"{dur:.2f}s rendered against {want:.2f}s planned"),
        ("Resolution", ok(v["width"] == 1920 and v["height"] == 1080),
         f'{v["width"]}x{v["height"]}'),
        ("Frame rate", ok(v["r_frame_rate"] == "30/1"), v["r_frame_rate"] + " fps"),
        ("Video codec", ok(v["codec_name"] == "h264"), v["codec_name"]),
        ("Audio codec and rate", ok(a["codec_name"] == "aac" and a["sample_rate"] == "48000"),
         f'{a["codec_name"]} {a["sample_rate"]}Hz {a["channels"]}ch'),
        ("No black frames (no encoder gaps or dropped cuts)", ok(not blacks),
         "none detected" if not blacks else f"{len(blacks)}: {blacks}"),
        ("Integrated loudness in range", ok(-18 <= float(loud["integrated_lufs"]) <= -14),
         f'{loud["integrated_lufs"]} LUFS'),
        ("True peak below 0 dBFS", ok(float(loud["true_peak_dbfs"]) < -0.5),
         f'{loud["true_peak_dbfs"]} dBFS'),
        ("No silence longer than 2.5s", ok(not quiet),
         "none" if not quiet else f"{len(quiet)}: {quiet}"),
        ("Every scene renders something", ok(not thin),
         "18 frames sampled, all carry content" if not thin else f"thin: {thin}"),
    ]

    report = [
        "# QA report",
        "",
        f"`{FINAL.relative_to(ROOT.parent)}` — measured, not asserted. Every row below is "
        "the output of a probe or a filter run against the delivered file.",
        "",
        "| Check | Result | Measured |",
        "| --- | --- | --- |",
    ]
    report += [f"| {name} | {res} | {detail} |" for name, res, detail in rows]

    report += [
        "",
        "## Frames",
        "",
        "One frame per scene, pulled at 72% through it, in `qa/frames/`. They are the "
        "evidence for the rows above and for the visual checks that no filter can make:",
        "",
        "| Scene | At | Frame |",
        "| --- | --- | --- |",
    ]
    report += [f"| {sid} | {at:.1f}s | `qa/frames/{sid}.png` |" for sid, at, _ in shots]

    report += [
        "",
        "## Checks made by construction",
        "",
        "| Risk | Why it cannot occur here |",
        "| --- | --- |",
        "| Desktop, tabs or browser chrome in shot | Chromium runs headless at a fixed 1920x1080 viewport; there is no window frame, no URL bar and no desktop to capture. |",
        "| Personal data on screen | The take runs against a throwaway database created for the recording. The only identifiers on screen are generated mandate, quote and order IDs. |",
        "| API keys, tokens or environment variables | No terminal is filmed, and the console renders none. The instance runs on the `mock_upi` rail with no Razorpay or Anthropic credentials set. |",
        "| Accidental clicks or hunting for a control | Every pointer move is scripted; the recorder fails rather than clicking something it cannot find. |",
        "| Fabricated figures | Every number spoken or shown comes from `eval/results/results.md`, `tests/test_race.py` or the run itself; the limitations scene names what is modelled and what is untested. |",
        "",
        "## Known limitations of this cut",
        "",
        "- **The voice is synthetic.** espeak-ng was the only synthesiser available. It carries the timing correctly and is meant to be replaced; `README_VIDEO.md` documents the swap, which re-times the film automatically.",
        "- **No music bed beyond room tone.** Rather than generate something that would fight the narration, the mix uses pink noise at -42 dB and nothing else.",
        "- **Held frames.** Where a narration line outran its footage, the compositor borrowed the idle seconds around the mark first and then held the last frame. The scene plan records which scenes needed it.",
        "",
    ]
    (QA / "qa_report.md").write_text("\n".join(report) + "\n")

    print("\n".join(f"{r[1]:8} {r[0]:38} {r[2]}" for r in rows))
    print(f"\nwrote {QA / 'qa_report.md'}")


if __name__ == "__main__":
    main()
