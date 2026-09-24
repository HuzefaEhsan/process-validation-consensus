#!/usr/bin/env python3
"""experiments.py -- P2: offline H1 (all-honest) and H3 (~10% free-riding) on 100 real GSM8K test
problems, with NO LLM in the loop (scripted agents emit logs converted from reference solutions).

Extends the audited prototype ONLY through the existing seams:
  * walking_skeleton.PAYLOAD_GENERATORS  -- new behavior classes registered with setdefault;
  * behavior_plan                        -- which agent runs which class;
and calls the UNCHANGED pipeline consensus.run_round. sim_ledger / agents / validation / consensus /
metrics are imported and never modified (their SHA-256 is checked against AUDITED_SHA256.txt).

Outputs (results/): results.json (authority), RESULTS_SUMMARY.md, h1_baseline.csv, h3_freeriding.csv,
h3_per_variant.csv, robustness.csv, cost.csv, per_log_records.csv, gsm8k_conversion_report.md,
example_logs.json (Fig. 2 candidates).

Run (from the repository root):  python3 src/experiments.py --out results
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata as im
import json
import os
import platform
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import rfc8785

import agents
import consensus
import metrics
import validation
import walking_skeleton as ws
from sim_ledger import SimulatedLedger

import gsm8k_logs as g
import stats_ci as sc

HERE = os.path.dirname(os.path.abspath(__file__))          # src/
ROOT = os.path.dirname(HERE)                               # repository root
DATA_PATH = os.path.join(ROOT, "data", "gsm8k_test.jsonl")

# ---------------------------------------------------------------------------------------------
# Configuration (everything here is recorded in results.json -> meta.config)
# ---------------------------------------------------------------------------------------------
N_TASKS = 100
PRIMARY_N = 30                       # decision 1 (BLUEPRINT D4)
ROBUST_N = [10, 20]
FREE_RIDER_FRACTION = 0.10
FR_PLACEMENTS = [4, 13, 22]          # adversaries.h3_config placements (distinct effort-tier slots)
PRIMARY_SEED = consensus.HARNESS_SEED
SEEDS = [PRIMARY_SEED + i for i in range(5)]
QUICK = os.environ.get("P2_QUICK") == "1"   # DEBUG ONLY: primary seed + primary N; never a reported result
K = consensus.PEER_SET_SIZE
THRESHOLD = consensus.SUPERMAJORITY_THRESHOLD
CROSSOVER_FRACTIONS = [0.10, 0.30, 0.50, 0.60]
CONFIRMATORY_CONF = 0.975            # BLUEPRINT D6 (unsigned): Bonferroni over {H1-FPR, H3-TPR}
DIVERGENT_FRACTION = 0.10            # BLUEPRINT D2: share of logs per task that diverge validly (H1b)
H1B_DIVERGENCE_SEED = 20260629       # fixed; which agents diverge on a task does NOT vary with the harness seed

HONEST_CLASS = "gsm8k/honest"
WRONG_BLOCK_CLASS = "gsm8k/wrong_block"
TIERS = g.EFFORT_TIERS
VARIANTS = g.VARIANTS


H1B_PREFIX = "gsm8k/h1b_"
HONEST_LABELS = {"honest", "honest_divergent"}
H1B_ASSIGN: dict[int, dict[str, frozenset]] = {}      # N -> task_id -> divergent agent indices


def _vkey(v: str):
    """Variant sort key: declared order first, anything else (e.g. the diagnostic wrong_block) after."""
    return (VARIANTS.index(v) if v in VARIANTS else len(VARIANTS), v)


def rr_class(slot: int, n_fr: int) -> str:
    return f"gsm8k/free_rider_rr{slot}of{n_fr}"


def h1b_class(n_agents: int, idx: int) -> str:
    return f"{H1B_PREFIX}n{n_agents}_a{idx}"


def is_honest_class(c: str) -> bool:
    return c == HONEST_CLASS or c.startswith(H1B_PREFIX)


def n_divergent(n_agents: int) -> int:
    return int(round(DIVERGENT_FRACTION * n_agents))


def h1b_assignment(n_agents: int, tasks: list[dict]) -> dict[str, frozenset]:
    """Seeded random 10% of agents per task (BLUEPRINT D2). Seed = sha256(fixed seed | N | task_id),
    so the draw is reproducible, independent across tasks, and independent of the harness seed."""
    out = {}
    for t in tasks:
        h = hashlib.sha256(f"{H1B_DIVERGENCE_SEED}|{n_agents}|{t['task_id']}".encode()).digest()
        out[t["task_id"]] = frozenset(random.Random(int.from_bytes(h[:8], "big"))
                                      .sample(range(n_agents), n_divergent(n_agents)))
    return out


def make_h1b_generator(n_agents: int, idx: int):
    def _gen(effort: str, task: dict) -> str:
        if idx in H1B_ASSIGN[n_agents][task["task_id"]]:
            return g.honest_payload(effort, g.divergent_task(task))
        return g.honest_payload(effort, task)
    return _gen


def n_free_riders(n_agents: int, frac: float = FREE_RIDER_FRACTION) -> int:
    return int(round(frac * n_agents))


def register_generators(max_fr: int = 18, h1b_ns=()) -> None:
    """Seam move 1: register generators (setdefault -> never clobbers an existing class)."""
    ws.PAYLOAD_GENERATORS.setdefault(HONEST_CLASS, g.honest_payload)
    for n_agents in h1b_ns:
        for idx in range(n_agents):
            ws.PAYLOAD_GENERATORS.setdefault(h1b_class(n_agents, idx), make_h1b_generator(n_agents, idx))
    ws.PAYLOAD_GENERATORS.setdefault(WRONG_BLOCK_CLASS, g.free_rider_wrong_block)
    for n_fr in range(1, max_fr + 1):
        for slot in range(n_fr):
            ws.PAYLOAD_GENERATORS.setdefault(rr_class(slot, n_fr),
                                             g.make_round_robin_generator(slot, n_fr))


def behavior_plan(n_agents: int, mode: str, n_fr: int = 0) -> list[str]:
    """Seam move 2: behavior_plan. mode: 'honest' | 'h1b' | 'round_robin' | 'wrong_block'."""
    bp = [HONEST_CLASS] * n_agents
    if mode == "h1b":
        bp = [h1b_class(n_agents, i) for i in range(n_agents)]
    elif mode == "round_robin":
        for slot, idx in enumerate(FR_PLACEMENTS[:n_fr]):
            bp[idx] = rr_class(slot, n_fr)
    elif mode == "wrong_block":
        for i in range(n_fr):                      # first n_fr agents (as adversaries' sweep)
            bp[i] = WRONG_BLOCK_CLASS
    return bp


def behaviour_label(behavior_class: str, task: dict) -> str:
    if behavior_class == HONEST_CLASS:
        return "honest"
    if behavior_class == WRONG_BLOCK_CLASS:
        return "wrong_block"
    if behavior_class.startswith(H1B_PREFIX):
        n_s, a_s = behavior_class[len(H1B_PREFIX):].split("_")
        return ("honest_divergent" if int(a_s[1:]) in H1B_ASSIGN[int(n_s[1:])][task["task_id"]]
                else "honest")
    tail = behavior_class.split("rr", 1)[1]        # "<slot>of<F>"
    slot, n_fr = (int(x) for x in tail.split("of"))
    return g.variant_for(task["position"], slot, n_fr)


# ---------------------------------------------------------------------------------------------
# Build (mirrors adversaries.build_population_pinned; ground truth from the '####' line)
# ---------------------------------------------------------------------------------------------
def build_population(ledger: SimulatedLedger, cfg: ws.ScenarioConfig, answers: dict):
    specs = ws.plan_population(cfg)
    log_recs, meta = [], {}
    for spec in sorted(specs, key=lambda s: s.agent_id):          # deterministic author order
        for j, task in enumerate(cfg.task_set):
            payload = agents.extract_json_object(
                ws.payload_for(spec.behavior_class, spec.effort, task))
            log = agents.assemble_log(
                agent_id=spec.agent_id, model_id=cfg.model_id, task=task,
                reasoning_payload=payload, seed=None, temperature=None, sim_tick=j + 1,
                runtime_ms=g.PINNED_RUNTIME_MS, timestamp=g.PINNED_TIMESTAMP)
            log_id = agents.finalize_and_commit(log, ledger)       # schema gate + content address
            log_recs.append(consensus.LogRec(log_id=log_id, task_id=task["task_id"],
                                             author_id=spec.agent_id))
            label = behaviour_label(spec.behavior_class, task)
            meta[log_id] = {"behaviour": label,
                            "effort_tier": spec.effort if label in HONEST_LABELS else "",
                            "agent_index": spec.index, "gsm8k_index": task["gsm8k_index"],
                            "rule_c_problem": bool(task.get("rule_c", False))}
    label_store = ws.LabelStore(
        ground_truth={t["task_id"]: answers[t["task_id"]] for t in cfg.task_set},
        attack_label={lid: m["behaviour"] not in HONEST_LABELS for lid, m in meta.items()},
        effort_tier={s.agent_id: s.effort for s in specs},
        honesty={s.agent_id: s.behavior_class for s in specs})
    return log_recs, {t["task_id"]: t for t in cfg.task_set}, [s.agent_id for s in specs], \
        label_store, meta, specs


# ---------------------------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------------------------
_GATE_CACHE: dict[str, dict] = {}     # log_id -> C1..C4 (content-addressed => cache is sound)
_REF_CACHE: dict[str, str] = {}       # log_id -> independent single-log predicate decision


def _subchecks(log_id: str, log: dict) -> dict:
    if log_id not in _GATE_CACHE:
        _, t = validation._causal_sufficiency_numeric(log, task_type="discrete", tol=0.0)
        s = t["subchecks"]
        _GATE_CACHE[log_id] = {"C1": s["C1_deterministic_ops_reexecute"],
                               "C2": s["C2_tool_steps_consistent"],
                               "C3": s["C3_output_reproduced_by_terminal"],
                               "C4": s["C4_output_grounded_in_recomputed_op"]}
    return _GATE_CACHE[log_id]


def run_one(name: str, n_agents: int, seed: int, mode: str, n_fr: int, tasks: list[dict],
            answers: dict) -> dict:
    cfg = ws.ScenarioConfig(n_agents=n_agents, model_id=g.SCRIPTED_MODEL_ID,
                            effort_spread=list(TIERS), behavior_plan=behavior_plan(n_agents, mode, n_fr),
                            k_peers=K, supermajority=THRESHOLD, task_set=tasks, harness_seed=seed)
    ledger = SimulatedLedger()
    log_recs, tasks_by_id, agent_ids, ls, meta, specs = build_population(ledger, cfg, answers)
    result = consensus.run_round(
        ledger, log_recs, tasks_by_id, agent_ids,
        ground_truth=ls.ground_truth, attack_labels=ls.attack_label,
        k=cfg.k_peers, threshold=cfg.supermajority, harness_rng=random.Random(seed),
        round_tick=len(tasks), verbose=False)

    # ---- sec 0.7 scans: audited harness scan + extended scan incl. per-log variant labels ----
    scan = ls.assert_invisible(ledger, log_recs)                 # raises on any leak
    labels = (set(ls.honesty.values()) | set(ls.effort_tier.values()) | set(TIERS)
              | set(VARIANTS) | HONEST_LABELS | {"wrong_block", "free_rider", "divergent"})
    fr_agents = {s.agent_id for s in specs if not is_honest_class(s.behavior_class)}
    ext_leaks = sum(1 for r in log_recs
                    if labels.intersection(ws._string_values(ledger.get_log(r.log_id))))

    baseline_by_task = result["baseline_by_task"]
    rows = []
    for rec in log_recs:
        log = ledger.get_log(rec.log_id)
        m = meta[rec.log_id]
        entries = rec.verdicts
        vds = [e["verdict"] for e in entries if e.get("verdict") is not None]
        decisions = {v.decision for v in vds}
        digests = {v.recompute_digest for v in vds}
        rr = vds[0].rule_results if vds else {}
        sub = _subchecks(rec.log_id, log)
        value = log["final_output"]["value"]
        plural = baseline_by_task.get(rec.task_id)
        flagged_base = not metrics._values_equal(value, plural, task_type="discrete", tol=0.0)
        # validator audit: verdicts cast by free-rider agents vs an independent predicate run
        fr_entries = [e for e in entries if e["validator_id"] in fr_agents]
        if rec.log_id not in _REF_CACHE:
            _REF_CACHE[rec.log_id] = validation.validate(log).decision
        ref_dec = _REF_CACHE[rec.log_id]
        rows.append({
            "scenario": name, "N": n_agents, "seed": seed, "task_id": rec.task_id,
            "gsm8k_index": m["gsm8k_index"], "agent_id": rec.author_id,
            "behaviour": m["behaviour"], "effort_tier": m["effort_tier"],
            "is_attack": m["behaviour"] not in HONEST_LABELS, "divergent": m["behaviour"] == "honest_divergent",
            "rule_c_problem": m["rule_c_problem"], "log_id": rec.log_id,
            "final_output": value, "ground_truth_answer": ls.ground_truth[rec.task_id],
            "output_correct": metrics._values_equal(value, ls.ground_truth[rec.task_id],
                                                    task_type="discrete", tol=0.0),
            "gate_process_coherence": rr.get("process_coherence"),
            "gate_tool_utilization": rr.get("tool_utilization"),
            "gate_causal_sufficiency": rr.get("causal_sufficiency"),
            "C1": sub["C1"], "C2": sub["C2"], "C3": sub["C3"], "C4": sub["C4"],
            "predicate_decision": vds[0].decision if len(decisions) == 1 else "mixed",
            "n_verdicts": len(vds),
            "verdicts_equal_reference_predicate": all(v.decision == ref_dec for v in vds),
            "fr_validator_verdicts": len(fr_entries),
            "fr_verdicts_equal_reference_predicate": all(e["verdict"] is not None
                                                         and e["verdict"].decision == ref_dec
                                                         for e in fr_entries),
            "digest_agreement": len(vds) == K and len(digests) == 1 and len(decisions) == 1,
            "consensus_outcome": rec.outcome, "flagged_process": rec.flagged,
            "excluded_from_aggregate": rec.outcome != "accept",
            "baseline_plurality": plural, "flagged_baseline": flagged_base,
            "n_steps": len(log["reasoning_steps"]), "n_tool_ops": len(log["tool_operations"]),
            "canonical_bytes": len(rfc8785.dumps(log)),
            "payload_sha256": hashlib.sha256(rfc8785.dumps(
                {k: log[k] for k in ("reasoning_steps", "tool_operations", "final_output")})
            ).hexdigest(),
        })

    # cross-checks against the audited metric functions (fail loudly on any divergence)
    atk = [r for r in rows if r["is_attack"]]
    hon = [r for r in rows if not r["is_attack"]]
    if atk:
        assert abs(sum(r["flagged_process"] for r in atk) / len(atk) - result["detection_rate"]) < 1e-12
    else:
        assert result["detection_rate"] is None
    assert abs(sum(r["flagged_process"] for r in hon) / len(hon) - result["false_positive_rate"]) < 1e-12

    return {"name": name, "N": n_agents, "seed": seed, "mode": mode, "n_fr": n_fr,
            "rows": rows, "result": result, "ledger": ledger, "specs": specs, "label_store": ls,
            "scan": {"audited_scan_logs": scan["logs_scanned"], "audited_scan_leaks": scan["leaks"],
                     "extended_scan_leaks": ext_leaks, "label_values_checked": sorted(labels)},
            "log_recs": log_recs}


# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------
def P(x, n, confirm=False):
    return sc.prop(x, n, extra_conf=CONFIRMATORY_CONF if confirm else None)


def _count(rows, key):
    return sum(1 for r in rows if r[key])


def _size_stats(rows):
    out = {}
    for f in ("n_steps", "n_tool_ops", "canonical_bytes"):
        v = [r[f] for r in rows]
        out[f] = {"min": min(v), "mean": round(sum(v) / len(v), 4), "max": max(v)} if v else None
    return out


def arm_metrics(rows: list[dict], arm: str, run: dict) -> dict:
    flag = "flagged_process" if arm == "process" else "flagged_baseline"
    atk = [r for r in rows if r["is_attack"]]
    hon = [r for r in rows if not r["is_attack"]]
    res = run["result"]
    acc = res["process_accuracy"] if arm == "process" else res["baseline_accuracy"]
    by_task = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    atk_tasks = [t for t, rs in by_task.items() if any(r["is_attack"] for r in rs)]
    out = {
        "unit_of_analysis": "log (TPR/FPR); task (accuracy, task_level)",
        "detection_tpr": P(_count(atk, flag), len(atk), confirm=True) if atk else None,
        "fpr": P(_count(hon, flag), len(hon), confirm=True),
        "fpr_per_tier": {t: P(_count([r for r in hon if r["effort_tier"] == t], flag),
                              sum(1 for r in hon if r["effort_tier"] == t)) for t in TIERS},
        "consensus_accuracy": P(acc["correct"], acc["scored"]),
        "tasks_unscored": len(res["consensus_by_task"]) - acc["scored"],
        "task_level": {
            "tasks_with_any_honest_log_flagged": P(sum(1 for rs in by_task.values()
                                                        if any(r[flag] for r in rs if not r["is_attack"])),
                                                    len(by_task)),
            "tasks_all_attacker_logs_flagged": (P(sum(1 for t in atk_tasks
                                                      if all(r[flag] for r in by_task[t] if r["is_attack"])),
                                                  len(atk_tasks)) if atk_tasks else None),
        },
    }
    if any(r["divergent"] for r in hon):                     # H1b: honest output divergence
        dv = [r for r in hon if r["divergent"]]
        nd = [r for r in hon if not r["divergent"]]
        out["fpr_divergence_split"] = {
            "divergent_logs": P(_count(dv, flag), len(dv)),
            "non_divergent_logs": P(_count(nd, flag), len(nd)),
            "tasks_with_any_divergent_log_flagged": P(
                sum(1 for rs in by_task.values() if any(r[flag] for r in rs if r["divergent"])), len(by_task)),
            "divergent_logs_output_correct": sum(1 for r in dv if r["output_correct"])}
    if any(r["rule_c_problem"] for r in hon):                # H1-U: unfiltered by rule (c)
        rc = [r for r in hon if r["rule_c_problem"]]
        oc = [r for r in hon if not r["rule_c_problem"]]
        rc_tasks = sorted({r["task_id"] for r in rc})
        out["fpr_rule_c_split"] = {
            "logs_on_rule_c_problems": P(_count(rc, flag), len(rc)),
            "logs_on_other_problems": P(_count(oc, flag), len(oc)),
            "rule_c_problems_with_any_log_flagged": P(
                sum(1 for t in rc_tasks if any(r[flag] for r in by_task[t])), len(rc_tasks)),
            "per_tier_on_rule_c_problems": {t: P(_count([r for r in rc if r["effort_tier"] == t], flag),
                                                 sum(1 for r in rc if r["effort_tier"] == t)) for t in TIERS}}
    if atk:
        out["detection_per_variant"] = {}
        for v in sorted({r["behaviour"] for r in atk}, key=_vkey):
            vr = [r for r in atk if r["behaviour"] == v]
            vt = sorted({r["task_id"] for r in vr})
            out["detection_per_variant"][v] = {
                "logs": P(_count(vr, flag), len(vr)),
                "tasks_all_logs_of_variant_flagged": P(
                    sum(1 for t in vt if all(r[flag] for r in vr if r["task_id"] == t)), len(vt)),
                "output_correct_logs": sum(1 for r in vr if r["output_correct"]),
            }
    return out


def process_only_metrics(run: dict) -> dict:
    rows, res, ledger = run["rows"], run["result"], run["ledger"]
    atk = [r for r in rows if r["is_attack"]]
    hon = [r for r in rows if not r["is_attack"]]
    flagged = [r for r in rows if r["flagged_process"]]
    out = {"digest_agreement": P(_count(rows, "digest_agreement"), len(rows)),
           "exclusion": P(sum(1 for r in flagged if r["excluded_from_aggregate"]), len(flagged))
           if flagged else None,
           "outcomes": dict(Counter(r["consensus_outcome"] for r in rows)),
           "honest_gate_failures": {gname: sum(1 for r in hon if r[gname] is False)
                                    for gname in ("gate_process_coherence", "gate_tool_utilization",
                                                  "gate_causal_sufficiency", "C1", "C2", "C3", "C4")}}
    if atk:
        out["gate_attribution_per_variant"] = {}
        for v in sorted({r["behaviour"] for r in atk}, key=_vkey):
            vr = [r for r in atk if r["behaviour"] == v]
            rej = [r for r in vr if r["predicate_decision"] == "reject"]
            att = {gname: sum(1 for r in rej if r[gname] is False)
                   for gname in ("gate_process_coherence", "gate_tool_utilization",
                                 "gate_causal_sufficiency", "C1", "C2", "C3", "C4")}
            intended = g.INTENDED_GATE.get(v)
            key = {"C1": "C1", "C4": "C4", "tool_utilization": "gate_tool_utilization"}.get(intended)
            out["gate_attribution_per_variant"][v] = {
                "rejected_logs": len(rej), "failed_gate_counts": att,
                "intended_gate": intended,
                "intended_gate_failed": P(sum(1 for r in rej if key and r[key] is False), len(vr))
                if key else None,
                "exclusion": P(sum(1 for r in vr if r["flagged_process"] and r["excluded_from_aggregate"]),
                               sum(1 for r in vr if r["flagged_process"])) if any(r["flagged_process"] for r in vr) else None}

    # validator audit (BLUEPRINT 4.3 "[Confirm current code behaviour]")
    fr_ids_all = {s.agent_id for s in run["specs"] if not is_honest_class(s.behavior_class)}
    fr_v = [r for r in rows if r["fr_validator_verdicts"]]
    n_verdicts = sum(r["n_verdicts"] for r in rows)
    out["validators"] = {
        "peer_set_pool": "all agents except the log's author (consensus.assign_peer_sets); free-rider agents are eligible",
        "verdict_function": ("consensus.compute_log_verdicts calls validation.validate_committed for every "
                             "validator; the validator id is a label on the Verdict, never an input to the "
                             "decision; consensus has no access to behavior_class"),
        "free_rider_agents": len(fr_ids_all),
        "verdicts_total": n_verdicts,
        "verdicts_cast_by_free_rider_agents": sum(r["fr_validator_verdicts"] for r in rows),
        "logs_with_a_free_rider_validator": len(fr_v),
        "attacker_logs_with_a_free_rider_validator": sum(1 for r in fr_v if r["is_attack"]),
        "logs_whose_free_rider_verdicts_equal_reference_predicate": (
            P(sum(1 for r in fr_v if r["fr_verdicts_equal_reference_predicate"]), len(fr_v)) if fr_v else None),
        "logs_whose_verdicts_all_equal_reference_predicate": P(_count(rows, "verdicts_equal_reference_predicate"),
                                                               len(rows)),
        "reference_predicate": "validation.validate(log) run once per log outside the protocol",
    }

    # reputation
    reps = res["final_reputation"]
    cls_of = {s.agent_id: ("honest" if is_honest_class(s.behavior_class) else "free_rider")
              for s in run["specs"]}
    honest_ids = [a for a, c in cls_of.items() if c == "honest"]
    fr_ids = [a for a, c in cls_of.items() if c != "honest"]
    by_author = defaultdict(set)
    for r in rows:
        by_author[r["agent_id"]].add(r["consensus_outcome"])
    uniform = all(len(s) == 1 for s in by_author.values())

    def after(aid, j):
        h = ledger.get_reputation_history(aid)
        return h[j]["value"] if len(h) > j else None

    rep = {"r0": consensus.REP_DEFAULT, "alpha": consensus.REP_ALPHA_UP, "beta": consensus.REP_BETA_DOWN,
           "floor": consensus.REP_FLOOR, "ceil": consensus.REP_CEIL,
           "updates_per_agent": len(rows) // len(reps),
           "uniform_update_sign_per_agent": uniform,
           "honest_final": {"mean": sum(reps[a] for a in honest_ids) / len(honest_ids),
                            "min": min(reps[a] for a in honest_ids),
                            "max": max(reps[a] for a in honest_ids)},
           "honest_after_3_updates": sorted({after(a, 3) for a in honest_ids}),
           "note": ("single batch-lockstep round: aggregation reads round-start reputations (all r0), "
                    "so weights are uniform in aggregation; reputation moves only at seal time. "
                    "Updates per agent are applied in log_id order; with a uniform sign per agent the "
                    "value after j updates is order-independent.")}
    if fr_ids:
        rep["free_rider_final"] = {a: reps[a] for a in sorted(fr_ids)}
        rep["free_rider_after_3_updates"] = sorted({after(a, 3) for a in fr_ids})
        rep["separation_min_honest_minus_max_free_rider"] = (min(reps[a] for a in honest_ids)
                                                              - max(reps[a] for a in fr_ids))
    out["reputation"] = rep

    # cost
    chain = ledger.get_history()
    by_type = dict(Counter(tx["tx_type"] for tx in chain))
    cost = dict(res["verification_cost"])
    n = cost["logs"]
    rep_updates = sum(1 for tx in chain if tx["tx_type"] == "reputation_set"
                      and tx["payload"]["reason_ref"] != "genesis-init")
    protocol_tx = (by_type.get("peer_set", 0) + by_type.get("verdict_commit", 0)
                   + by_type.get("verdict_reveal", 0) + by_type.get("verdict_seal", 0) + rep_updates)
    out["cost"] = {
        "counters": cost,
        "ledger_tx_by_type": by_type, "ledger_tx_total": len(chain),
        "reputation_updates": rep_updates,
        "per_log": {"verdicts": cost["verdicts_computed"] / n, "commits": cost["commits"] / n,
                    "reveals": cost["reveals"] / n, "seals": cost["seals"] / n,
                    "peer_set_records": cost["peer_set_assignments"] / n,
                    "protocol_ledger_tx": protocol_tx / n, "all_ledger_tx": len(chain) / n},
        "walking_skeleton_overhead_ops": (cost["peer_set_assignments"] + cost["verdicts_computed"]
                                          + cost["commits"] + cost["reveals"] + cost["seals"]),
        "walking_skeleton_overhead_formula": "peer_set_assignments + verdicts_computed + commits + reveals + seals",
        "baseline_validation_ops": 0,
        "note": "counts, not latency; verdicts are computed off-ledger, commits/reveals/seals/"
                "peer-set records/reputation writes are ledger transactions",
    }
    out["log_size"] = {
        "per_tier": {t: _size_stats([r for r in hon if r["effort_tier"] == t]) for t in TIERS},
        "per_variant": {v: _size_stats([r for r in atk if r["behaviour"] == v])
                        for v in sorted({r["behaviour"] for r in atk}, key=_vkey)},
        "divergent_honest": _size_stats([r for r in hon if r["divergent"]]) if any(r["divergent"] for r in hon) else None,
    }
    # the +0.0 pp arithmetic: per task, correct-output votes vs the largest wrong-output bloc
    by_task = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    correct_votes = [sum(1 for r in rs if r["output_correct"]) for rs in by_task.values()]
    wrong_blocs = []
    for rs in by_task.values():
        c = Counter(str(metrics._canon_key(r["final_output"])) for r in rs if not r["output_correct"])
        wrong_blocs.append(max(c.values()) if c else 0)
    out["vote_arithmetic"] = {"min_correct_output_logs_per_task": min(correct_votes),
                              "max_largest_wrong_output_bloc_per_task": max(wrong_blocs),
                              "min_honest_logs_per_task": min(sum(1 for r in rs if not r["is_attack"])
                                                              for rs in by_task.values()),
                              "max_attacker_logs_per_task": max(sum(1 for r in rs if r["is_attack"])
                                                                for rs in by_task.values())}
    out["integrity"] = {"ledger_verify": res["ledger_ok"], **run["scan"],
                        "log_id_set_sha256": hashlib.sha256(
                            "\n".join(sorted(r["log_id"] for r in rows)).encode()).hexdigest()}
    return out


def _paired(a_flags: list[bool], b_flags: list[bool]) -> dict:
    """Paired comparison of arm A (process) vs arm B (output-only) on the same units."""
    n = len(a_flags)
    b = sum(1 for x, y in zip(a_flags, b_flags) if x and not y)
    c = sum(1 for x, y in zip(a_flags, b_flags) if y and not x)
    return {"n": n, "process_positive": sum(a_flags), "baseline_positive": sum(b_flags),
            "delta_pp": 100.0 * (sum(a_flags) - sum(b_flags)) / n if n else None,
            "discordant_process_only": b, "discordant_baseline_only": c,
            "mcnemar_exact": sc.mcnemar_exact(b, c)}


def comparative(proc: dict, base: dict, rows: list[dict], run: dict) -> dict:
    """Both arms judge the SAME logs and tasks, so every comparison is paired: McNemar's exact test
    on the discordant pairs (replaces BLUEPRINT 4.6's Newcombe interval, which assumes independent
    samples). Log-level pairs share a task (and tier-identical payloads), so log-level p-values are
    optimistic; the task-level test is the conservative unit."""
    atk = [r for r in rows if r["is_attack"]]
    hon = [r for r in rows if not r["is_attack"]]
    res = run["result"]
    gt = run["label_store"].ground_truth
    tids = sorted(res["consensus_by_task"])

    def correct(v, t):
        return v is not None and metrics._values_equal(v, gt[t], task_type="discrete", tol=0.0)

    by_task = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    out = {"accuracy_delta_pp": 100.0 * (proc["consensus_accuracy"]["p"] - base["consensus_accuracy"]["p"]),
           "accuracy_tasks": _paired([correct(res["consensus_by_task"][t], t) for t in tids],
                                     [correct(res["baseline_by_task"].get(t), t) for t in tids]),
           "fpr_logs": _paired([r["flagged_process"] for r in hon], [r["flagged_baseline"] for r in hon]),
           "fpr_tasks_any_honest_flagged": _paired(
               [any(r["flagged_process"] for r in rs if not r["is_attack"]) for rs in by_task.values()],
               [any(r["flagged_baseline"] for r in rs if not r["is_attack"]) for rs in by_task.values()])}
    if any(r["divergent"] for r in hon):
        dv = [r for r in hon if r["divergent"]]
        out["fpr_divergent_logs"] = _paired([r["flagged_process"] for r in dv], [r["flagged_baseline"] for r in dv])
    if atk:
        out["detection_pooled"] = _paired([r["flagged_process"] for r in atk], [r["flagged_baseline"] for r in atk])
        atk_tasks = [t for t, rs in by_task.items() if any(r["is_attack"] for r in rs)]
        out["detection_tasks_all_attackers_flagged"] = _paired(
            [all(r["flagged_process"] for r in by_task[t] if r["is_attack"]) for t in atk_tasks],
            [all(r["flagged_baseline"] for r in by_task[t] if r["is_attack"]) for t in atk_tasks])
        out["detection_per_variant"] = {}
        for v in sorted({r["behaviour"] for r in atk}, key=_vkey):
            vr = [r for r in atk if r["behaviour"] == v]
            vt = sorted({r["task_id"] for r in vr})
            out["detection_per_variant"][v] = {
                "logs": _paired([r["flagged_process"] for r in vr], [r["flagged_baseline"] for r in vr]),
                "tasks": _paired([all(r["flagged_process"] for r in vr if r["task_id"] == t) for t in vt],
                                 [all(r["flagged_baseline"] for r in vr if r["task_id"] == t) for t in vt])}
    out["test_note"] = ("McNemar exact two-sided test on discordant pairs (b = process-only positive, "
                        "c = output-only-only positive); replaces the independent-sample Newcombe interval "
                        "(BLUEPRINT 4.6). Log-level units are not independent within a task, so log-level "
                        "p-values are optimistic; task-level tests are the conservative unit. With b + c = 0 "
                        "the test is uninformative (p = 1 by convention).")
    return out


def scenario_block(run: dict) -> dict:
    proc = arm_metrics(run["rows"], "process", run)
    base = arm_metrics(run["rows"], "baseline", run)
    return {"N": run["N"], "seed": run["seed"], "n_free_riders": run["n_fr"],
            "free_rider_share": run["n_fr"] / run["N"],
            "logs": len(run["rows"]), "attacker_logs": sum(r["is_attack"] for r in run["rows"]),
            "process": {**proc, **process_only_metrics(run)}, "baseline": base,
            "comparative": comparative(proc, base, run["rows"], run)}


def seed_summary(block: dict) -> dict:
    pr, ba = block["process"], block["baseline"]
    s = {"process_tpr": pr["detection_tpr"]["p"] if pr["detection_tpr"] else None,
         "process_fpr": pr["fpr"]["p"], "process_accuracy": pr["consensus_accuracy"]["p"],
         "baseline_tpr": ba["detection_tpr"]["p"] if ba["detection_tpr"] else None,
         "baseline_fpr": ba["fpr"]["p"], "baseline_accuracy": ba["consensus_accuracy"]["p"],
         "digest_agreement": pr["digest_agreement"]["p"], "ledger_verify": pr["integrity"]["ledger_verify"],
         "label_leaks": pr["integrity"]["audited_scan_leaks"] + pr["integrity"]["extended_scan_leaks"],
         "honest_rep_mean": pr["reputation"]["honest_final"]["mean"],
         "free_rider_rep_max": (max(pr["reputation"]["free_rider_final"].values())
                                if "free_rider_final" in pr["reputation"] else None)}
    for v, d in (pr.get("detection_per_variant") or {}).items():
        s[f"process_tpr_{v}"] = d["logs"]["p"]
    for v, d in (ba.get("detection_per_variant") or {}).items():
        s[f"baseline_tpr_{v}"] = d["logs"]["p"]
    if "fpr_divergence_split" in pr:
        s["process_fpr_divergent"] = pr["fpr_divergence_split"]["divergent_logs"]["p"]
        s["baseline_fpr_divergent"] = ba["fpr_divergence_split"]["divergent_logs"]["p"]
    if "fpr_rule_c_split" in pr:
        s["process_fpr_rule_c_problems"] = pr["fpr_rule_c_split"]["logs_on_rule_c_problems"]["p"]
    return s


# ---------------------------------------------------------------------------------------------
# Constructed illustration + sensitivity + out-of-scope diagnostic
# ---------------------------------------------------------------------------------------------
def determinism_gap_illustration(run: dict) -> dict:
    """Exact-artifact agreement rules on all-honest logs (H1 or H1b) vs the predicate.
    CONSTRUCTED ILLUSTRATION."""
    by_task = defaultdict(list)
    for r in run["rows"]:
        by_task[r["task_id"]].append(r)
    out = {}
    for rule, key in (("full_log_content_hash", "log_id"), ("reasoning_payload_hash", "payload_sha256")):
        flagged = distinct = ties = 0
        plur_sizes = []
        for rs in by_task.values():
            wm = metrics.weighted_majority([("h:" + r[key], 1.0) for r in rs])   # 'h:' keeps hashes non-numeric
            flagged += sum(1 for r in rs if "h:" + r[key] != wm["value"])
            distinct += len(wm["tally"])
            ties += int(wm["tie_broken"])
            plur_sizes.append(wm["n"])
        out[rule] = {"flagged_honest_logs": P(flagged, len(run["rows"])),
                     "mean_distinct_artifacts_per_task": distinct / len(by_task),
                     "plurality_size_min_max": [min(plur_sizes), max(plur_sizes)],
                     "tasks_with_tied_plurality": ties}
    out["predicate_fpr"] = P(_count(run["rows"], "flagged_process"), len(run["rows"]))
    out["output_plurality_fpr"] = P(_count(run["rows"], "flagged_baseline"), len(run["rows"]))
    div = sum(1 for r in run["rows"] if r["divergent"])
    source = ("the five effort tiers (agents of one tier emit byte-identical payloads)" if not div else
              f"the five effort tiers plus {div} validly derived divergent-output logs "
              f"({n_divergent(run['N'])} of {run['N']} per task, BLUEPRINT D2)")
    out["divergent_logs"] = div
    out["label"] = ("CONSTRUCTED ILLUSTRATION, not a measurement: the only honest divergence in this "
                    f"scripted population is {source}. full_log_content_hash is the literal rule (every log "
                    "embeds its author DID, so every hash is unique); reasoning_payload_hash compares "
                    "reasoning_steps + tool_operations + final_output only. Ties are broken by "
                    "metrics.weighted_majority's deterministic order.")
    return out


AB_RULES = {g.R_NO_SOLUTION_ANSWER, g.R_NO_ANN, g.R_MALFORMED, g.R_NOT_EXECUTABLE, g.R_MISMATCH,
            g.R_ANSWER_NE_LAST}
GATE_KEYS = ("process_coherence", "tool_utilization", "causal_sufficiency", "C1", "C2", "C3", "C4")


def pass_ab(problems: list[g.Problem]) -> list[g.Problem]:
    """Problems satisfying ONLY the two requested eligibility rules (a) and (b)."""
    return [p for p in problems if not (set(p.reasons) & AB_RULES)]


def rule_c_unfiltered(problems: list[g.Problem]) -> dict:
    """First-class output (decision 5): honest-log FPR of the predicate on FAITHFUL reference
    derivations NOT filtered by the added rule (c), i.e. every problem passing rules (a)+(b), all five
    tiers. Unit = distinct faithful log (problem x tier); tier-identical agents would only replicate
    it. Rule (d) guarantees acceptance of all tiers on problems passing (a)-(c), so every rejection
    here comes from a rule-(c) problem -- the code checks this rather than assuming it."""
    ab = pass_ab(problems)
    rejected_logs, per_tier, gate_fail = 0, {t: 0 for t in TIERS}, Counter()
    per_problem, prob_rej = [], 0
    for p in ab:
        d = g.tier_decisions(p)
        rej = [t for t in TIERS if d[t]["decision"] != "accept"]
        if rej and g.R_INCOMPLETE not in p.reasons:
            raise AssertionError(f"problem {p.index}: faithful tier rejected without failing rule (c)")
        if not rej:
            continue
        prob_rej += 1
        rejected_logs += len(rej)
        for t in rej:
            per_tier[t] += 1
            for k in GATE_KEYS:
                if d[t][k] is False:
                    gate_fail[k] += 1
        det = next(x for x in p.details if x["rule"] == g.R_INCOMPLETE)
        failed_by_tier = {t: [k for k in GATE_KEYS if d[t][k] is False] for t in rej}
        patterns = {tuple(v) for v in failed_by_tier.values()}
        per_problem.append({"gsm8k_index": p.index, "annotations": len(p.annotations),
                            "orphan_steps": det["steps_outside_answer_cone"],
                            "identity_annotation": det["has_identity_annotation"],
                            "tiers_rejected": rej, "tiers_accepted": [t for t in TIERS if t not in rej],
                            "failed_gates_by_tier": failed_by_tier,
                            "same_gate_pattern_in_every_rejected_tier": len(patterns) == 1,
                            "failed_gates": sorted({k for v in failed_by_tier.values() for k in v},
                                                   key=GATE_KEYS.index)})
    inc = [p for p in ab if g.R_INCOMPLETE in p.reasons]
    n_logs = len(ab) * len(TIERS)
    return {
        "question": "honest-log FPR of the predicate on faithful reference derivations not filtered by rule (c)",
        "population": ("every GSM8K test problem that satisfies the two requested rules (a)+(b); five honest "
                       "tiers each; one faithful log per (problem, tier)"),
        "problems": len(ab), "logs": n_logs,
        "fpr_logs": P(rejected_logs, n_logs),
        "fpr_per_tier": {t: P(per_tier[t], len(ab)) for t in TIERS},
        "problems_with_any_tier_rejected": P(prob_rej, len(ab)),
        "problems_failing_rule_c": P(len(inc), len(ab)),
        "rule_c_problems_with_identity_annotation": sum(
            1 for p in inc for x in p.details if x["rule"] == g.R_INCOMPLETE and x["has_identity_annotation"]),
        "rejections_all_on_rule_c_problems": True,
        "gate_failures_over_rejected_logs": {k: gate_fail[k] for k in GATE_KEYS},
        "rejected_logs": rejected_logs,
        "gate_patterns": dict(Counter(" + ".join(pp["failed_gates"]) for pp in per_problem)),
        "per_problem": per_problem,
        "not_evaluated": ("problems failing rule (a) (value mismatch / not executable) or rule (b) (answer "
                          "not the last calculator result) were NOT converted or evaluated: a faithful log "
                          "for them is not uniquely defined, so this FPR covers only problems passing (a)+(b)"),
    }


def model_id_invariance(run: dict, alt_ids=("llama3.1:8b", "any-other-model")) -> dict:
    """Seminar comment (a): re-validate every log of a run with model_id replaced; decision, rule
    results and recompute digest must be identical (log_id left unchanged on purpose: only the
    model_id FIELD varies)."""
    ledger = run["ledger"]
    n = same = 0
    for rec in run["log_recs"]:
        log = ledger.get_log(rec.log_id)
        v0 = validation.validate(log)
        for m in alt_ids:
            v1 = validation.validate({**log, "model_id": m})
            n += 1
            same += int(v1.decision == v0.decision and v1.rule_results == v0.rule_results
                        and v1.recompute_digest == v0.recompute_digest)
    return {"scenario": run["name"], "N": run["N"], "seed": run["seed"], "logs": len(run["log_recs"]),
            "alternative_model_ids": list(alt_ids), "revalidations": n,
            "identical_decision_rules_and_digest": P(same, n)}


# ---------------------------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------------------------
def pct(p):
    return "n/a" if p is None else f"{100 * p:.1f}%"


def cif(ci):
    return "n/a" if ci is None else f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def ppf(d):
    return "n/a" if d is None else f"{(0.0 if abs(d) < 5e-4 else d):+.1f} pp"


def repf(r):
    return "n/a" if r is None else f"{r:.4f}"


def add_fmt(obj):
    """Attach canonical formatted strings to every proportion record (so every consumer uses the
    SAME rounding)."""
    if isinstance(obj, dict):
        if {"x", "n", "p", "wilson95", "cp95"} <= set(obj):
            obj["fmt"] = {"p": pct(obj["p"]), "x_n": f"{obj['x']}/{obj['n']}",
                          "wilson95": cif(obj["wilson95"]), "cp95": cif(obj["cp95"])}
            for k in list(obj):
                if k.startswith(("wilson97", "cp97")):
                    obj["fmt"][k] = cif(obj[k])
        for v in obj.values():
            add_fmt(v)
    elif isinstance(obj, list):
        for v in obj:
            add_fmt(v)
    return obj


def prop_line(label, rec):
    if rec is None:
        return f"| {label} | n/a | n/a | n/a | n/a |"
    return (f"| {label} | {pct(rec['p'])} | {rec['x']}/{rec['n']} | {cif(rec['wilson95'])} | "
            f"{cif(rec['cp95'])} |")


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def prop_rows(scenario, N, seed, arm, metric, group, rec):
    base = {"scenario": scenario, "N": N, "seed": seed, "arm": arm, "metric": metric, "group": group}
    if rec is None:
        return base
    return {**base, "x": rec["x"], "n": rec["n"], "p": f"{rec['p']:.6f}" if rec["p"] is not None else "",
            "p_fmt": pct(rec["p"]),
            "wilson95_lo": f"{rec['wilson95'][0]:.6f}", "wilson95_hi": f"{rec['wilson95'][1]:.6f}",
            "cp95_lo": f"{rec['cp95'][0]:.6f}", "cp95_hi": f"{rec['cp95'][1]:.6f}",
            "wilson95_fmt": cif(rec["wilson95"]), "cp95_fmt": cif(rec["cp95"])}


PROP_FIELDS = ["scenario", "N", "seed", "arm", "metric", "group", "x", "n", "p", "p_fmt",
               "wilson95_lo", "wilson95_hi", "cp95_lo", "cp95_hi", "wilson95_fmt", "cp95_fmt"]


def block_prop_rows(scen, blk):
    out = []
    N, seed = blk["N"], blk["seed"]
    for arm in ("process", "baseline"):
        a = blk[arm]
        out.append(prop_rows(scen, N, seed, arm, "consensus_accuracy", "all_tasks", a["consensus_accuracy"]))
        out.append(prop_rows(scen, N, seed, arm, "fpr", "all_honest", a["fpr"]))
        for t in TIERS:
            out.append(prop_rows(scen, N, seed, arm, "fpr", f"tier:{t}", a["fpr_per_tier"][t]))
        if a["detection_tpr"]:
            out.append(prop_rows(scen, N, seed, arm, "detection_tpr", "all_attackers", a["detection_tpr"]))
        out.append(prop_rows(scen, N, seed, arm, "task_level_any_honest_flagged", "tasks",
                             a["task_level"]["tasks_with_any_honest_log_flagged"]))
        if a["task_level"]["tasks_all_attacker_logs_flagged"]:
            out.append(prop_rows(scen, N, seed, arm, "task_level_all_attackers_flagged", "tasks",
                                 a["task_level"]["tasks_all_attacker_logs_flagged"]))
    for arm in ("process", "baseline"):
        a = blk[arm]
        if "fpr_divergence_split" in a:
            ds = a["fpr_divergence_split"]
            out.append(prop_rows(scen, N, seed, arm, "fpr", "divergent_logs", ds["divergent_logs"]))
            out.append(prop_rows(scen, N, seed, arm, "fpr", "non_divergent_logs", ds["non_divergent_logs"]))
            out.append(prop_rows(scen, N, seed, arm, "task_level_any_divergent_flagged", "tasks",
                                 ds["tasks_with_any_divergent_log_flagged"]))
        if "fpr_rule_c_split" in a:
            rs = a["fpr_rule_c_split"]
            out.append(prop_rows(scen, N, seed, arm, "fpr", "logs_on_rule_c_problems", rs["logs_on_rule_c_problems"]))
            out.append(prop_rows(scen, N, seed, arm, "fpr", "logs_on_other_problems", rs["logs_on_other_problems"]))
            out.append(prop_rows(scen, N, seed, arm, "task_level_any_flagged", "rule_c_problems",
                                 rs["rule_c_problems_with_any_log_flagged"]))
            for t in TIERS:
                out.append(prop_rows(scen, N, seed, arm, "fpr_on_rule_c_problems", f"tier:{t}",
                                     rs["per_tier_on_rule_c_problems"][t]))
    out.append(prop_rows(scen, N, seed, "process", "digest_agreement", "all_logs",
                         blk["process"]["digest_agreement"]))
    if blk["process"].get("exclusion"):
        out.append(prop_rows(scen, N, seed, "process", "exclusion", "flagged_logs", blk["process"]["exclusion"]))
    return out


LOG_FIELDS = ["scenario", "N", "seed", "task_id", "gsm8k_index", "agent_id", "behaviour", "effort_tier",
              "is_attack", "divergent", "rule_c_problem", "log_id", "final_output", "ground_truth_answer", "output_correct",
              "gate_process_coherence", "gate_tool_utilization", "gate_causal_sufficiency",
              "C1", "C2", "C3", "C4", "predicate_decision", "n_verdicts", "verdicts_equal_reference_predicate",
              "fr_validator_verdicts", "fr_verdicts_equal_reference_predicate", "digest_agreement",
              "consensus_outcome", "flagged_process", "excluded_from_aggregate", "baseline_plurality",
              "flagged_baseline", "n_steps", "n_tool_ops", "canonical_bytes", "payload_sha256"]


def lib_versions():
    out = {"python": platform.python_version()}
    for pkg in ("rfc8785", "Mesa", "pytest", "langchain-ollama", "langchain-core"):
        try:
            out[pkg] = im.version(pkg)
        except im.PackageNotFoundError:
            out[pkg] = None
    return out


def source_hashes():
    # audit fix D1: hash source and pinned inputs only; never a generated log (run_log_final.txt is
    # overwritten by the run itself, which made results.json irreproducible from the package).
    # Repository layout: the same rule applied to src/, tests/, audit/, scripts/ and the root;
    # keys are paths relative to the repository root.
    files = []
    for d in ("", "src", "tests", "audit", "scripts"):
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        files += [os.path.join(d, f) if d else f for f in os.listdir(full)
                  if os.path.isfile(os.path.join(full, f))
                  and (f.endswith((".py", ".sh")) or f in ("AUDITED_SHA256.txt", "requirements.txt"))]
    return {f.replace(os.sep, "/"): g.file_sha256(os.path.join(ROOT, f)) for f in sorted(files)}


def audited_unmodified():
    path = os.path.join(HERE, "AUDITED_SHA256.txt")
    ok = {}
    with open(path) as f:
        for line in f:
            h, name = line.split()
            ok[name] = g.file_sha256(os.path.join(HERE, name)) == h
    return ok


def mcnemar_rows(scen, N, seed, cmp_):
    out = []

    def add(comparison, unit, d):
        m = d["mcnemar_exact"]
        out.append({"scenario": scen, "N": N, "seed": seed, "comparison": comparison, "unit": unit,
                    "n": d["n"], "process_positive": d["process_positive"],
                    "baseline_positive": d["baseline_positive"],
                    "delta_pp": "" if d["delta_pp"] is None else f"{d['delta_pp']:.4f}",
                    "b_process_only": m["b"], "c_baseline_only": m["c"], "discordant": m["discordant"],
                    "p_value": f"{m['p_value']:.6g}", "p_fmt": m["p_fmt"], "p_sci": m["p_sci"]})

    add("accuracy_correct", "task", cmp_["accuracy_tasks"])
    add("fpr_flagged_honest", "log", cmp_["fpr_logs"])
    add("fpr_any_honest_flagged", "task", cmp_["fpr_tasks_any_honest_flagged"])
    if "fpr_divergent_logs" in cmp_:
        add("fpr_flagged_divergent_honest", "log", cmp_["fpr_divergent_logs"])
    if "detection_pooled" in cmp_:
        add("detection_flagged_attacker", "log", cmp_["detection_pooled"])
        add("detection_all_attackers_flagged", "task", cmp_["detection_tasks_all_attackers_flagged"])
        for v, d in cmp_["detection_per_variant"].items():
            add(f"detection:{v}", "log", d["logs"])
            add(f"detection:{v}", "task", d["tasks"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "results"))
    ap.add_argument("--data", default=DATA_PATH)
    args = ap.parse_args(argv)
    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)

    audited = audited_unmodified()
    if not all(audited.values()):
        raise SystemExit(f"audited modules modified: {audited}")

    # ---- dataset + conversion ----------------------------------------------------------------
    rows = g.load_rows(args.data)
    conv = g.convert(rows, N_TASKS)
    selected = conv["selected"]
    tasks = [g.attach_divergence(g.make_task(p, i)) for i, p in enumerate(selected)]
    missing_div = [t["task_id"] for t in tasks if t["divergent_reference"] is None]
    if missing_div:
        raise SystemExit(f"no valid divergent derivation for {missing_div}")
    answers = {p.task_id: p.answer for p in selected}
    # H1-U: first N_TASKS problems passing ONLY rules (a)+(b) (rule (c) not applied)
    ab = pass_ab(conv["problems"])[:N_TASKS]
    tasks_u = []
    for i, p in enumerate(ab):
        t = g.make_task(p, i)
        t["rule_c"] = g.R_INCOMPLETE in p.reasons
        tasks_u.append(t)
    answers_u = {p.task_id: p.answer for p in ab}
    global SEEDS, ROBUST_N
    if QUICK:
        SEEDS, ROBUST_N = [PRIMARY_SEED], []
    all_ns = [PRIMARY_N] + ROBUST_N
    for n_agents in all_ns:
        H1B_ASSIGN[n_agents] = h1b_assignment(n_agents, tasks)
    register_generators(h1b_ns=all_ns)

    # ---- runs ------------------------------------------------------------------------------------
    plan = [("H1", "honest", tasks, answers, all_ns), ("H1b", "h1b", tasks, answers, all_ns),
            ("H3", "round_robin", tasks, answers, all_ns), ("H1U", "honest", tasks_u, answers_u, [PRIMARY_N])]
    scenarios: dict = {name: {} for name, *_ in plan}
    all_log_rows, primary_runs, model_inv = [], {}, None
    for scen, mode, tset, ans, ns in plan:
        for N in ns:
            n_fr = n_free_riders(N) if scen == "H3" else 0
            per_seed = {}
            for seed in SEEDS:
                run = run_one(scen, N, seed, mode, n_fr, tset, ans)
                blk = add_fmt(scenario_block(run))
                per_seed[str(seed)] = blk
                sm = seed_summary(blk)
                all_log_rows.extend(run["rows"])
                if seed == PRIMARY_SEED:
                    primary_runs[(scen, N)] = run
                    if scen == "H3" and N == PRIMARY_N:
                        model_inv = add_fmt(model_id_invariance(run))
                del run["ledger"]
                print(f"[{time.time() - t0:7.1f}s] {scen} N={N} seed={seed}  "
                      f"TPR={sm['process_tpr']} FPR={sm['process_fpr']} acc={sm['process_accuracy']} "
                      f"base_FPR={sm['baseline_fpr']} base_acc={sm['baseline_accuracy']}", flush=True)
            summaries = [seed_summary(b) for b in per_seed.values()]
            invariant = {k: len({json.dumps(sm[k]) for sm in summaries}) == 1 for k in summaries[0]}
            scenarios[scen][f"N{N}"] = {"primary": per_seed[str(PRIMARY_SEED)],
                                        "per_seed_summary": {sd: seed_summary(b) for sd, b in per_seed.items()},
                                        "seed_invariant": invariant,
                                        "all_metrics_seed_invariant": all(invariant.values())}

    illus = {sc: add_fmt(determinism_gap_illustration(primary_runs[(sc, PRIMARY_N)])) for sc in ("H1", "H1b")}
    rule_c = add_fmt(rule_c_unfiltered(conv["problems"]))

    crossover = {}
    for frac in CROSSOVER_FRACTIONS:
        n_fr = int(round(frac * PRIMARY_N))
        run = run_one(f"DIAG_crossover_{int(frac * 100)}", PRIMARY_N, PRIMARY_SEED, "wrong_block",
                      n_fr, tasks, answers)
        pr, ba = arm_metrics(run["rows"], "process", run), arm_metrics(run["rows"], "baseline", run)
        crossover[f"{int(frac * 100)}pct"] = add_fmt({
            "wrong_answer_free_riders": n_fr, "N": PRIMARY_N,
            "process_detection": pr["detection_tpr"], "process_fpr": pr["fpr"],
            "process_accuracy": pr["consensus_accuracy"], "baseline_accuracy": ba["consensus_accuracy"],
            "accuracy_delta_pp": 100.0 * (pr["consensus_accuracy"]["p"] - ba["consensus_accuracy"]["p"]),
            "ledger_verify": run["result"]["ledger_ok"]})
        all_log_rows.extend(run["rows"])
        del run["ledger"]
        print(f"[{time.time() - t0:7.1f}s] crossover {frac:.0%} done", flush=True)

    # ---- example logs (Fig. 2 candidates): first task where each class occurs ---------------------
    examples = {}
    for scen in ("H3", "H1b"):
        run = primary_runs[(scen, PRIMARY_N)]
        want = ({("honest", t) for t in TIERS} | {(v, "") for v in VARIANTS} if scen == "H3"
                else {("honest_divergent", t) for t in TIERS})
        for r in run["rows"]:
            k = (r["behaviour"], r["effort_tier"])
            if k in want and k not in examples:
                examples[k] = (scen, r["log_id"], r["agent_id"], r["task_id"])
    rebuilt = {}
    for (b, t), (scen, lid, aid, tid) in sorted(examples.items()):
        run = primary_runs[(scen, PRIMARY_N)]
        spec = next(sp for sp in run["specs"] if sp.agent_id == aid)
        j, task = next((j, tk) for j, tk in enumerate(tasks) if tk["task_id"] == tid)
        payload = agents.extract_json_object(ws.payload_for(spec.behavior_class, spec.effort, task))
        log = agents.assemble_log(agent_id=aid, model_id=g.SCRIPTED_MODEL_ID, task=task,
                                  reasoning_payload=payload, seed=None, temperature=None,
                                  sim_tick=j + 1, runtime_ms=g.PINNED_RUNTIME_MS, timestamp=g.PINNED_TIMESTAMP)
        log["log_id"] = log["hash"] = SimulatedLedger().recompute_hash(log)
        assert log["log_id"] == lid, "example log rebuild mismatch"
        rebuilt[f"{b}{('/' + t) if t else ''}"] = {"scenario": scen, "log": log, "gates": g.gate_breakdown(log)}

    # ---- results.json ------------------------------------------------------------------------------
    rep = conv["report"]
    selected_meta = [{"task_id": p.task_id, "gsm8k_index": p.index, "annotations": len(p.annotations),
                      "answer": p.answer, "forged_op_fallback": p.forged_fallback,
                      "divergence": tasks[i]["divergent_reference"]["meta"]} for i, p in enumerate(selected)]
    dmeta = [s_["divergence"] for s_ in selected_meta]
    results = {
        "meta": {
            "volatile": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "runtime_seconds": None},
            "description": "P2 offline H1/H1b/H3 evaluation on real GSM8K test problems; scripted agents (no LLM).",
            "authority": "This file is the single numeric authority for the paper (with RESULTS_SUMMARY.md).",
            "environment": {**lib_versions(), "platform": platform.platform(),
                            "machine": platform.machine(), "cpu_count": os.cpu_count()},
            "source_sha256": source_hashes(), "audited_modules_unmodified": audited,
            "git_commit": None,
            "git_commit_note": "the git commit is not recorded; source identity is given by source_sha256",
            "dataset": {"name": g.DATASET, "source_repo": g.SOURCE_REPO, "upstream_commit": g.SOURCE_COMMIT,
                        "source_url": g.SOURCE_URL, "file_sha256": g.SOURCE_SHA256,
                        "rows_in_file": len(rows), "license": "MIT (see data/GSM8K_LICENSE)",
                        "citation_flag": "[VERIFY] Cobbe et al. (2021) is out-of-matrix",
                        "selection_rule": ("first 100 eligible problems by 0-based line index; eligibility = "
                                           "(a) every annotation re-executes exactly under validation._safe_arith; "
                                           "(b) '####' answer == last annotation value; (c) ADDED: complete "
                                           "recorded derivation (all annotation steps in the answer's "
                                           "dependency cone); (d) safety net: all five honest tiers accepted"),
                        "conversion": rep, "selected": selected_meta,
                        "h1u_tasks": {"rule": "first 100 problems passing rules (a)+(b) only",
                                      "last_index": ab[-1].index,
                                      "rule_c_problems": [p.index for p in ab if g.R_INCOMPLETE in p.reasons]}},
            "config": {
                "quick_mode_debug_only": QUICK,
                "n_tasks": N_TASKS, "task_type": "discrete", "primary_N": PRIMARY_N, "robustness_N": ROBUST_N,
                "scenarios": {"H1": "all honest (6 agents per tier at N = 30)",
                              "H1b": "all honest; per task a seeded random 10% of agents emit a validly derived "
                                     "DIVERGENT output (BLUEPRINT D2)",
                              "H3": "10% free-riders, four variants round-robin",
                              "H1U": "as H1 on the first 100 problems passing (a)+(b) only (rule (c) not applied); N = 30"},
                "free_rider_fraction": FREE_RIDER_FRACTION,
                "free_riders_by_N": {str(n): n_free_riders(n) for n in all_ns},
                "free_rider_agent_indices": {str(n): FR_PLACEMENTS[:n_free_riders(n)] for n in all_ns},
                "variant_assignment": "round-robin over (task position, free-rider slot): variant = VARIANTS[(t*F + slot) % 4]",
                "variants": VARIANTS, "intended_gate": g.INTENDED_GATE, "effort_tiers": TIERS,
                "effort_assignment": "agent i -> tier[i % 5]; free-rider effort label is bookkeeping only",
                "divergence": {
                    "fraction": DIVERGENT_FRACTION, "divergent_agents_by_N": {str(n): n_divergent(n) for n in all_ns},
                    "assignment_seed": H1B_DIVERGENCE_SEED,
                    "assignment": "per task, sha256(seed|N|task_id) seeds random.sample(range(N), n); fixed across harness seeds",
                    "construction": ("misread operand: one operand of the reference derivation that is not an earlier "
                                     "step's result is changed by delta (tried in order " + str(list(g.DIVERGENCE_DELTAS))
                                     + "); every downstream calculator step is recomputed with the whitelisted "
                                     "executor; one divergent derivation per task, rendered in the agent's own tier; candidates with a "
                                     "negative divergent answer are rejected in favour of the next (audit O4)"),
                    "operand_in_question": sum(1 for d in dmeta if d["operand_in_question"]),
                    "clean_values": sum(1 for d in dmeta if d["clean"]),
                    "wiring_identical": sum(1 for d in dmeta if d["wiring"] == "identical"),
                    "wiring_subset_value_collision": sum(1 for d in dmeta if d["wiring"] == "subset"),
                    "delta_counts": dict(sorted(Counter(str(d["delta"]) for d in dmeta).items())),
                    "multi_step_recomputation": sum(1 for d in dmeta if len(d["steps_recomputed"]) > 1),
                    "nonnegative_answer_preference": {
                        "rule": ("audit O4: a candidate whose divergent answer is below zero is rejected in favour of "
                                 "the next candidate in search order (preference: kept only if no non-negative "
                                 "candidate exists)"),
                        "tasks_selection_changed": [s_["task_id"] for s_ in selected_meta
                                                    if s_["divergence"]["nonneg_changed_selection"]],
                        "tasks_negative_answer_kept_as_fallback": [s_["task_id"] for s_ in selected_meta
                                                                   if s_["divergence"]["nonneg_fallback_negative_kept"]],
                        "negative_divergent_answers": sum(1 for d in dmeta if float(d["divergent_answer"]) < 0)}},
                "k": K, "supermajority_reject_threshold": THRESHOLD, "r0": consensus.REP_DEFAULT,
                "alpha": consensus.REP_ALPHA_UP, "beta": consensus.REP_BETA_DOWN,
                "rep_floor": consensus.REP_FLOOR, "rep_ceil": consensus.REP_CEIL,
                "harness_seeds": SEEDS, "primary_seed": PRIMARY_SEED,
                "seed_scope": ("the harness seed drives ONLY peer-set draws (and commitment salts, which "
                               "cannot change any outcome); payloads, task order, population and the H1b "
                               "divergent-agent draw are identical across seeds, so seeds are not independent "
                               "samples and are never pooled into n"),
                "round_structure": ("one batch-lockstep consensus.run_round over all tasks (as in the "
                                    "audited adversaries.run_attack_round); aggregation weights are the "
                                    "round-start reputations (all r0), i.e. uniform"),
                "validators": "all validators honest, including free-rider agents when drawn into a peer set; peer sets exclude the log's author",
                "model_id_in_logs": g.SCRIPTED_MODEL_ID, "llm_in_loop": False,
                "log_metadata_pins": {"timestamp": g.PINNED_TIMESTAMP, "runtime_ms": g.PINNED_RUNTIME_MS,
                                      "seed": None, "temperature": None},
                "baseline_arm": ("unweighted plurality over ALL committed final outputs per task "
                                 "(metrics.baseline_consensus); a log is flagged iff its output differs "
                                 "canonically from its task's plurality output"),
                "baseline_geometric_median": ("not applicable: GSM8K tasks are discrete; the geometric "
                                              "median is metrics' continuous-task aggregator. No number is reported."),
                "comparative_test": "McNemar exact two-sided on paired units (replaces BLUEPRINT 4.6 Newcombe)",
                "confirmatory_intervals": "97.5% (Wilson and CP) attached to H1-FPR and H3-TPR per BLUEPRINT D6 (decision unsigned)",
                "targets": {"accuracy_min": 0.95, "detection_min": 0.90, "fpr_max": 0.05,
                            "rule": "met only if the interval bound clears the target (BLUEPRINT)"},
            },
            "statistics": {"intervals": "Wilson 95% and Clopper-Pearson 95% for every proportion",
                           "unit_of_analysis": ("log for TPR/FPR, task for accuracy; logs within a task are NOT "
                                                "independent (same problem; honest logs of one tier are "
                                                "byte-identical payloads), so log-level intervals and p-values "
                                                "are optimistic; task-level proportions and tests are reported "
                                                "alongside"),
                           "seeds": "intervals on the primary seed only; seeds never pooled"},
        },
        "scenarios": scenarios,
        "rule_c_unfiltered": rule_c,
        "determinism_gap_illustration": illus,
        "model_id_invariance": model_inv,
        "diagnostic_out_of_scope_crossover": {
            "label": "OUT OF SCOPE -- NOT FOR THE PAPER (fact sheet sec 5; BLUEPRINT D10). "
                     f"Wrong-answer free-riders sharing one wrong value (answer + 10), N = {PRIMARY_N}, primary seed.",
            "fractions": crossover},
    }
    tgt = {}
    for lbl in ("H1", "H1b", "H3", "H1U"):
        pr = scenarios[lbl][f"N{PRIMARY_N}"]["primary"]["process"]
        tgt[lbl] = {"accuracy_wilson_lower_ge_95": pr["consensus_accuracy"]["wilson95"][0] >= 0.95,
                    "accuracy_cp_lower_ge_95": pr["consensus_accuracy"]["cp95"][0] >= 0.95,
                    "fpr_wilson_upper_lt_5": pr["fpr"]["wilson95"][1] < 0.05,
                    "fpr_cp_upper_lt_5": pr["fpr"]["cp95"][1] < 0.05}
        if pr["detection_tpr"]:
            tgt[lbl]["detection_wilson_lower_ge_90"] = pr["detection_tpr"]["wilson95"][0] >= 0.90
            tgt[lbl]["detection_cp_lower_ge_90"] = pr["detection_tpr"]["cp95"][0] >= 0.90
            tgt[lbl]["per_variant_detection_wilson_lower_ge_90"] = {
                v: d["logs"]["wilson95"][0] >= 0.90 for v, d in pr["detection_per_variant"].items()}
    tgt["rule_c_unfiltered_whole_file"] = {"fpr_wilson_upper_lt_5": rule_c["fpr_logs"]["wilson95"][1] < 0.05,
                                           "fpr_cp_upper_lt_5": rule_c["fpr_logs"]["cp95"][1] < 0.05}
    results["targets_primary"] = tgt

    # ---- write everything -----------------------------------------------------------------------
    results["meta"]["volatile"]["runtime_seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, sort_keys=False, default=str)
        f.write("\n")
    with open(os.path.join(args.out, "example_logs.json"), "w", encoding="utf-8") as f:
        json.dump(rebuilt, f, indent=1)
        f.write("\n")

    prim = {sc: scenarios[sc][f"N{PRIMARY_N}"]["primary"] for sc in scenarios}
    write_csv(os.path.join(args.out, "per_log_records.csv"), all_log_rows, LOG_FIELDS)
    write_csv(os.path.join(args.out, "h1_baseline.csv"), block_prop_rows("H1", prim["H1"]), PROP_FIELDS)
    write_csv(os.path.join(args.out, "h1b_divergence.csv"), block_prop_rows("H1b", prim["H1b"]), PROP_FIELDS)
    write_csv(os.path.join(args.out, "h3_freeriding.csv"), block_prop_rows("H3", prim["H3"]), PROP_FIELDS)
    h3p = prim["H3"]
    pv = []
    for arm in ("process", "baseline"):
        for v, d in h3p[arm]["detection_per_variant"].items():
            m = h3p["comparative"]["detection_per_variant"][v]["logs"]["mcnemar_exact"]
            pv.append({**prop_rows("H3", PRIMARY_N, PRIMARY_SEED, arm, "detection_tpr", v, d["logs"]),
                       "tasks_all_flagged_fmt": d["tasks_all_logs_of_variant_flagged"]["fmt"]["x_n"],
                       "output_correct_logs": d["output_correct_logs"],
                       "intended_gate": g.INTENDED_GATE[v],
                       "intended_gate_failed": (h3p["process"]["gate_attribution_per_variant"][v]["intended_gate_failed"]["fmt"]["x_n"]
                                                if arm == "process" else ""),
                       "mcnemar_b_c": f"{m['b']}/{m['c']}", "mcnemar_p": m["p_fmt"]})
    write_csv(os.path.join(args.out, "h3_per_variant.csv"), pv,
              PROP_FIELDS + ["tasks_all_flagged_fmt", "output_correct_logs", "intended_gate", "intended_gate_failed",
                             "mcnemar_b_c", "mcnemar_p"])
    mc = []
    for sc in ("H1", "H1b", "H3", "H1U"):
        mc.extend(mcnemar_rows(sc, PRIMARY_N, PRIMARY_SEED, prim[sc]["comparative"]))
    write_csv(os.path.join(args.out, "mcnemar.csv"), mc, list(mc[0].keys()))
    # rule (c) first-class table + per-problem gate breakdown
    rc_rows = []
    rc_rows.append({**prop_rows("rule_c_unfiltered", "", "", "predicate", "fpr_distinct_faithful_logs",
                                "all_tiers", rule_c["fpr_logs"]), "unit": "log (problem x tier)"})
    for t in TIERS:
        rc_rows.append({**prop_rows("rule_c_unfiltered", "", "", "predicate", "fpr_distinct_faithful_logs",
                                    f"tier:{t}", rule_c["fpr_per_tier"][t]), "unit": "log (problem x tier)"})
    rc_rows.append({**prop_rows("rule_c_unfiltered", "", "", "predicate", "problems_with_any_tier_rejected",
                                "problems", rule_c["problems_with_any_tier_rejected"]), "unit": "problem"})
    h1u = prim["H1U"]
    for arm in ("process", "baseline"):
        rc_rows.append({**prop_rows("H1U", PRIMARY_N, PRIMARY_SEED, arm, "fpr", "all_honest", h1u[arm]["fpr"]),
                        "unit": "log (end-to-end protocol)"})
        rc_rows.append({**prop_rows("H1U", PRIMARY_N, PRIMARY_SEED, arm, "consensus_accuracy", "all_tasks",
                                    h1u[arm]["consensus_accuracy"]), "unit": "task"})
        rs = h1u[arm]["fpr_rule_c_split"]
        rc_rows.append({**prop_rows("H1U", PRIMARY_N, PRIMARY_SEED, arm, "fpr", "logs_on_rule_c_problems",
                                    rs["logs_on_rule_c_problems"]), "unit": "log (end-to-end protocol)"})
        rc_rows.append({**prop_rows("H1U", PRIMARY_N, PRIMARY_SEED, arm, "task_level_any_honest_flagged", "tasks",
                                    h1u[arm]["task_level"]["tasks_with_any_honest_log_flagged"]), "unit": "task"})
    write_csv(os.path.join(args.out, "rule_c_unfiltered.csv"), rc_rows, PROP_FIELDS + ["unit"])
    gb = [{"gsm8k_index": pp["gsm8k_index"], "annotations": pp["annotations"],
           "orphan_steps": " ".join(str(x) for x in pp["orphan_steps"]),
           "identity_annotation": pp["identity_annotation"],
           "tiers_rejected": " ".join(pp["tiers_rejected"]), "tiers_accepted": " ".join(pp["tiers_accepted"]),
           **{k: sum(1 for t in pp["tiers_rejected"] if k in pp["failed_gates_by_tier"][t]) for k in GATE_KEYS},
           "same_pattern_all_rejected_tiers": pp["same_gate_pattern_in_every_rejected_tier"],
           "failed_gates": " + ".join(pp["failed_gates"])} for pp in rule_c["per_problem"]]
    write_csv(os.path.join(args.out, "rule_c_gate_breakdown.csv"), gb, list(gb[0].keys()))
    write_csv(os.path.join(args.out, "h1b_divergent_derivations.csv"),
              [{"task_id": s_["task_id"], "gsm8k_index": s_["gsm8k_index"], **{k: (" ".join(map(str, v)) if isinstance(v, list) else v)
                                                                        for k, v in s_["divergence"].items()}}
               for s_ in selected_meta],
              ["task_id", "gsm8k_index"] + list(selected_meta[0]["divergence"].keys()))
    rob = []
    for scen in ("H1", "H1b", "H3"):
        for N in all_ns:
            for sd in SEEDS:
                sm = scenarios[scen][f"N{N}"]["per_seed_summary"][str(sd)]
                rob.append({"scenario": scen, "N": N, "seed": sd, "primary_seed": sd == PRIMARY_SEED,
                            **{k: ("" if v is None else (f"{v:.6f}" if isinstance(v, float) else v)) for k, v in sm.items()}})
    rob_fields = []
    for r in rob:
        for k in r:
            if k not in rob_fields:
                rob_fields.append(k)
    write_csv(os.path.join(args.out, "robustness.csv"), rob, rob_fields)
    cost_rows = []
    for scen in ("H1", "H1b", "H3", "H1U"):
        for N in ([PRIMARY_N] + ROBUST_N if scen != "H1U" else [PRIMARY_N]):
            c = scenarios[scen][f"N{N}"]["primary"]["process"]["cost"]
            cost_rows.append({"scenario": scen, "N": N, "seed": PRIMARY_SEED, **c["counters"],
                              **{f"per_log_{k}": f"{v:.4f}" for k, v in c["per_log"].items()},
                              "ledger_tx_total": c["ledger_tx_total"],
                              "reputation_updates": c["reputation_updates"],
                              "walking_skeleton_overhead_ops": c["walking_skeleton_overhead_ops"],
                              "baseline_validation_ops": 0})
    write_csv(os.path.join(args.out, "cost.csv"), cost_rows, list(cost_rows[0].keys()))

    import report  # summary + conversion report (kept separate for readability)
    report.write_summary(results, os.path.join(args.out, "RESULTS_SUMMARY.md"))
    report.write_conversion_report(results, conv, os.path.join(args.out, "gsm8k_conversion_report.md"))
    print(f"done in {time.time() - t0:.1f}s -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
