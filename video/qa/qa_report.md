# QA report

`video/final/final_project_video.mp4` — measured, not asserted. Every row below is the output of a probe or a filter run against the delivered file.

| Check | Result | Measured |
| --- | --- | --- |
| Runtime matches the timeline | PASS | 349.82s rendered against 349.82s planned |
| Resolution | PASS | 1920x1080 |
| Frame rate | PASS | 30/1 fps |
| Video codec | PASS | h264 |
| Audio codec and rate | PASS | aac 48000Hz 2ch |
| No black frames (no encoder gaps or dropped cuts) | PASS | none detected |
| Integrated loudness in range | PASS | -16.0 LUFS |
| True peak below 0 dBFS | PASS | -3.1 dBFS |
| No silence longer than 2.5s | PASS | none |
| Every scene renders something | PASS | 18 frames sampled, all carry content |

## Frames

One frame per scene, pulled at 72% through it, in `qa/frames/`. They are the evidence for the rows above and for the visual checks that no filter can make:

| Scene | At | Frame |
| --- | --- | --- |
| S01 | 10.6s | `qa/frames/S01.png` |
| S02 | 31.7s | `qa/frames/S02.png` |
| S03 | 49.6s | `qa/frames/S03.png` |
| S04 | 71.5s | `qa/frames/S04.png` |
| S05 | 97.9s | `qa/frames/S05.png` |
| S06 | 124.9s | `qa/frames/S06.png` |
| S07 | 145.1s | `qa/frames/S07.png` |
| S08 | 165.5s | `qa/frames/S08.png` |
| S09 | 187.8s | `qa/frames/S09.png` |
| S10 | 205.1s | `qa/frames/S10.png` |
| S11 | 222.6s | `qa/frames/S11.png` |
| S12 | 237.8s | `qa/frames/S12.png` |
| S13 | 253.9s | `qa/frames/S13.png` |
| S14 | 274.7s | `qa/frames/S14.png` |
| S15 | 288.6s | `qa/frames/S15.png` |
| S16 | 309.6s | `qa/frames/S16.png` |
| S17 | 332.6s | `qa/frames/S17.png` |
| S18 | 346.7s | `qa/frames/S18.png` |

## Checks made by construction

| Risk | Why it cannot occur here |
| --- | --- |
| Desktop, tabs or browser chrome in shot | Chromium runs headless at a fixed 1920x1080 viewport; there is no window frame, no URL bar and no desktop to capture. |
| Personal data on screen | The take runs against a throwaway database created for the recording. The only identifiers on screen are generated mandate, quote and order IDs. |
| API keys, tokens or environment variables | No terminal is filmed, and the console renders none. The instance runs on the `mock_upi` rail with no Razorpay or Anthropic credentials set. |
| Accidental clicks or hunting for a control | Every pointer move is scripted; the recorder fails rather than clicking something it cannot find. |
| Fabricated figures | Every number spoken or shown comes from `eval/results/results.md`, `tests/test_race.py` or the run itself; the limitations scene names what is modelled and what is untested. |

## Known limitations of this cut

- **The voice is synthetic.** espeak-ng was the only synthesiser available. It carries the timing correctly and is meant to be replaced; `README_VIDEO.md` documents the swap, which re-times the film automatically.
- **No music bed beyond room tone.** Rather than generate something that would fight the narration, the mix uses pink noise at -42 dB and nothing else.
- **Held frames.** Where a narration line outran its footage, the compositor borrowed the idle seconds around the mark first and then held the last frame. The scene plan records which scenes needed it.

