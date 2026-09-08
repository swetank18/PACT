"""Cuts, captions and mixes the film from the measured timeline.

Three inputs, none of them hand-edited: the narration timings in
`script/timeline.json`, the marks the walkthrough recorder wrote while it drove
the product, and the marks the scene renderer wrote when it started each
animation. Every cut point here is computed from those, so re-recording a take
or re-timing the script needs no edit in this file.

Screen scenes are fitted to their narration by borrowing the idle moments
either side of the mark first — real footage before synthetic padding — and
only holding the last frame if the take genuinely has nothing left to give.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TL = json.loads((ROOT / "script" / "timeline.json").read_text())
TAKE_MARKS = json.loads((ROOT / "recording" / "marks.json").read_text())["marks"]
SCENE_MARKS = json.loads((ROOT / "recording" / "scenes" / "scene-marks.json").read_text())
def sole_take(directory: Path, what: str) -> Path:
    """
    The one take in a directory, or a loud failure.

    This was `next(glob("*.webm"))`, which returns whatever the filesystem
    hands back first. Playwright names a recording by a hash of the page, so
    re-filming does not overwrite the previous take — it adds one. Seven had
    accumulated in recording/scenes/S17 before anyone looked, and the composed
    film had been cutting from an arbitrary one of them: a scene could be
    re-rendered, the change confirmed in the HTML, and the old footage still end
    up in the cut. Silently, because every take is a valid video of the right
    length.

    The recorders now clear their directory before filming, so more than one
    here means something went wrong. Refuse rather than pick.
    """
    takes = sorted(directory.glob("*.webm"))
    if not takes:
        raise SystemExit(f"no take in {directory} — film {what} first")
    if len(takes) > 1:
        listing = "\n  ".join(t.name for t in takes)
        raise SystemExit(
            f"{len(takes)} takes in {directory}, and no way to tell which one you "
            f"meant. Delete all but the one you want, or re-run the recorder, "
            f"which clears the directory first:\n  {listing}"
        )
    return takes[0]


TAKE = sole_take(ROOT / "recording", "the walkthrough")
RENDER = ROOT / "render"
RENDER.mkdir(exist_ok=True)

FPS = 30
SIZE = "1920x1080"
#: The most footage that may be borrowed from before or after a mark. Beyond
#: this the clip starts showing the previous beat's result, which reads as a
#: mistake rather than as a lead-in.
MAX_BORROW = 3.0
V_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "17",
          "-pix_fmt", "yuv420p", "-r", str(FPS)]


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        print(" ".join(shlex.quote(c) for c in cmd))
        print(proc.stderr[-2500:])
        raise SystemExit(f"ffmpeg failed ({proc.returncode})")


def probe(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


TAKE_LEN = probe(TAKE)
ORDERED = [m for m in TAKE_MARKS]


def window(mark: str, need: float) -> tuple[float, float, float]:
    """Return (start, length, hold) for a screen scene.

    `hold` is how much of the scene the last frame has to carry because the
    take, plus every idle frame around it, was still shorter than the line.
    """
    m = TAKE_MARKS[mark]
    i = ORDERED.index(mark)
    prev_end = TAKE_MARKS[ORDERED[i - 1]]["end"] if i else 0.0
    next_start = TAKE_MARKS[ORDERED[i + 1]]["start"] if i + 1 < len(ORDERED) else TAKE_LEN

    have = m["end"] - m["start"]
    short = max(0.0, need - have)
    # Split what is missing across the two idle stretches around the mark.
    lead = min(short / 2, MAX_BORROW, max(0.0, m["start"] - prev_end))
    tail = min(short - lead, MAX_BORROW, max(0.0, next_start - m["end"]))
    start = max(0.0, m["start"] - lead)
    length = min(have + lead + tail, TAKE_LEN - start)
    return start, length, max(0.0, need - length)


def build_scene(scene: dict) -> Path:
    out = RENDER / f"{scene['id']}.mp4"
    need = scene["duration"]

    if scene["kind"] == "motion":
        src = sole_take(ROOT / "recording" / "scenes" / scene["id"], scene["id"])
        start = SCENE_MARKS[scene["id"]]["started"]
        # The recording ends at the last pixel change — a scene that finishes on
        # a held frame simply stops producing frames — so clone the last one out
        # to the scene's length. That is what the browser was showing anyway.
        vf = (f"fps={FPS},scale={SIZE}:flags=lanczos,"
              f"tpad=stop_mode=clone:stop_duration={need:.3f},format=yuv420p")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf,
             "-ss", f"{start:.3f}", "-t", f"{need:.3f}", *V_ARGS, "-an", str(out)])
        return out

    start, length, hold = window(scene["mark"], need)
    overlay = ROOT / "assets" / "overlays" / f"{scene['id']}.png"

    # The take, trimmed to its window, held on its last frame if the line runs
    # past the footage, with a caption riding the opening seconds to name what
    # the viewer is about to watch happen.
    chain = [f"fps={FPS}", f"scale={SIZE}:flags=lanczos"]
    if hold:
        chain.append(f"tpad=stop_mode=clone:stop_duration={hold + 0.4:.2f}")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(TAKE)]

    if overlay.exists():
        out_at = max(1.0, min(7.0, need - 1.5))
        graph = (f"[0:v]{','.join(chain)}[v];"
                 f"[1:v]format=rgba,fade=t=in:st=0.4:d=0.5:alpha=1,"
                 f"fade=t=out:st={out_at:.2f}:d=0.6:alpha=1[cap];"
                 f"[v][cap]overlay=0:0:format=auto,format=yuv420p[vo]")
        cmd += ["-loop", "1", "-t", f"{need:.3f}", "-i", str(overlay),
                "-filter_complex", graph, "-map", "[vo]"]
    else:
        cmd += ["-vf", ",".join(chain) + ",format=yuv420p"]

    cmd += ["-t", f"{need:.3f}", *V_ARGS, "-an", str(out)]
    run(cmd)
    return out


def build_video() -> Path:
    clips = []
    for scene in TL["scenes"]:
        path = build_scene(scene)
        got = probe(path)
        print(f"  {scene['id']}  {scene['kind']:6}  want {scene['duration']:6.2f}s  got {got:6.2f}s")
        clips.append(path)

    listing = RENDER / "concat.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips))
    silent = RENDER / "_video_only.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(silent)])
    return silent


def build_audio() -> Path:
    """Narration placed at its absolute cue, over a bed of very quiet air."""
    total = TL["total"]
    inputs, filters, labels = [], [], []
    for i, scene in enumerate(TL["scenes"]):
        inputs += ["-i", str(ROOT / scene["vo_file"])]
        delay = int(scene["speech_at"] * 1000)
        filters.append(f"[{i}:a]adelay={delay}|{delay},apad[v{i}]")
        labels.append(f"[v{i}]")

    n = len(TL["scenes"])
    # Pink noise at the level of a quiet room: it glues the cuts together
    # without ever competing with the voice.
    filters.append(f"anoisesrc=color=pink:duration={total + 1:.2f}:sample_rate=48000"
                   f",lowpass=f=520,highpass=f=60,volume=-42dB,aformat=channel_layouts=stereo[bed]")
    filters.append("".join(labels) + f"[bed]amix=inputs={n + 1}:normalize=0:duration=longest[mixed]")
    filters.append(f"[mixed]atrim=0:{total:.3f},afade=t=in:st=0:d=0.6,"
                   f"afade=t=out:st={total - 1.2:.2f}:d=1.2,loudnorm=I=-16:TP=-1.5:LRA=11[out]")

    out = RENDER / "_audio.wav"
    run(["ffmpeg", "-y", "-v", "error", *inputs,
         "-filter_complex", ";".join(filters), "-map", "[out]",
         "-ar", "48000", "-ac", "2", str(out)])
    return out


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narration,DejaVu Sans,40,&H00F2EFEB,&H00F2EFEB,&HC8000000,&HB4000000,0,0,0,0,100,100,0.2,0,3,3,0,2,300,300,58,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_subtitles() -> Path:
    """SRT -> ASS on a 1920x1080 canvas, so the type is the size it says it is."""
    src = (ROOT / "subtitles" / "project.srt").read_text().strip()
    lines = []
    for block in src.split("\n\n"):
        rows = [r for r in block.splitlines() if r.strip()]
        if len(rows) < 3:
            continue
        start, end = (t.strip().replace(",", ".") for t in rows[1].split("-->"))
        clip = lambda t: t[1:-1] if t.startswith("0") else t      # ASS drops the leading zero
        text = "\\N".join(rows[2:])
        lines.append(f"Dialogue: 0,{clip(start)},{clip(end)},Narration,,0,0,0,,{text}")
    out = ROOT / "subtitles" / "project.ass"
    out.write_text(ASS_HEADER + "\n".join(lines) + "\n")
    return out


def main() -> None:
    print("cutting scenes")
    silent = build_video()
    print("mixing narration")
    audio = build_audio()

    subs = build_subtitles()
    final = ROOT / "final" / "final_project_video.mp4"
    final.parent.mkdir(exist_ok=True)
    print("burning subtitles and encoding")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(audio),
         "-vf", f"ass={subs}",
         *V_ARGS, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", "-shortest", str(final)])

    print(f"\n{final}  {probe(final):.2f}s")


if __name__ == "__main__":
    main()
