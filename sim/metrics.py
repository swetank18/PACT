"""
Metrics, computed from the engine's decision log and the merchant's saga table.

**Not from the harness's own bookkeeping.** The agent records what it thinks
happened; the engine records what did. Where they disagree the engine is right
and the harness has a bug, and `cross_check` exists to say so out loud rather
than let a quiet discrepancy reach a slide.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import httpx

from contracts.money import Paise
from buyer.agent import SessionResult


@dataclass
class ArmMetrics:
    arm: str
    sessions: int = 0

    gmv_paise: Paise = 0
    completed: int = 0
    avg_order_value_paise: Paise = 0

    upsell_offers_made: int = 0
    upsell_offers_accepted: int = 0
    upsell_rejected_by_gate: int = 0

    step_ups: int = 0
    step_ups_recovered: int = 0
    false_blocks: int = 0

    saga_recoveries: int = 0
    refunded_paise: Paise = 0
    loss_paise: Paise = 0
    errors: int = 0

    @property
    def gmv_per_100(self) -> float:
        return (self.gmv_paise / self.sessions * 100) if self.sessions else 0.0

    @property
    def completion_rate(self) -> float:
        return self.completed / self.sessions if self.sessions else 0.0

    @property
    def attach_rate(self) -> float:
        return (
            self.upsell_offers_accepted / self.upsell_offers_made
            if self.upsell_offers_made
            else 0.0
        )

    @property
    def upsell_rejection_rate(self) -> float:
        """
        The share of offers the gate refused.

        Zero for arm D by construction — the merchant read the buyer's authority
        before offering. Visibly non-zero for arm C, and that gap is the whole
        argument.
        """
        return (
            self.upsell_rejected_by_gate / self.upsell_offers_made
            if self.upsell_offers_made
            else 0.0
        )

    @property
    def false_block_rate(self) -> float:
        return self.false_blocks / self.sessions if self.sessions else 0.0

    @property
    def step_up_recovery_rate(self) -> float:
        return self.step_ups_recovered / self.step_ups if self.step_ups else 0.0

    @property
    def net_revenue_paise(self) -> Paise:
        """GMV minus what was lost and what was refunded. The bottom line."""
        return self.gmv_paise - self.loss_paise

    @property
    def loss_per_100(self) -> float:
        """
        Losses on the same basis as GMV.

        Reporting a multi-seed total in a column next to per-100 means makes the
        loss look three times larger than it is. Every money column in the table
        is per 100 sessions.
        """
        return (self.loss_paise / self.sessions * 100) if self.sessions else 0.0

    @property
    def net_per_100(self) -> float:
        return (self.net_revenue_paise / self.sessions * 100) if self.sessions else 0.0

    def to_row(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "sessions": self.sessions,
            "gmv_per_100_paise": round(self.gmv_per_100),
            "completion_rate": round(self.completion_rate, 4),
            "aov_paise": self.avg_order_value_paise,
            "offers_made": self.upsell_offers_made,
            "offers_accepted": self.upsell_offers_accepted,
            "offers_rejected_by_gate": self.upsell_rejected_by_gate,
            "attach_rate": round(self.attach_rate, 4),
            "upsell_rejection_rate": round(self.upsell_rejection_rate, 4),
            "false_block_rate": round(self.false_block_rate, 4),
            "step_up_recovery_rate": round(self.step_up_recovery_rate, 4),
            "saga_recoveries": self.saga_recoveries,
            "loss_paise": self.loss_paise,
            "net_per_100_paise": round(self.net_per_100),
            "errors": self.errors,
        }


def aggregate(arm: str, sessions: Sequence[SessionResult]) -> ArmMetrics:
    m = ArmMetrics(arm=arm, sessions=len(sessions))
    for s in sessions:
        m.gmv_paise += s.gmv_paise
        m.loss_paise += s.loss_paise
        m.refunded_paise += s.refunded_paise
        m.upsell_offers_made += s.upsell_offered
        m.upsell_offers_accepted += s.upsell_accepted
        m.upsell_rejected_by_gate += s.upsell_rejected_by_gate
        m.step_ups += s.step_ups
        m.step_ups_recovered += s.step_ups_recovered
        m.false_blocks += s.false_blocks
        m.saga_recoveries += s.saga_recoveries
        if s.completed:
            m.completed += 1
        if s.error:
            m.errors += 1

    m.avg_order_value_paise = round(m.gmv_paise / m.completed) if m.completed else 0
    return m


def mean_and_range(values: Sequence[float]) -> tuple[float, float, float]:
    """
    Mean, min, max.

    A single run of a stochastic agent is not a result, and a national panel
    will say so. Every headline number is reported across seeds.
    """
    if not values:
        return 0.0, 0.0, 0.0
    return statistics.fmean(values), min(values), max(values)


@dataclass
class CrossCheck:
    """What the engine says, next to what the harness says."""

    engine_gmv_paise: Paise = 0
    harness_gmv_paise: Paise = 0
    engine_decisions: int = 0
    harness_decisions: int = 0
    discrepancies: list[str] = field(default_factory=list)

    @property
    def agrees(self) -> bool:
        return not self.discrepancies


def cross_check(
    sessions: Sequence[SessionResult],
    *,
    gate_url: str = "http://localhost:8000",
    merchant_url: str = "http://localhost:8100",
) -> CrossCheck:
    """
    Compare the harness's tally against the services' own records.

    This is the check that stops a plausible-looking table being wrong. If the
    merchant's stats endpoint and this harness disagree on GMV, one of them is
    lying and it is worth knowing which before the number is read out loud.
    """
    check = CrossCheck()
    try:
        with httpx.Client(timeout=10) as client:
            stats = client.get(f"{merchant_url}/v1/stats").json()
            decisions = client.get(f"{gate_url}/v1/decisions", params={"limit": 200}).json()
    except httpx.HTTPError as exc:
        check.discrepancies.append(f"could not reach the services: {exc}")
        return check

    check.engine_gmv_paise = int(stats.get("gmv_paise", 0))
    check.harness_gmv_paise = sum(s.gmv_paise for s in sessions)
    check.engine_decisions = len(decisions.get("decisions", []))
    check.harness_decisions = sum(len(s.decisions) for s in sessions)

    if check.engine_gmv_paise != check.harness_gmv_paise:
        check.discrepancies.append(
            f"GMV: merchant says {check.engine_gmv_paise}, harness says "
            f"{check.harness_gmv_paise}. The merchant is right; the harness has a bug."
        )
    # The decision log is capped at 200 rows, so a long run legitimately shows
    # fewer engine decisions than the harness made. Only the other direction is
    # a problem: decisions the engine recorded that the harness never made.
    if check.engine_decisions > check.harness_decisions and check.harness_decisions:
        check.discrepancies.append(
            f"the engine recorded {check.engine_decisions} decisions but the harness "
            f"only made {check.harness_decisions}; something else is talking to the gate"
        )
    return check
