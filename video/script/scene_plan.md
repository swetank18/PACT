# PACT — scene plan

| # | Act | Kind | In | Out | Length | Source |
| --- | --- | --- | --- | --- | --- | --- |
| S01 | 1 - The problem | motion | 00:00.00 | 00:14.72 | 14.72s | `assets/scenes/S01.html` |
| S02 | 1 - The problem | motion | 00:14.72 | 00:38.27 | 23.55s | `assets/scenes/S02.html` |
| S03 | 2 - The solution | motion | 00:38.27 | 00:53.97 | 15.70s | `assets/scenes/S03.html` |
| S04 | 3 - How it works | motion | 00:53.97 | 01:18.37 | 24.40s | `assets/scenes/S04.html` |
| S05 | 3 - How it works | motion | 01:18.37 | 01:45.56 | 27.20s | `assets/scenes/S05.html` |
| S06 | 4 - Technical differentiation | motion | 01:45.56 | 02:12.36 | 26.80s | `assets/scenes/S06.html` |
| S07 | 4 - Technical differentiation | motion | 02:12.37 | 02:30.06 | 17.70s | `assets/scenes/S07.html` |
| S08 | 4 - Technical differentiation | motion | 02:30.06 | 02:51.53 | 21.46s | `assets/scenes/S08.html` |
| S09 | 5 - Live walkthrough | screen | 02:51.53 | 03:14.18 | 22.65s | take mark `grant` |
| S10 | 5 - Live walkthrough | screen | 03:14.18 | 03:29.37 | 15.19s | take mark `beat1` |
| S11 | 5 - Live walkthrough | screen | 03:29.37 | 03:47.72 | 18.36s | take mark `addon` |
| S12 | 5 - Live walkthrough | screen | 03:47.72 | 04:01.66 | 13.94s | take mark `beat3` |
| S13 | 5 - Live walkthrough | screen | 04:01.66 | 04:18.71 | 17.05s | take mark `beat4` |
| S14 | 5 - Live walkthrough | screen | 04:18.71 | 04:40.91 | 22.20s | take mark `beat5` |
| S15 | 5 - Live walkthrough | screen | 04:40.91 | 04:51.64 | 10.73s | take mark `firewall` |
| S16 | 6 - Impact | motion | 04:51.64 | 05:16.54 | 24.90s | `assets/scenes/S16.html` |
| S17 | 6 - Impact | motion | 05:16.54 | 05:38.83 | 22.29s | `assets/scenes/S17.html` |
| S18 | 7 - Close | motion | 05:38.83 | 05:49.82 | 10.99s | `assets/scenes/S18.html` |

## How the cut is decided

Motion scenes are filmed from their own HTML page, which holds every animation at its first frame until the renderer plays it, so a cue lands on the word that explains it.

Product scenes are cut from one continuous take. The recorder wrote a start and end offset for every segment it drove; where a narration line outlasts its segment, the compositor first borrows real footage from the idle moments either side of the mark, and only holds the last frame if the take has nothing left.

