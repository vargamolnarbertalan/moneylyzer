"""Money-weighted return (XIRR) from dated cash flows: money in (buys) negative, money out (sales, and today's
value as if sold) positive, all in the reporting currency.

When the money has been invested for a year or more on average (weighted by amount), the figure is the
annual rate (XIRR). Otherwise an annual rate would explode (+3% in two weeks reads as +116% a year, and one
small buy two years before a large one last week would not change that), so the figure is the return for
the period, not annualised (owner decision 2026-11-26): what came back (sales and today's value) against
what went in. Solving the rate equation with the period as its unit was tried and rejected: money that
came in near the end still made it explode. The period ends at the last flow: today for a holding still
held, the last sale for one sold out (its return must not keep growing while no money is invested).
"""
from __future__ import annotations

import math
from datetime import date

YEAR_DAYS = 365.0
_GRID = [-0.99999, -0.9999, -0.999, -0.99, -0.9, -0.75, -0.5, -0.3, -0.15, -0.05, 0.0, 0.05, 0.15, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0, 100.0, 1e4, 1e6]


def _npv(terms: list[tuple[float, float]], r: float) -> float:
    """Net present value at rate r, in log space so a very long history cannot overflow (it returns ±inf)."""
    total = 0.0
    log_base = math.log1p(r)
    for t, cf in terms:
        exponent = -t * log_base
        total += cf * (math.exp(exponent) if exponent < 700 else math.inf)
    return total


def _solve(flows: list[tuple[date, float]], unit_days: float) -> float | None:
    """The rate r with sum(cf / (1 + r) ** (days since the first flow / unit_days)) = 0.

    The sign of the NPV is checked across a grid of rates first: cash flows that switch between money in and
    out more than once can have several rates that all solve the equation, and picking one silently would be
    a wrong number, so then there is no figure. With exactly one sign change, bisection finds that rate
    (it cannot diverge; 200 halvings are far more than a percentage to one decimal needs)."""
    t0 = flows[0][0]
    terms = [((d - t0).days / unit_days, cf) for d, cf in flows]
    values = [(r, _npv(terms, r)) for r in _GRID]
    values = [(r, v) for r, v in values if not math.isnan(v)]
    changes = [(a, b) for a, b in zip(values, values[1:]) if a[1] * b[1] < 0]
    if len(changes) != 1:
        return None if len(changes) > 1 else next((r for r, v in values if v == 0), None)
    (lo, f_lo), (hi, _f_hi) = changes[0]
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = _npv(terms, mid)
        if f_mid == 0:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def money_weighted(flows: list[tuple[date, float]], today: date) -> dict | None:
    """{"rate", "annualised", "since", "until"} or None when there is nothing (or no single rate) to measure.

    `flows` must hold at least one outflow (a buy, negative) and one inflow (a sale or today's value).
    """
    flows = sorted((d, float(cf)) for d, cf in flows if cf)
    if not any(cf < 0 for _, cf in flows) or not any(cf > 0 for _, cf in flows):
        return None
    since, until = flows[0][0], flows[-1][0]
    span = (until - since).days
    # ongoing: still held (the last flow is today's value); otherwise the period ended at the last sale
    out = {"since": since.isoformat(), "until": until.isoformat(), "ongoing": until >= today}
    # how long the money was invested, on average weighted by amount: one small early buy doesn't make it a year
    paid = [(d, -cf) for d, cf in flows if cf < 0]
    weighted_days = sum(amount * (until - d).days for d, amount in paid) / sum(amount for _, amount in paid)
    if span < 1 or weighted_days < YEAR_DAYS:  # the period's return: money back against money in
        rate = sum(cf for _, cf in flows if cf > 0) / sum(amount for _, amount in paid) - 1
        return {**out, "rate": rate, "annualised": False}
    rate = _solve(flows, YEAR_DAYS)
    if rate is None:
        return None
    return {**out, "rate": rate, "annualised": True}
