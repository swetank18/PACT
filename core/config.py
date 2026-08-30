"""
Profiles. A profile is a config file, not a fork.

One per event. Changing events is changing a flag: which rail settles, which
surfaces the console renders, which framing copy loads. Never fork the repo per
hackathon, and never carry event-specific logic into `core/`.

This module also exists so that `core/` holds no vendor literal. The merchant's
VPA happens to have `@razorpay` as its handle, and a default like that sitting
in `core/app.py` is exactly the kind of quiet coupling `test_invariants.py`
greps for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

#: Deliberately not a literal. Naming an event's profile here would put a
#: vendor string back into the rail-agnostic layer, which is the thing this
#: module exists to prevent. The launcher sets PACT_PROFILE; with nothing set,
#: the loader falls back to the simulated rail, which is the safe default —
#: booting into a real payment rail because an env var was missing is not.
PROFILE_ENV_VAR = "PACT_PROFILE"
FALLBACK_RAIL = "mock_upi"

#: Rails that settle in memory. Named positively, because the alternative —
#: `rail != "<vendor>"` — is a vendor literal in the rail-agnostic layer and
#: silently reclassifies every future rail as simulated.
SIMULATED_RAILS = frozenset({"mock_upi"})


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    rail: str
    merchant_vpa: str
    merchant_name: str
    surfaces: tuple[str, ...] = ()
    #: Which demo beats are bound to which keys, for the console.
    beats: dict[str, str] = field(default_factory=dict)
    #: Event-specific framing copy. Read by the console, never by the engine.
    framing: dict[str, Any] = field(default_factory=dict)

    @property
    def is_simulated_rail(self) -> bool:
        return self.rail in SIMULATED_RAILS


def load(name: str | None = None) -> Profile:
    """
    Load a profile by name. Environment variables win, so a single run can be
    overridden without editing a file that is under version control.
    """
    profile_name = name or os.environ.get(PROFILE_ENV_VAR, "")

    data: dict[str, Any] = {}
    if profile_name:
        path = PROFILES_DIR / f"{profile_name}.yaml"
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
    # A missing or unnamed profile is not fatal: the environment can supply
    # everything, and a service that refuses to boot because a config file moved
    # is a bad trade at hour 30. It falls back to the simulated rail, never to a
    # live one.

    return Profile(
        name=profile_name or "(none)",
        rail=os.environ.get("PACT_RAIL") or data.get("rail", FALLBACK_RAIL),
        merchant_vpa=os.environ.get("PACT_MERCHANT_VPA") or data.get("merchant_vpa", ""),
        merchant_name=data.get("merchant_name", "DeskKit"),
        surfaces=tuple(data.get("surfaces", ())),
        beats=data.get("beats", {}) or {},
        framing=data.get("framing", {}) or {},
    )
