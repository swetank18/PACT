# Reproducing the video

Everything in `video/` is generated. There is no project file to open and no
manual edit to repeat: the narration is measured, the timeline is computed from
it, the scenes are filmed in a browser, and the cut is assembled with ffmpeg.
Running the five commands below on a clean checkout produces the same film.

```
video/
  script/       scenes.json (the source of truth) -> timeline.json, video_script.md, scene_plan.md, project_brief.md
  assets/       scenes/*.html (motion graphics)  overlays/*.png (captions)  diagrams/architecture.svg
  audio/vo/     one wav per scene
  subtitles/    project.srt
  recording/    the walkthrough take + marks.json, and scenes/ for the motion takes
  render/       one mp4 per scene, then the silent assembly and the mixed audio
  final/        final_project_video.mp4
  qa/           qa_report.md and the frames it was written from
```

## Prerequisites

| Need | Why | Check |
| --- | --- | --- |
| Python 3.12 + the project venv | runs PACT and the build scripts | `.venv/bin/python -V` |
| Node 20 with `console/node_modules` | Playwright drives and films the browser | `cd console && npm install` |
| ffmpeg with libx264, aac and libass | cuts, mixes and burns subtitles | `ffmpeg -buildconf \| grep libass` |
| espeak-ng | the placeholder voice (see below) | `espeak-ng --version` |
| A built console (`console/dist`) | the app serves the UI from `/` | `cd console && npm run build` |

No network is needed. Fonts are read from `console/node_modules/@fontsource-variable`,
so the scenes render identically offline.

## Build

```bash
# 1. A cold instance on a throwaway database, so the take starts from zero.
export RUN=/tmp/pact-video
rm -rf $RUN && mkdir -p $RUN
PACT_PROFILE=razorpay-track01 \
PACT_DB_URL=sqlite:///$RUN/demo.db \
PACT_GATE_KEY_PATH=$RUN/key.hex \
PACT_SELF_URL=http://127.0.0.1:8090 \
PACT_SAGA_STEP_DELAY_S=0.25 \
.venv/bin/python -m uvicorn deploy.app:app --host 127.0.0.1 --port 8090 &
curl -s http://127.0.0.1:8090/healthz          # must report ok, console true

# 2. Narration, timeline and subtitles — everything downstream is timed by this.
python3 video/build_audio.py

# 3. The pages: motion scenes, caption overlays, and the documents.
python3 video/build_scenes.py
python3 video/build_overlays.py
python3 video/build_docs.py

# 4. Film. Both run from console/ so they resolve @playwright/test.
cd console
node ../video/record_walkthrough.mjs http://127.0.0.1:8090 ../video/recording
node ../video/render_scenes.mjs ../video
node ../video/render_overlays.mjs ../video/assets/overlays
cd ..

# 5. Cut, mix, burn subtitles, encode.
python3 video/compose.py

# 6. Check the result and write the report.
python3 video/qa.py
```

Output: `video/final/final_project_video.mp4` — 1920×1080, 30 fps, H.264 + AAC.

## The voice

`espeak-ng` is the only synthesiser available on the build machine, and it
sounds like it. It is used as a **placeholder that sets the timing**, not as a
finished voice track.

To replace it with a real voice without re-timing anything by hand, put one wav
per scene in `video/audio/vo/` named `S01.wav` … `S18.wav`, skip step 2's
synthesis, and re-run `build_audio.py` with `--keep-existing` — or simply run
the pipeline from step 2 after swapping the `synthesise()` call for your own
provider. The timeline, the subtitles and every animation cue are derived from
the *measured* length of whatever audio is there, so a longer or shorter read
re-times the film correctly on the next `compose.py`.

## Re-rendering one motion scene

`render_scenes.mjs` takes scene ids after the root and films only those, merging
into the existing `scene-marks.json` rather than replacing it:

```bash
node ../video/render_scenes.mjs ../video S04     # then compose.py
```

Without ids it films all eleven, which is five minutes for a change to one.

## Re-recording only the product footage

The walkthrough is one take. If the app changes, re-run step 4's first command
and then `compose.py`; the marks file it writes tells the compositor where each
segment starts and ends, so no cut points need updating.

If a narration line outruns its footage, the compositor first borrows the idle
seconds either side of the mark (up to 3 s each) and only then holds the last
frame. Give a beat more room by raising its settle time in
`record_walkthrough.mjs` rather than by padding in ffmpeg.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| A motion scene is blank | animations were still paused when filming started | the scene page holds every animation until the renderer plays it — run `render_scenes.mjs`, not a plain screenshot pass |
| A motion scene ends early | Chromium's screencast stops emitting frames while the page is static | already handled: `compose.py` clones the last frame out to the scene length |
| A cue lands late, or a scene composes from footage you replaced | Playwright names a recording by a hash of the page, so re-filming *adds* a take rather than replacing one, and `compose.py` used to cut from `next(glob("*.webm"))` — whichever the filesystem returned. Seven had piled up in `recording/scenes/S17` | fixed: the recorders clear their directory before filming and `compose.py` refuses to guess, naming every take it found. If you see that error, delete all but the one you want |
| `Send` never enables in the take | the composer needs text | the recorder types the intent; check the placeholder string still matches |
| Beat 3 "fails" | it is supposed to | it is the contrast; CI asserts it does not complete |
| Subtitles do not appear | ffmpeg built without libass | rebuild ffmpeg, or drop the `subtitles=` filter and ship `project.srt` as a sidecar |
| The take shows a 404 | a headroom poll for a mandate cleared by the reset | it happens before the first mark and is not in the cut; QA checks the used frames |
