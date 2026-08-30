#!/usr/bin/env python3
"""
Generates `contracts/generated.ts` from the Python contracts.

The reason codes, the check order and the saga states exist in two languages.
Hand-maintaining both is how they drift, and a drift here is not a compile error
— it is a console that renders a code it does not recognise, at the worst
possible moment. So one side is generated from the other and the generated file
is committed, so a reviewer sees the diff when a code changes.

Run after touching `contracts/reason_codes.py` or the saga states:

    python3 scripts/gen_ts_contracts.py

`tests/test_invariants.py` asserts the committed file is up to date, so
forgetting to run it fails the suite rather than shipping a mismatch.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from contracts.reason_codes import (  # noqa: E402
    CHECK_ORDER,
    REASON_TEXT,
    ReasonCode,
    STEP_UP_CODES,
)
from contracts.schemas import SagaState  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "contracts" / "generated.ts"

HEADER = """\
/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Produced by `scripts/gen_ts_contracts.py` from `contracts/reason_codes.py`.
 * Run that script after changing a reason code, the check order, or a saga
 * state. `tests/test_invariants.py` asserts this file is current, so a stale
 * copy fails the Python suite rather than shipping a console that renders a
 * code it does not recognise.
 *
 * Human-facing strings are generated too, but the console is free to override
 * them — wording on screen is a presentation decision. What must not drift is
 * the set of codes and the order of the chain.
 */

"""


def ts_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    lines: list[str] = [HEADER]

    lines.append("export const REASON_CODES = [")
    for code in ReasonCode:
        lines.append(f"  {ts_string(code.value)},")
    lines.append("] as const;\n")
    lines.append("export type ReasonCode = (typeof REASON_CODES)[number];\n")

    lines.append("/** Plain language. The console may override any of these. */")
    lines.append("export const REASON_TEXT: Record<ReasonCode, string> = {")
    for code in ReasonCode:
        lines.append(f"  {code.value}: {ts_string(REASON_TEXT[code])},")
    lines.append("};\n")

    lines.append("/** Codes that ask a human rather than refusing outright. */")
    lines.append("export const STEP_UP_CODES: readonly ReasonCode[] = [")
    for code in sorted(c.value for c in STEP_UP_CODES):
        lines.append(f"  {ts_string(code)},")
    lines.append("];\n")

    lines.append("/** The frozen order. Never reorder, never truncate on screen. */")
    lines.append("export const CHECK_ORDER = [")
    for name in CHECK_ORDER:
        lines.append(f"  {ts_string(name)},")
    lines.append("] as const;\n")
    lines.append("export type CheckName = (typeof CHECK_ORDER)[number];\n")

    saga_states = SagaState.__args__  # type: ignore[attr-defined]
    lines.append("export const SAGA_STATES = [")
    for state in saga_states:
        lines.append(f"  {ts_string(state)},")
    lines.append("] as const;\n")
    lines.append("export type SagaState = (typeof SAGA_STATES)[number];\n")

    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({len(ReasonCode)} codes, {len(CHECK_ORDER)} checks, "
          f"{len(saga_states)} saga states)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
