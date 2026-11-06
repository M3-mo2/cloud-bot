"""The plan catalogue — the **one** place a price or an allowance is written.

Prices are in Telegram Stars. The catalogue is plain data so a new plan is a row
here, never a scatter of ``if plan == "year"`` branches across the codebase.

Price reasoning (the commitment must be rewarded):

* ``week`` is the reference rate: 50 stars for 7 days ≈ 7.14 stars/day.
* ``month`` at 150 stars for 30 days is 5.00 stars/day — 30 % below the weekly
  rate (30 × 7.14 = 214 stars), which pays for a month's commitment.
* ``year`` at 1200 stars for 365 days is 3.29 stars/day — 35 % below 52 weeks
  (52 × 50 = 2600 stars) and 34 % below 12 months (12 × 150 = 1800 stars), so a
  year is the strongest commitment discount while staying a round, memorable
  number.

The trial is a catalogue entry too (``trial``): 7 free days, once per user, with
the ``week`` allowance. It is not purchasable because ``amount_stars == 0`` and
the store refuses to buy it.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.domain.value_objects import Limits

#: 7 days free, once per user (the first workspace starts it).
TRIAL_DAYS = 7


@dataclass(frozen=True)
class Plan:
    code: str
    period_days: int
    amount_stars: int
    limits: Limits
    max_workspaces: int

    @property
    def purchasable(self) -> bool:
        return self.amount_stars > 0

    def to_limits(self, maxima: Limits | None = None) -> Limits:
        """The plan's workspace allowance, optionally capped by engine maxima."""
        if maxima is None:
            return self.limits
        return Limits(
            cpu_cores=min(self.limits.cpu_cores, maxima.cpu_cores),
            memory_mb=min(self.limits.memory_mb, maxima.memory_mb),
            disk_mb=min(self.limits.disk_mb, maxima.disk_mb),
            max_wall_time_sec=min(self.limits.max_wall_time_sec, maxima.max_wall_time_sec),
            max_runs=min(self.limits.max_runs, maxima.max_runs),
            allow_net=self.limits.allow_net and maxima.allow_net,
            allow_unlimited_runs=(
                self.limits.allow_unlimited_runs and maxima.allow_unlimited_runs
            ),
            allow_tunnels=self.limits.allow_tunnels and maxima.allow_tunnels,
            write_rate_mb_s=min(self.limits.write_rate_mb_s, maxima.write_rate_mb_s),
        )


#: The catalogue. Order is display order.
PLANS: dict[str, Plan] = {
    "week": Plan(
        code="week",
        period_days=7,
        amount_stars=50,
        limits=Limits(cpu_cores=1.0, memory_mb=512, disk_mb=1024),
        max_workspaces=2,
    ),
    "month": Plan(
        code="month",
        period_days=30,
        amount_stars=150,
        limits=Limits(cpu_cores=2.0, memory_mb=1024, disk_mb=5120),
        max_workspaces=3,
    ),
    "year": Plan(
        code="year",
        period_days=365,
        amount_stars=1200,
        limits=Limits(cpu_cores=2.0, memory_mb=2048, disk_mb=10240),
        max_workspaces=5,
    ),
    "trial": Plan(
        code="trial",
        period_days=TRIAL_DAYS,
        amount_stars=0,
        limits=Limits(cpu_cores=1.0, memory_mb=512, disk_mb=1024),
        max_workspaces=2,
    ),
}

#: Plans a user may buy. The trial is deliberately excluded.
PAID_PLAN_CODES: tuple[str, ...] = ("week", "month", "year")


def plan(code: str) -> Plan:
    try:
        return PLANS[code]
    except KeyError as exc:
        raise KeyError(f"unknown plan code {code!r}") from exc


def paid_plans() -> tuple[Plan, ...]:
    return tuple(PLANS[code] for code in PAID_PLAN_CODES)
