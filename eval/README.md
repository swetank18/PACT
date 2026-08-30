# eval

`results/results.md` is generated. Do not edit it — regenerate it.

```bash
./scripts/dev.sh                                   # services must be up
python sim/run.py --all --sessions 200 --seeds 3
```

Everything in it comes from a run. A hand-typed number on a results slide is the
one thing a panel will catch and never forgive, so there is no path by which a
figure reaches that file except by being measured.

## What the numbers currently say

The comparison that is unambiguous is **C against D**: PACT nets about a quarter
more revenue than a naive client-side cap, with a 0% false block rate against
C's ~28%.

The comparison that is *not* what we would have liked is **B against D**. An
ungated agent channel converts more, because nothing stops it — including the
things that should. Under the loss model here it nets more than PACT below
roughly 20% adversarial traffic. That is in `results.md` with a six-point
sensitivity sweep, and it is not being adjusted until it says something nicer.

What the loss model does not price, and cannot from here: chargeback fees,
dispute handling cost, the cost of an account in bad standing, and what it does
to a merchant to be known for accepting unauthorised agent payments. Those are
real and they all point the same way. They are also not measurable in this
harness, so they are named rather than estimated.

One number in `results.md` is worth more than the crossover: **arm D's net is
flat across every adversarial rate we swept.** It refuses all of it. Arm B
degrades linearly.

## The assumptions, all in one place

| Assumption | Value | Where it lives | How to vary it |
|---|---|---|---|
| Human checkout completion (arm A) | 34% | `sim/run.py` | edit and rerun |
| Human addon rate (arm A) | 11% | `sim/run.py` | edit and rerun |
| Adversarial share of sessions | 8% | `sim/hostile.py` | `--hostile-rate` |
| Persona mix | weighted | `sim/personas.yaml` | `--uniform` |

Arm A is **modelled, not simulated** — there is no agent to run. Arms B, C and D
are fully simulated against the real services.

## Layout

```
results/results.md      the paste-ready tables
results/raw.json        the same data, machine readable
results/sessions/       one JSON per session when --no-save is omitted
```
