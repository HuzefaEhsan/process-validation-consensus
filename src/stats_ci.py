#!/usr/bin/env python3
"""stats_ci.py -- binomial confidence intervals (stdlib only, no scipy).

  wilson(x, n, conf)          Wilson score interval.
  clopper_pearson(x, n, conf) exact (Clopper-Pearson) interval, by bisection on the binomial tail:
                              lower L solves P(X >= x | n, L) = a/2 (L = 0 when x = 0),
                              upper U solves P(X <= x | n, U) = a/2 (U = 1 when x = n).
  prop(x, n)                  the {x, n, p, wilson95, cp95} record stored in results.json.

UNIT OF ANALYSIS WARNING (repeated in results.json): proportions over LOGS treat logs as
independent Bernoulli trials. They are not: logs of one task share the problem, and honest logs of
one tier share a byte-identical reasoning payload. The intervals are therefore optimistic; task-level
proportions are reported alongside.
"""
from __future__ import annotations

import math
from statistics import NormalDist


def _z(conf: float) -> float:
    return NormalDist().inv_cdf(1.0 - (1.0 - conf) / 2.0)


def wilson(x: int, n: int, conf: float = 0.95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    z = _z(conf)
    p = x / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    lo = 0.0 if x == 0 else max(0.0, centre - half)
    hi = 1.0 if x == n else min(1.0, centre + half)
    return lo, hi


def _log_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p))


def _cdf(x: int, n: int, p: float) -> float:
    """P(X <= x)."""
    return min(1.0, sum(math.exp(_log_pmf(k, n, p)) for k in range(0, x + 1)))


def _sf(x: int, n: int, p: float) -> float:
    """P(X >= x)."""
    return min(1.0, sum(math.exp(_log_pmf(k, n, p)) for k in range(x, n + 1)))


def _bisect(f, target: float, increasing: bool, iters: int = 200) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        v = f(mid)
        if (v < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def clopper_pearson(x: int, n: int, conf: float = 0.95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    a = (1.0 - conf) / 2.0
    lo = 0.0 if x == 0 else _bisect(lambda p: _sf(x, n, p), a, increasing=True)
    hi = 1.0 if x == n else _bisect(lambda p: _cdf(x, n, p), a, increasing=False)
    return lo, hi


def prop(x: int, n: int, *, extra_conf: float | None = None) -> dict:
    """The canonical proportion record. p is None when n == 0 (undefined, never a fake 0)."""
    rec = {"x": int(x), "n": int(n), "p": (x / n) if n else None,
           "wilson95": list(wilson(x, n)) if n else None,
           "cp95": list(clopper_pearson(x, n)) if n else None}
    if extra_conf is not None and n:
        key = f"{extra_conf * 100:g}".replace(".", "_")
        rec[f"wilson{key}"] = list(wilson(x, n, extra_conf))
        rec[f"cp{key}"] = list(clopper_pearson(x, n, extra_conf))
    return rec


# ---------------------------------------------------------------------------------------------
# Paired comparison: McNemar's exact (conditional binomial) test. Replaces the Newcombe interval
# of BLUEPRINT 4.6, which assumes independent samples; here both arms judge the SAME logs/tasks.
# ---------------------------------------------------------------------------------------------
def fmt_p(p: float) -> str:
    """Canonical p-value string: 3 decimals, or '< 0.001'."""
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def mcnemar_exact(b: int, c: int) -> dict:
    """Two-sided exact McNemar test on the discordant counts of a paired 2x2 table.

    b = pairs positive under arm A only, c = pairs positive under arm B only. Under H0 the b+c
    discordant pairs split Binomial(b+c, 1/2); p = min(1, 2 * P[X <= min(b, c)]). Integer
    arithmetic, no scipy. With b + c = 0 the test is uninformative and p = 1 by convention."""
    if b < 0 or c < 0:
        raise ValueError("counts must be non-negative")
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "discordant": 0, "p_value": 1.0, "p_fmt": fmt_p(1.0),
                "p_sci": "1.0e+00", "note": "no discordant pairs: test uninformative (p = 1 by convention)"}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    p = min(1.0, (2 * tail) / (2 ** n))
    return {"b": b, "c": c, "discordant": n, "p_value": p, "p_fmt": fmt_p(p), "p_sci": f"{p:.1e}",
            "note": None}
