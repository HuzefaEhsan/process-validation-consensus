"""metrics.py -- Component D (D-) evaluation metrics + the OUTPUT-BASED baseline arm.

Thesis framing (locked): the unit of agreement is the *validated reasoning process*
(the structured action log), not the output. This module supplies the experiment-side
measurements for the consensus layer and -- crucially -- the *output-only* baseline that
the process-validation consensus is compared against (the contrast that the framing's
contribution rests on; it only becomes visible under adversaries in H2/H3).

Design constraints honoured here:
  * PURE STDLIB. This module imports nothing from sim_ledger / validation / agents, so it
    cannot accidentally couple the metric layer to the substrate or the predicate. The one
    place semantics MUST match the predicate is value equality (so "37" == 37 == 37.0 for a
    discrete task, exactly as `validation._values_equal` decides it). That equality is
    re-implemented here and the match is *guarded by a parity test* (test_consensus.py
    imports `validation._values_equal` / `_canon_value` and asserts byte-for-byte agreement
    over a battery), rather than by importing validation. If the predicate's canon ever
    drifts, the test fails loudly.
  * NOTHING FABRICATED. `verification_cost` reports operation COUNTS actually performed (no
    invented cost model); detection/FPR return None when undefined (no attacks present)
    instead of a misleading 0; `clustering_stub` REFUSES to score open_ended (raises), since
    the open-ended scorer is co-gated by F2 and designed-but-deferred -- no fake verdict.

Metric surface (all consumed by consensus.run_round):
  weighted_majority, geometric_median, clustering_stub  -- aggregation primitives
  baseline_consensus                                    -- the output-only comparison arm
  detection_rate, false_positive_rate                   -- attack-detection metrics
  consensus_accuracy, comparative_vs_baseline           -- correctness vs ground truth/baseline
  verification_cost                                     -- verification overhead (counts)
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Optional

# A local default mirroring validation.DEFAULT_CONTINUOUS_TOLERANCE. Kept here so the module
# is self-contained; the parity test pins it against the predicate's value.
DEFAULT_CONTINUOUS_TOLERANCE = 1e-9


# ==============================================================================================
# Canonical value equality -- MUST match validation._canon_value / _values_equal.
# (Re-implemented, not imported, to keep this module component-free; parity is test-enforced.)
# ==============================================================================================
def _canon_value(x: Any) -> Any:
    """Canonical comparison form. Numeric strings parse to numbers (int preferred); non-numeric
    strings stay strings; bool stays bool (NOT a number); other objects returned as-is."""
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        s = x.strip()
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            return s
    return x


def _values_equal(a: Any, b: Any, *, task_type: str, tol: float) -> bool:
    """Canonical equality. discrete/other: exact canonical match (37 == 37.0). continuous:
    numeric within absolute tolerance `tol`. Non-numeric: exact canonical match."""
    ca, cb = _canon_value(a), _canon_value(b)
    a_num = isinstance(ca, (int, float)) and not isinstance(ca, bool)
    b_num = isinstance(cb, (int, float)) and not isinstance(cb, bool)
    if a_num and b_num:
        if task_type == "continuous":
            return abs(float(ca) - float(cb)) <= tol
        return ca == cb
    return ca == cb


def _canon_key(x: Any) -> Any:
    """A hashable grouping key for a (possibly numeric-string) value. Numeric values group by
    their float value so "37", 37 and 37.0 land in one bucket; non-numeric by canonical form."""
    c = _canon_value(x)
    if isinstance(c, bool):
        return ("bool", c)
    if isinstance(c, (int, float)):
        return ("num", float(c))
    if isinstance(c, str):
        return ("str", c)
    return ("repr", repr(c))


def _sort_key(x: Any):
    """Deterministic final tie-break: numbers before strings, each ascending."""
    c = _canon_value(x)
    if isinstance(c, bool):
        return (2, str(c))
    if isinstance(c, (int, float)):
        return (0, float(c))
    return (1, str(c))


# ==============================================================================================
# Aggregation primitives (used over VALIDATED outputs, weighted by author reputation)
# ==============================================================================================
def weighted_majority(items: list[tuple[Any, float]]) -> dict:
    """Reputation-weighted plurality for DISCRETE outputs.

    `items` = [(value, weight)]. Values are grouped by canonical equality (so the schema's
    string/number representations of one answer share a bucket). The winner maximises total
    weight; ties break by raw count (the number of agents producing the value -- a Cross-Agent
    Coherence *proxy*, used here ONLY as a tie-break signal, never as a gate), then by a
    deterministic value order. Returns the winning value plus a transparent tally.
    """
    if not items:
        return {"value": None, "weight": 0.0, "n": 0, "tally": [], "tie_broken": False}

    weight_by: dict[Any, float] = defaultdict(float)
    count_by: Counter = Counter()
    repr_by: dict[Any, Any] = {}
    for value, w in items:
        k = _canon_key(value)
        weight_by[k] += float(w)
        count_by[k] += 1
        repr_by.setdefault(k, _canon_value(value))

    # rank: (-weight, -count, sort_key) -> the winner is first
    ranked = sorted(weight_by.keys(),
                    key=lambda k: (-weight_by[k], -count_by[k], _sort_key(repr_by[k])))
    top = ranked[0]
    tie_broken = len(ranked) > 1 and math.isclose(weight_by[ranked[0]], weight_by[ranked[1]])

    tally = [{"value": repr_by[k], "weight": round(weight_by[k], 6), "count": count_by[k]}
             for k in ranked]
    return {"value": repr_by[top], "weight": round(weight_by[top], 6),
            "n": count_by[top], "tally": tally, "tie_broken": tie_broken,
            "method": "reputation_weighted_plurality"}


def geometric_median(items: list[tuple[Any, float]], *, max_iter: int = 256,
                     eps: float = 1e-10) -> dict:
    """Reputation-weighted geometric median for CONTINUOUS outputs (the robust L1 centre).

    Scalars: the weighted median -- the value m minimising sum_i w_i * |x_i - m| -- which is
    attained at a data point (returned as-is). Vectors (list/tuple of equal length): Weiszfeld
    iteration. Robust to a minority of outliers, unlike the (output-)mean a naive baseline
    might use. Returns the centre and the achieved weighted L1 cost.
    """
    pts = [(v, float(w)) for v, w in items if w is not None]
    if not pts:
        return {"value": None, "cost": None, "n": 0, "method": "empty"}

    first = pts[0][0]
    is_vector = isinstance(first, (list, tuple))

    if not is_vector:
        xs = sorted(((float(_canon_value(v)), w) for v, w in pts), key=lambda t: t[0])
        total = sum(w for _, w in xs)
        half, cum, med = total / 2.0, 0.0, xs[-1][0]
        for x, w in xs:
            cum += w
            if cum >= half:
                med = x
                break
        cost = sum(w * abs(x - med) for x, w in xs)
        return {"value": med, "cost": round(cost, 10), "n": len(xs), "method": "weighted_median_1d"}

    # ---- vector case: Weiszfeld ----
    vecs = [([float(c) for c in v], w) for v, w in pts]
    dim = len(vecs[0][0])
    wsum = sum(w for _, w in vecs)
    cur = [sum(w * v[j] for v, w in vecs) / wsum for j in range(dim)]   # weighted centroid seed
    for _ in range(max_iter):
        num = [0.0] * dim
        den = 0.0
        coincide = None
        for v, w in vecs:
            d = math.sqrt(sum((cur[j] - v[j]) ** 2 for j in range(dim)))
            if d < eps:
                coincide = v
                break
            for j in range(dim):
                num[j] += w * v[j] / d
            den += w / d
        if coincide is not None:
            cur = list(coincide)
            break
        nxt = [num[j] / den for j in range(dim)]
        if math.sqrt(sum((nxt[j] - cur[j]) ** 2 for j in range(dim))) < eps:
            cur = nxt
            break
        cur = nxt
    cost = sum(w * math.sqrt(sum((cur[j] - v[j]) ** 2 for j in range(dim))) for v, w in vecs)
    return {"value": cur, "cost": round(cost, 10), "n": len(vecs), "method": "weiszfeld"}


def clustering_stub(items: list[tuple[Any, float]]) -> dict:
    """Aggregation for OPEN_ENDED outputs -- DESIGNED, DEFERRED, and deliberately NOT scored.

    The intended procedure: cluster outputs by an F2 semantic-equivalence oracle (NOT string
    identity), then return the largest reputation-weighted cluster's representative. That oracle
    is co-gated by F2 (open-ended task set + semantic/rubric scorer) and is designed-but-deferred
    in lockstep with the predicate's open_ended branch (which raises `F2-pending`). Fabricating a
    string-identity cluster here would silently change the claim, so this REFUSES rather than
    invent a verdict. Open-ended outputs are excluded from aggregation upstream.
    """
    raise NotImplementedError(
        "open_ended aggregation is co-gated by F2 (semantic-equivalence scorer) and is "
        "designed-but-deferred; refusing to fabricate a clustering. Outputs for open_ended "
        "tasks are excluded from aggregation (see consensus.aggregate_round)."
    )


# ==============================================================================================
# The OUTPUT-BASED baseline arm (the comparison the framing's contribution is measured against)
# ==============================================================================================
def baseline_consensus(outputs_by_task: dict[str, list[Any]],
                       task_types: dict[str, str],
                       *, tolerance: float = DEFAULT_CONTINUOUS_TOLERANCE) -> dict:
    """Output-only consensus: vote on FINAL OUTPUTS alone, with NO process validation and NO
    reputation weighting (every agent's output counts equally). This is the straw-man the
    process-validation consensus must beat: it is blind to *how* an output was produced, so a
    colluder or free-rider whose output looks right is counted the same as an honest agent. For
    all-honest H1 it agrees with the process consensus (both correct) -- the gap opens in H2/H3.

    discrete -> unweighted plurality; continuous -> unweighted geometric median;
    open_ended -> None (deferred, co-gated by F2). Returns {task_id: {value, method, ...}}.
    """
    out: dict[str, dict] = {}
    for task_id, outputs in outputs_by_task.items():
        ttype = task_types.get(task_id, "discrete")
        if not outputs:
            out[task_id] = {"value": None, "method": "empty", "n": 0}
        elif ttype == "open_ended":
            out[task_id] = {"value": None, "method": "deferred_f2", "n": len(outputs)}
        elif ttype == "continuous":
            res = geometric_median([(v, 1.0) for v in outputs])
            out[task_id] = {"value": res["value"], "method": "baseline_geometric_median",
                            "n": res["n"]}
        else:  # discrete
            res = weighted_majority([(v, 1.0) for v in outputs])
            out[task_id] = {"value": res["value"], "method": "baseline_plurality",
                            "n": res["n"], "tally": res["tally"]}
    return out


# ==============================================================================================
# Attack-detection metrics
# ==============================================================================================
def detection_rate(flagged_by_log: dict[str, bool],
                   attack_labels: dict[str, bool]) -> Optional[float]:
    """TPR = (flagged AND attacker) / (attackers). Returns None when there are NO attackers in
    the batch (the quantity is undefined -- reported as None, never a misleading 0). The attack
    labels are HARNESS ground truth (derived from behavior_class), never read from any log."""
    attackers = [lid for lid, atk in attack_labels.items() if atk]
    if not attackers:
        return None
    caught = sum(1 for lid in attackers if flagged_by_log.get(lid, False))
    return caught / len(attackers)


def false_positive_rate(flagged_by_log: dict[str, bool],
                        attack_labels: dict[str, bool]) -> Optional[float]:
    """FPR = (flagged AND honest) / (honest). Returns None if there are NO honest logs. An honest
    minimal-but-valid log being flagged would count here -- the metric that must stay < 5%."""
    honest = [lid for lid, atk in attack_labels.items() if not atk]
    if not honest:
        return None
    fp = sum(1 for lid in honest if flagged_by_log.get(lid, False))
    return fp / len(honest)


# ==============================================================================================
# Correctness vs ground truth + vs the baseline arm
# ==============================================================================================
def consensus_accuracy(consensus_by_task: dict[str, Any],
                       ground_truth: dict[str, Any],
                       task_types: dict[str, str],
                       *, tolerance: float = DEFAULT_CONTINUOUS_TOLERANCE) -> dict:
    """Fraction of SCORED tasks whose consensus value matches ground truth (canonical equality;
    continuous within tolerance). A task whose consensus is None (no agreement / all deferred)
    is NOT scored. Ground truth is a harness label store, never present in any log. Returns
    {accuracy, scored, correct, per_task}; accuracy is None when nothing was scored."""
    per_task = {}
    correct = scored = 0
    for task_id, gt in ground_truth.items():
        cons = consensus_by_task.get(task_id)
        ttype = task_types.get(task_id, "discrete")
        if cons is None:
            per_task[task_id] = {"consensus": None, "ground_truth": gt, "scored": False,
                                 "match": None}
            continue
        tol = tolerance if ttype == "continuous" else 0.0
        match = _values_equal(cons, gt, task_type=ttype, tol=tol)
        per_task[task_id] = {"consensus": cons, "ground_truth": gt, "scored": True,
                             "match": match}
        scored += 1
        correct += int(match)
    accuracy = (correct / scored) if scored else None
    return {"accuracy": accuracy, "scored": scored, "correct": correct, "per_task": per_task}


def comparative_vs_baseline(process_accuracy: Optional[float],
                            baseline_accuracy: Optional[float]) -> dict:
    """Process-validation consensus accuracy vs the output-only baseline. `delta > 0` means the
    process consensus is more accurate (the result the framing predicts under attack). For H1
    both are 1.0 and delta is 0.0 -- the gap is an H2/H3 phenomenon."""
    delta = None
    if process_accuracy is not None and baseline_accuracy is not None:
        delta = process_accuracy - baseline_accuracy
    return {"process": process_accuracy, "baseline": baseline_accuracy, "delta": delta}


def verification_cost(counters: dict[str, int]) -> dict:
    """Verification overhead, reported as the COUNT of operations actually performed (no invented
    cost model). Expects keys like logs / verdicts_computed / commits / reveals / seals /
    peer_set_assignments / schema_rejects / deferred. Adds a couple of honest derived ratios."""
    logs = counters.get("logs", 0)
    validated = counters.get("validated_logs", logs)
    vc = counters.get("verdicts_computed", 0)
    out = dict(counters)
    out["avg_validators_per_log"] = round(vc / logs, 4) if logs else 0.0
    out["avg_validators_per_validated_log"] = round(vc / validated, 4) if validated else 0.0
    return out
