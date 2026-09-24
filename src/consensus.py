"""consensus.py -- Component D (the simple/honest path, "D-").

Leaderless, per-task PROCESS-VALIDATION consensus over the SimulatedLedger substrate. The unit
of agreement is the VALIDATED REASONING PROCESS (the committed action log), not the output: a log
is admitted to the per-task aggregate only if a supermajority of its assigned validators accept
its log under the mechanical causal-sufficiency predicate (Component C). Reputation is then an
asymmetric, auditable function of those validation OUTCOMES.

What this module is, and is NOT (locked framing):
  * The blockchain is a TAMPER-EVIDENT SUBSTRATE ONLY: it stores logs/verdicts/seals/reputation
    immutably and anchors the attestations. It runs NO protocol logic and is NEVER a randomness
    beacon -- D reads nothing on-chain as a coordination input (sec 0.2).
  * Peer-set randomness comes from the EXPERIMENT HARNESS RNG (recorded seed); randomness flows
    harness -> assignment -> ledger-record, never ledger -> agent (F3).
  * The validity predicate is MECHANICAL and recomputable (no LLM-as-judge anywhere), so any
    verdict -- and any sealed outcome -- is publicly refutable off-ledger.

Build step: playbook A.4 / build-order step 5 (D-). In scope here: harness-seeded peer-set
assignment; per-log verdicts via `validation.validate_committed` (the loop CATCHES `SchemaError`
-> reject/exclude and `F2PendingError` -> defer/exclude, so one malformed or deferred log never
aborts a round); honest-reveal commit-reveal through the phase API; supermajority detection;
reputation-weighted aggregation over validated outputs; seal-then-asymmetric-reputation; and the
H1 all-honest baseline run together with the output-only baseline arm (in metrics.py).

OUT OF SCOPE here (later steps, flagged):
  * Equivocation / non-reveal POLICY (F5) over the evidence the ledger already records
    (`is_equivocating`, `reveal_fail`) -- build-order steps 7-8.
  * The full bounded-Byzantine-VALIDATOR scenario and metric H4 -- build-order step 8 (with the
    Option-B fallback). In D- all validators are honest, so the predicate -- which is called
    WITHOUT peers and excludes validator identity from `recompute_digest` -- yields the SAME
    decision and the SAME digest for every validator on a given committed log; verdict divergence
    is a Byzantine-validator phenomenon introduced later.

This file IMPORTS sim_ledger / validation / agents / metrics and does not modify any of them.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import agents
import metrics
import validation
from sim_ledger import SimulatedLedger

# ==============================================================================================
# Flags / tunables owned by D (see prototype_plan v1.3 sec 5)
# ==============================================================================================
# F4 -- supermajority threshold + peer-set size. A log is FLAGGED (detected) when a fraction
#       >= SUPERMAJORITY_THRESHOLD of its admissible verdicts REJECT; symmetric accept rule.
PEER_SET_SIZE = 3
SUPERMAJORITY_THRESHOLD = 2.0 / 3.0

# F6 -- reputation default + curve. The 0.5 default lives in D (the ledger returns None for an
#       uninitialised agent). The update is ASYMMETRIC (a reject costs more than an accept earns),
#       BOUNDED in the open interval (0, 1), and DIMINISHING at both extremes:
#         accept : r' = r + ALPHA * (1 - r)        (additive increase toward 1)
#         reject : r' = r * (1 - BETA)             (multiplicative decrease toward 0)
#       BETA > ALPHA makes one reject outweigh one accept. The delta is keyed to the VALIDATION
#       OUTCOME, never to output agreement -- so an honest minimal-but-valid log that ACCEPTS is
#       rewarded regardless of how its output compares to the crowd (condition iii / FPR < 5%).
#       Trajectory under three consecutive accepts from the default:
#         0.5 -> 0.55 -> 0.595 -> 0.6355.
REP_DEFAULT = 0.5
REP_ALPHA_UP = 0.10
REP_BETA_DOWN = 0.30
REP_FLOOR = 1e-6
REP_CEIL = 1.0 - 1e-6


class ConsensusError(Exception):
    """A consensus-layer lifecycle / invariant violation (e.g. aggregating before reveal, or an
    honest reveal failing). Distinct from the ledger's PhaseError and from a verdict's decision."""


# ==============================================================================================
# Per-log record threaded through the round's stages
# ==============================================================================================
@dataclass
class LogRec:
    log_id: str
    task_id: str
    author_id: str
    peer_set: list[str] = field(default_factory=list)
    peer_seed: Optional[int] = None
    # verdict entries: {"validator_id", "verdict"(Verdict|None), "status", optional commit_id/salt/verdict_dict}
    verdicts: list[dict] = field(default_factory=list)
    status: str = "pending"            # "ok" | "schema_invalid" | "open_ended_deferred"
    admissible: list[dict] = field(default_factory=list)   # revealed verdict dicts from assigned peers
    outcome: str = "pending"           # "accept" | "reject" | "no_consensus" | "deferred"
    flagged: bool = False
    seal_id: Optional[str] = None
    rep_before: Optional[float] = None
    rep_after: Optional[float] = None


# ==============================================================================================
# Reputation (F6)
# ==============================================================================================
def init_population_reputation(ledger: SimulatedLedger, agent_ids, *, sim_tick: int = 0) -> int:
    """Initialise each not-yet-known agent to the D default (0.5) via the ledger's genesis path
    (`init_reputation`, reason_ref 'genesis-init'). Already-initialised agents are skipped (the
    ledger rejects re-init). Returns the number newly initialised."""
    n = 0
    for aid in sorted(set(agent_ids)):
        if ledger.get_reputation(aid) is None:
            ledger.init_reputation(aid, REP_DEFAULT, sim_tick)
            n += 1
    return n


def current_reputation(ledger: SimulatedLedger, agent_id: str) -> float:
    """Stored reputation, or the D default if the agent was never initialised (defensive: the
    population is initialised at round start, so this normally returns a stored value)."""
    r = ledger.get_reputation(agent_id)
    return REP_DEFAULT if r is None else r


def reputation_update(current: float, outcome: str) -> float:
    """The asymmetric, bounded, diminishing curve (see F6 note above). Only "accept"/"reject"
    change reputation; any other outcome is a no-op (deferred / no-consensus do not write)."""
    if outcome == "accept":
        nxt = current + REP_ALPHA_UP * (1.0 - current)
    elif outcome == "reject":
        nxt = current * (1.0 - REP_BETA_DOWN)
    else:
        return current
    return min(REP_CEIL, max(REP_FLOOR, nxt))


# ==============================================================================================
# Stage 1 -- peer-set assignment (harness-seeded; recorded on-ledger for audit) [F3]
# ==============================================================================================
def assign_peer_sets(ledger: SimulatedLedger, log_recs: list[LogRec], agent_ids,
                     *, k: int, harness_rng: random.Random, sim_tick: int) -> None:
    """Assign each committed log a random k-validator peer set drawn from all agents EXCEPT the
    log's author. The per-log seed is drawn from the HARNESS RNG (never any on-chain value) and
    is recorded via `assign_peer_set` for reproducibility only (F3). Logs are processed in a
    deterministic order so the seed stream is reproducible from the harness seed."""
    pool_all = sorted(set(agent_ids))
    for rec in sorted(log_recs, key=lambda r: r.log_id):
        pool = [a for a in pool_all if a != rec.author_id]
        if len(pool) < k:
            raise ConsensusError(
                f"cannot assign {k} validators for log {rec.log_id[:12]}: only {len(pool)} "
                f"eligible peers (need an agent population of at least k+1)")
        log_seed = harness_rng.randrange(2 ** 31)          # <- HARNESS rng, recorded for audit
        validators = sorted(random.Random(log_seed).sample(pool, k))
        ledger.assign_peer_set(rec.task_id, rec.log_id, validators, seed=log_seed, sim_tick=sim_tick)
        rec.peer_set = validators
        rec.peer_seed = log_seed


# ==============================================================================================
# Stage 2 -- per-log verdicts via Component C (catch SchemaError / F2PendingError)
# ==============================================================================================
def compute_log_verdicts(ledger: SimulatedLedger, log_recs: list[LogRec], *, sim_tick: int) -> int:
    """Each assigned validator runs the mechanical predicate over the committed log. The loop
    CATCHES `SchemaError` (structurally-invalid log -> treat as reject/exclude) and
    `F2PendingError` (open_ended -> defer/exclude); a plain ValueError from
    `validate_committed` (missing log / failed integrity) is a substrate bug and is allowed to
    propagate. One malformed or deferred log therefore cannot abort the round. Returns the
    number of real (non-excluded) verdicts computed."""
    n = 0
    for rec in log_recs:
        statuses: list[str] = []
        for v in rec.peer_set:
            try:
                verdict = validation.validate_committed(ledger, rec.log_id,
                                                        validator_id=v, sim_tick=sim_tick)
                rec.verdicts.append({"validator_id": v, "verdict": verdict,
                                     "status": verdict.decision})
                statuses.append(verdict.decision)
                n += 1
            except validation.F2PendingError:
                rec.verdicts.append({"validator_id": v, "verdict": None, "status": "deferred"})
                statuses.append("deferred")
            except agents.SchemaError:
                rec.verdicts.append({"validator_id": v, "verdict": None, "status": "schema_reject"})
                statuses.append("schema_reject")
        if statuses and all(s == "schema_reject" for s in statuses):
            rec.status = "schema_invalid"
        elif statuses and all(s == "deferred" for s in statuses):
            rec.status = "open_ended_deferred"
        else:
            rec.status = "ok"
    return n


# ==============================================================================================
# Stage 3 -- commit-reveal through the ledger (global, batch-lockstep)
# ==============================================================================================
def commit_all_verdicts(ledger: SimulatedLedger, log_recs: list[LogRec],
                        *, harness_rng: random.Random, sim_tick: int) -> int:
    """Open ONE global commit phase and commit a hiding commitment for EVERY validator of every
    "ok" log BEFORE any reveal. The commitment uses the ledger's canonical binding
    `SimulatedLedger._commitment(verdict_dict, salt)` so commit and reveal agree by construction.
    Salts are drawn from the harness RNG. Returns the number of commitments written."""
    ledger.begin_commit_phase(sim_tick)
    n = 0
    for rec in log_recs:
        if rec.status != "ok":
            continue                                       # schema_invalid / deferred -> nothing to commit
        for entry in rec.verdicts:
            verdict = entry["verdict"]
            if verdict is None:
                continue
            vd = verdict.to_dict()
            salt = format(harness_rng.getrandbits(128), "032x")
            commitment = SimulatedLedger._commitment(vd, salt)
            cid = ledger.commit_verdict(rec.task_id, rec.log_id, entry["validator_id"],
                                        commitment, sim_tick)
            entry["commit_id"] = cid
            entry["salt"] = salt
            entry["verdict_dict"] = vd
            n += 1
    return n


def reveal_all_verdicts(ledger: SimulatedLedger, log_recs: list[LogRec], *, sim_tick: int) -> int:
    """Close commits, open ONE global reveal phase, and reveal every committed verdict. Each
    reveal is cross-checked: the ledger must accept it (commitment matches), the revealed
    verdict's INTERNAL log_id/task_id must equal those on its commit record, and
    `verify_verdict` must hold. An honest reveal failing is a ConsensusError (a D bug), distinct
    from the adversary non-reveal that F5 will handle. Returns the number revealed."""
    ledger.begin_reveal_phase(sim_tick)
    n = 0
    for rec in log_recs:
        if rec.status != "ok":
            continue
        committed = {r["commitment"]: r for r in ledger.get_verdicts(rec.log_id, phase="committed")}
        for entry in rec.verdicts:
            if "commit_id" not in entry:
                continue
            vd, salt, cid = entry["verdict_dict"], entry["salt"], entry["commit_id"]
            if not ledger.reveal_verdict(cid, vd, salt):
                raise ConsensusError(f"honest reveal failed for commit {cid[:12]} "
                                     f"(log {rec.log_id[:12]})")
            crec = committed.get(SimulatedLedger._commitment(vd, salt))
            if crec is None:
                raise ConsensusError("revealed verdict has no matching commit record")
            if crec["log_id"] != vd["log_id"] or crec["task_id"] != vd["task_id"]:
                raise ConsensusError("commit/reveal log_id|task_id mismatch -- verdict body does "
                                     "not match the commitment's claimed log/task")
            if not ledger.verify_verdict(cid):
                raise ConsensusError(f"verify_verdict failed after reveal for {cid[:12]}")
            n += 1
    return n


# ==============================================================================================
# Stage 4 + 5 -- admissibility, per-log outcome / detection, per-task aggregation
# ==============================================================================================
def _final_value(ledger: SimulatedLedger, rec: LogRec) -> Any:
    return ledger.get_log(rec.log_id)["final_output"]["value"]


def read_admissible_verdicts(ledger: SimulatedLedger, rec: LogRec) -> list[dict]:
    """Only the ASSIGNED validators' revealed verdicts count. The ledger records every revealed
    verdict; D enforces admissibility here (record-but-don't-filter on the substrate)."""
    peer = set(rec.peer_set)
    return [v for v in ledger.get_verdicts(rec.log_id, phase="revealed")
            if v.get("validator_id") in peer]


def log_outcome(rec: LogRec, admissible: list[dict], *, threshold: float) -> tuple[str, bool]:
    """Per-log outcome + flag. schema_invalid -> reject & FLAGGED (a detected, refutable fault).
    open_ended_deferred -> deferred (excluded from aggregation AND detection; no reputation).
    ok -> supermajority over admissible accept/reject decisions: a reject fraction >= threshold
    FLAGS the log (detection, F4); an accept fraction >= threshold yields accept; otherwise
    no_consensus (no aggregation, no reputation -- F5/H4 territory)."""
    if rec.status == "schema_invalid":
        return "reject", True
    if rec.status == "open_ended_deferred":
        return "deferred", False
    decisions = [v["decision"] for v in admissible]
    total = len(decisions)
    if total == 0:
        return "no_consensus", False
    rejects = sum(1 for d in decisions if d == "reject")
    accepts = sum(1 for d in decisions if d == "accept")
    need = math.ceil(threshold * total)
    if rejects >= need:
        return "reject", True
    if accepts >= need:
        return "accept", False
    return "no_consensus", False


def aggregate_round(ledger: SimulatedLedger, log_recs: list[LogRec],
                    tasks_by_id: dict[str, dict], *, threshold: float) -> tuple[dict, dict]:
    """Compute per-log outcomes, then a per-task consensus over the VALIDATED (accept-outcome)
    logs only, reputation-weighted by author. discrete -> reputation-weighted plurality;
    continuous -> reputation-weighted geometric median; open_ended -> deferred (None), co-gated
    by F2. Cross-Agent Coherence enters only as the plurality tie-break SIGNAL (never a gate).

    GUARD: this MUST run in the REVEAL phase. Calling it before commit-reveal has completed
    raises ConsensusError -- aggregation refuses to read verdicts until every commitment is
    locked and revealed (verdict independence; this is the property the test exercises)."""
    if ledger.phase != "reveal":
        raise ConsensusError(
            "aggregate_round requires the REVEAL phase: refusing to read verdicts before every "
            f"commitment is locked and revealed (ledger.phase == {ledger.phase!r}).")

    for rec in log_recs:
        rec.admissible = read_admissible_verdicts(ledger, rec)
        rec.outcome, rec.flagged = log_outcome(rec, rec.admissible, threshold=threshold)

    by_task: dict[str, list[LogRec]] = defaultdict(list)
    for rec in log_recs:
        by_task[rec.task_id].append(rec)

    consensus_by_task: dict[str, Any] = {}
    detail: dict[str, dict] = {}
    for task_id, recs in by_task.items():
        ttype = tasks_by_id[task_id]["task_type"]
        accepted = [r for r in recs if r.outcome == "accept"]
        if ttype == "open_ended":
            consensus_by_task[task_id] = None
            detail[task_id] = {"method": "deferred_f2", "accepted": len(accepted)}
            continue
        if not accepted:
            consensus_by_task[task_id] = None
            detail[task_id] = {"method": "no_accepted_logs", "accepted": 0}
            continue
        items = [(_final_value(ledger, r), current_reputation(ledger, r.author_id)) for r in accepted]
        res = (metrics.geometric_median(items) if ttype == "continuous"
               else metrics.weighted_majority(items))
        consensus_by_task[task_id] = res["value"]
        detail[task_id] = {**res, "accepted": len(accepted)}
    return consensus_by_task, detail


# ==============================================================================================
# Stage 6 -- seal the outcome, then write the asymmetric reputation update keyed to the seal
# ==============================================================================================
def seal_and_update_reputation(ledger: SimulatedLedger, log_recs: list[LogRec],
                               *, sim_tick: int) -> None:
    """For every log whose outcome is accept/reject: mint a verdict-set seal (`seal_verdict_set`)
    over the included verdict commit_ids and the computed outcome, then write the reputation
    update for the AUTHOR citing that seal_id (`set_reputation(reason_ref=seal_id)`), so the
    refutability chain reputation -> seal -> verdicts -> log resolves and `verify()` passes
    (every reputation write anchors to a recorded seal or to genesis-init). A schema_invalid log
    is sealed with an EMPTY verdict_commit_ids set -- the absence of any acceptable verdict IS
    the evidence; an auditor re-runs the section-2 check, it fails, and the reject is justified.
    deferred / no_consensus outcomes mint NO seal and write NO reputation. Logs are processed in
    a deterministic order."""
    for rec in sorted(log_recs, key=lambda r: r.log_id):
        if rec.outcome not in ("accept", "reject"):
            rec.seal_id = None
            continue
        commit_ids = [e["commit_id"] for e in rec.verdicts if "commit_id" in e]
        seal_id = ledger.seal_verdict_set(rec.task_id, rec.log_id, commit_ids, rec.outcome, sim_tick)
        rec.seal_id = seal_id
        before = current_reputation(ledger, rec.author_id)
        after = reputation_update(before, rec.outcome)
        ledger.set_reputation(rec.author_id, after, reason_ref=seal_id)   # tick inherits the seal
        rec.rep_before, rec.rep_after = before, after


# ==============================================================================================
# Orchestrator -- one batch-lockstep round (assign -> verdict -> commit -> reveal -> aggregate
#                 -> seal/reputation), then metrics + the output-only baseline arm
# ==============================================================================================
def run_round(ledger: SimulatedLedger, log_recs: list[LogRec], tasks_by_id: dict[str, dict],
              agent_ids, ground_truth: dict[str, Any], attack_labels: dict[str, bool],
              *, k: int = PEER_SET_SIZE, threshold: float = SUPERMAJORITY_THRESHOLD,
              harness_rng: Optional[random.Random] = None, round_tick: int = 1,
              verbose: bool = True) -> dict:
    """Run ONE consensus batch over already-committed logs and return a results dict. The phase
    lifecycle is strictly: assign all -> compute all verdicts -> ONE global commit -> ONE global
    reveal -> aggregate -> seal/reputation (D must not overlap rounds; F11 batch-lockstep)."""
    harness_rng = harness_rng or random.Random(0)
    task_types = {tid: t["task_type"] for tid, t in tasks_by_id.items()}

    if verbose:
        _hr("ROUND START  (Component D- : process-validation consensus)")
        print(f"logs={len(log_recs)}  tasks={len(tasks_by_id)}  agents={len(set(agent_ids))}  "
              f"k={k}  supermajority>={threshold:.3f}  round_tick={round_tick}")

    # -- init reputations -----------------------------------------------------------------------
    n_init = init_population_reputation(ledger, agent_ids, sim_tick=0)
    if verbose:
        _stage("0. init reputation",
               f"{n_init} agents initialised to D default {REP_DEFAULT} (genesis-init)")
        for aid in sorted(set(agent_ids)):
            print(f"      {_aid(aid)}  r0 = {current_reputation(ledger, aid):.4f}")

    # -- stage 1: peer assignment ---------------------------------------------------------------
    assign_peer_sets(ledger, log_recs, agent_ids, k=k, harness_rng=harness_rng, sim_tick=round_tick)
    if verbose:
        _stage("1. peer assignment", f"harness-seeded k={k} validator set per log (F3)")
        for rec in sorted(log_recs, key=lambda r: (r.task_id, r.author_id)):
            print(f"      log {_short(rec.log_id)} [{rec.task_id}] author {_aid(rec.author_id)} "
                  f"seed={rec.peer_seed} -> peers {[_aid(v) for v in rec.peer_set]}")

    # -- stage 2: verdicts ----------------------------------------------------------------------
    n_verdicts = compute_log_verdicts(ledger, log_recs, sim_tick=round_tick)
    if verbose:
        _stage("2. verdicts (mechanical predicate; SchemaError/F2Pending caught)",
               f"{n_verdicts} verdicts computed")
        for rec in sorted(log_recs, key=lambda r: (r.task_id, r.author_id)):
            decisions = [e["status"] for e in rec.verdicts]
            print(f"      log {_short(rec.log_id)} [{rec.task_id}] status={rec.status:<18} "
                  f"verdicts={decisions}")

    # -- stage 3: commit then reveal (global) ---------------------------------------------------
    n_commit = commit_all_verdicts(ledger, log_recs, harness_rng=harness_rng, sim_tick=round_tick)
    if verbose:
        _stage("3a. commit (global)", f"phase={ledger.phase!r}  {n_commit} hiding commitments "
               f"locked BEFORE any reveal")
    n_reveal = reveal_all_verdicts(ledger, log_recs, sim_tick=round_tick)
    if verbose:
        _stage("3b. reveal (global)", f"phase={ledger.phase!r}  {n_reveal} verdicts revealed + "
               f"cross-checked (verify_verdict)")

    # -- stage 4/5: aggregate -------------------------------------------------------------------
    consensus_by_task, agg_detail = aggregate_round(ledger, log_recs, tasks_by_id, threshold=threshold)
    flagged_by_log = {rec.log_id: rec.flagged for rec in log_recs}
    if verbose:
        _stage("4. detection (supermajority reject -> flag, F4)", "")
        any_flag = False
        for rec in sorted(log_recs, key=lambda r: (r.task_id, r.author_id)):
            if rec.flagged:
                any_flag = True
                print(f"      FLAGGED  log {_short(rec.log_id)} [{rec.task_id}] "
                      f"outcome={rec.outcome} status={rec.status}")
        if not any_flag:
            print("      no logs flagged (all admitted under the predicate)")
        _stage("5. aggregation (reputation-weighted, over ACCEPTED logs only)", "")
        for tid in sorted(tasks_by_id):
            d = agg_detail.get(tid, {})
            print(f"      task {tid} [{task_types[tid]}]  consensus={consensus_by_task.get(tid)!r}  "
                  f"({d.get('method', '?')}, accepted={d.get('accepted', 0)})")
            for row in d.get("tally", []):
                print(f"          value={row['value']!r:>6}  weight={row['weight']:<8} "
                      f"count={row['count']}")

    # -- stage 6: seal + reputation -------------------------------------------------------------
    seal_and_update_reputation(ledger, log_recs, sim_tick=round_tick)
    if verbose:
        _stage("6. seal + asymmetric reputation (reason_ref = seal_id, F6)", "")
        for rec in sorted(log_recs, key=lambda r: (r.task_id, r.author_id)):
            if rec.seal_id:
                print(f"      log {_short(rec.log_id)} [{rec.task_id}] outcome={rec.outcome:<6} "
                      f"seal={_short(rec.seal_id)}  rep {rec.rep_before:.4f} -> {rec.rep_after:.4f}")
            else:
                print(f"      log {_short(rec.log_id)} [{rec.task_id}] outcome={rec.outcome:<12} "
                      f"(no seal / no reputation write)")

    # -- metrics + output-only baseline arm -----------------------------------------------------
    outputs_by_task: dict[str, list[Any]] = defaultdict(list)
    for rec in log_recs:
        outputs_by_task[rec.task_id].append(_final_value(ledger, rec))
    baseline = metrics.baseline_consensus(dict(outputs_by_task), task_types)
    baseline_by_task = {tid: b["value"] for tid, b in baseline.items()}

    proc_acc = metrics.consensus_accuracy(consensus_by_task, ground_truth, task_types)
    base_acc = metrics.consensus_accuracy(baseline_by_task, ground_truth, task_types)
    comparative = metrics.comparative_vs_baseline(proc_acc["accuracy"], base_acc["accuracy"])
    det = metrics.detection_rate(flagged_by_log, attack_labels)
    fpr = metrics.false_positive_rate(flagged_by_log, attack_labels)

    n_validated = sum(1 for r in log_recs if r.status == "ok")
    counters = {
        "logs": len(log_recs),
        "validated_logs": n_validated,
        "schema_rejects": sum(1 for r in log_recs if r.status == "schema_invalid"),
        "deferred": sum(1 for r in log_recs if r.status == "open_ended_deferred"),
        "peer_set_assignments": len(log_recs),
        "verdicts_computed": n_verdicts,
        "commits": n_commit,
        "reveals": n_reveal,
        "seals": sum(1 for r in log_recs if r.seal_id),
    }
    cost = metrics.verification_cost(counters)
    final_rep = {aid: current_reputation(ledger, aid) for aid in sorted(set(agent_ids))}

    if verbose:
        _hr("METRICS")
        print(f"  consensus accuracy (process) : {_fmt_acc(proc_acc['accuracy'])}  "
              f"({proc_acc['correct']}/{proc_acc['scored']} scored tasks)")
        print(f"  consensus accuracy (baseline): {_fmt_acc(base_acc['accuracy'])}  "
              f"  <- output-only arm, no process validation")
        print(f"  comparative (process - base) : {_fmt_delta(comparative['delta'])}")
        print(f"  attack detection rate        : {det if det is not None else 'N/A (no attackers in H1)'}")
        print(f"  false positive rate          : {_fmt_acc(fpr)}   (honest logs flagged / honest logs)")
        print(f"  verification cost            : {cost['verdicts_computed']} verdicts / "
              f"{cost['commits']} commits / {cost['reveals']} reveals / {cost['seals']} seals "
              f"({cost['avg_validators_per_validated_log']} validators per validated log)")
        print("  per-task (process vs baseline vs ground truth):")
        for tid in sorted(tasks_by_id):
            print(f"      {tid}: process={consensus_by_task.get(tid)!r:>6}  "
                  f"baseline={baseline_by_task.get(tid)!r:>6}  truth={ground_truth.get(tid)!r:>6}")
        print("  final reputation:")
        for aid, r in final_rep.items():
            print(f"      {_aid(aid)}  r = {r:.4f}")
        print(f"  whole-ledger verify(): {ledger.verify()}")
        _hr("ROUND END")

    return {
        "consensus_by_task": consensus_by_task,
        "baseline_by_task": baseline_by_task,
        "aggregation_detail": agg_detail,
        "flagged_by_log": flagged_by_log,
        "process_accuracy": proc_acc,
        "baseline_accuracy": base_acc,
        "comparative": comparative,
        "detection_rate": det,
        "false_positive_rate": fpr,
        "verification_cost": cost,
        "final_reputation": final_rep,
        "ledger_ok": ledger.verify(),
        "log_recs": log_recs,
    }


# ==============================================================================================
# Pretty-printing helpers
# ==============================================================================================
def _short(s: str, n: int = 10) -> str:
    return (s[:n] + "..") if isinstance(s, str) and len(s) > n else s


def _aid(s: str) -> str:
    """Compact, DISTINGUISHING agent-id label: keep the did:key 'z6Mk' marker plus the tail, so
    different agents/peer sets are visibly different in the printout (the common prefix is not)."""
    if isinstance(s, str) and s.startswith("did:key:z6Mk"):
        return "z6Mk\u2026" + s[-5:]
    return _short(s)


def _hr(title: str) -> None:
    print("\n" + "=" * 86)
    print(title)
    print("=" * 86)


def _stage(title: str, note: str) -> None:
    print(f"\n  -- {title}" + (f"  :: {note}" if note else ""))


def _fmt_acc(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x * 100:.1f}%"


def _fmt_delta(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x * 100:+.1f} pp"


# ==============================================================================================
# H1 -- all-honest baseline run (a spread of legitimate effort levels, incl. minimal-but-valid)
# ==============================================================================================
HARNESS_SEED = 20260629          # recorded experiment seed (F3): environment, not protocol
MODEL_SEED_BASE = 101

# Five legitimate effort levels, ALL grounding the answer in a re-executable calculator op
# (so all ACCEPT under causal-sufficiency). "minimal" is a single tool_call step that is itself
# the terminal -- the floor of honest effort -- included to demonstrate that minimal-but-valid
# reasoning is NOT penalised (it accepts and is rewarded exactly like the verbose tiers).
EFFORT_TIERS = ["minimal", "terse", "standard", "verbose", "evidence"]

# Three discrete GSM8K-style tasks (operands a, b; answer a + b). Ground truth is a HARNESS
# label store and never appears in any log.
H1_TASKS = [
    {"task_id": "gsm8k-A", "task_type": "discrete", "dataset": "GSM8K",
     "prompt": "A box has 12 red and 25 blue marbles. How many marbles in total?", "a": 12, "b": 25},
    {"task_id": "gsm8k-B", "task_type": "discrete", "dataset": "GSM8K",
     "prompt": "There are 9 apples and 8 pears in a basket. How many fruits in total?", "a": 9, "b": 8},
    {"task_id": "gsm8k-C", "task_type": "discrete", "dataset": "GSM8K",
     "prompt": "A shelf holds 40 novels and 2 atlases. How many books in total?", "a": 40, "b": 2},
]


def make_payload(effort: str, a: int, b: int) -> str:
    """Return a canned reasoning-payload JSON string for one honest log at the given effort tier.
    Every tier reproduces a + b via a calculator op and reads the answer off a terminal step
    whose value is GROUNDED in that op (passes process-coherence, tool-utilization, and
    causal-sufficiency). The 'evidence' tier records the op output as an int and uses a sum()
    expression; the validator canonicalises "N" == N, so all tiers validate identically."""
    import json
    ans = a + b
    s = str(ans)
    if effort == "minimal":                       # 1 step: a tool_call that is itself terminal
        steps = [{"step_index": 0, "step_type": "tool_call",
                  "content": f"Compute {a} + {b} with the calculator; that is the answer.",
                  "depends_on": [], "produces": s, "tool_op_ref": "op-0"}]
        ops = [{"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": f"{a}+{b}"},
                "output": s, "deterministic": True, "evidence_hash": None}]
        derived = 0
    elif effort == "terse":                        # 2 steps: tool_call -> decision
        steps = [{"step_index": 0, "step_type": "tool_call",
                  "content": f"Add {a} and {b}.", "depends_on": [], "produces": s, "tool_op_ref": "op-0"},
                 {"step_index": 1, "step_type": "decision",
                  "content": f"The total is {s}.", "depends_on": [0], "produces": s, "tool_op_ref": None}]
        ops = [{"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": f"{a}+{b}"},
                "output": s, "deterministic": True, "evidence_hash": None}]
        derived = 1
    elif effort == "verbose":                      # 4 steps: inference -> inference -> tool_call -> decision
        steps = [{"step_index": 0, "step_type": "inference",
                  "content": f"Identify the two quantities: {a} and {b}.",
                  "depends_on": [], "produces": None, "tool_op_ref": None},
                 {"step_index": 1, "step_type": "inference",
                  "content": "They are disjoint groups, so the total is their sum.",
                  "depends_on": [0], "produces": None, "tool_op_ref": None},
                 {"step_index": 2, "step_type": "tool_call",
                  "content": f"Add {a} and {b}.", "depends_on": [1], "produces": s, "tool_op_ref": "op-0"},
                 {"step_index": 3, "step_type": "decision",
                  "content": f"Total = {s}.", "depends_on": [2], "produces": s, "tool_op_ref": None}]
        ops = [{"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": f"{a} + {b}"},
                "output": s, "deterministic": True, "evidence_hash": None}]
        derived = 3
    elif effort == "evidence":                     # 4 steps with evidence_use; int op output, sum() expr
        steps = [{"step_index": 0, "step_type": "inference",
                  "content": "The question asks for the combined count.",
                  "depends_on": [], "produces": None, "tool_op_ref": None},
                 {"step_index": 1, "step_type": "tool_call",
                  "content": f"Use the calculator to sum {a} and {b}.",
                  "depends_on": [0], "produces": s, "tool_op_ref": "op-0"},
                 {"step_index": 2, "step_type": "evidence_use",
                  "content": f"The calculator returned {s}, the total.",
                  "depends_on": [1], "produces": s, "tool_op_ref": None},
                 {"step_index": 3, "step_type": "decision",
                  "content": f"Answer: {s}.", "depends_on": [2], "produces": s, "tool_op_ref": None}]
        ops = [{"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": f"sum({a}, {b})"},
                "output": ans, "deterministic": True, "evidence_hash": None}]
        derived = 3
    else:                                          # "standard" -- 3 steps: inference -> tool_call -> decision
        steps = [{"step_index": 0, "step_type": "inference",
                  "content": "Total = first quantity + second quantity.",
                  "depends_on": [], "produces": None, "tool_op_ref": None},
                 {"step_index": 1, "step_type": "tool_call",
                  "content": f"Compute {a} + {b} with the calculator.",
                  "depends_on": [0], "produces": s, "tool_op_ref": "op-0"},
                 {"step_index": 2, "step_type": "decision",
                  "content": f"The total is {s}.", "depends_on": [1], "produces": s, "tool_op_ref": None}]
        ops = [{"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": f"{a}+{b}"},
                "output": s, "deterministic": True, "evidence_hash": None}]
        derived = 2
    return json.dumps({"reasoning_steps": steps, "tool_operations": ops,
                       "final_output": {"value": s, "derived_from_step": derived}})


def build_h1(ledger: SimulatedLedger):
    """Build the H1 population (5 honest agents, one per effort tier), have each solve all three
    tasks via the offline ScriptedModelClient, and commit the logs. Returns (log_recs,
    tasks_by_id, agent_ids, ground_truth, attack_labels)."""
    configs, clients = [], []
    for i, effort in enumerate(EFFORT_TIERS):
        configs.append(agents.AgentConfig(agent_id=agents.make_did(i), model_id=agents.DEFAULT_MODEL,
                                          seed=MODEL_SEED_BASE + i, temperature=0.7,
                                          behavior_class="honest"))   # harness label, never logged
        clients.append(agents.ScriptedModelClient(*[make_payload(effort, t["a"], t["b"])
                                                    for t in H1_TASKS]))
    model = agents.ConsensusModel(configs, clients, ledger, H1_TASKS[0], seed=7)
    for t in H1_TASKS:                              # one Mesa step per task (ticks 1,2,3)
        model.current_task = t
        model.step()

    log_recs: list[LogRec] = []
    for agent in sorted(model.agents, key=lambda a: a.agent_id):
        for res in agent.results:
            log_recs.append(LogRec(log_id=res["log_id"], task_id=res["task_id"],
                                   author_id=agent.agent_id))

    tasks_by_id = {t["task_id"]: t for t in H1_TASKS}
    agent_ids = [c.agent_id for c in configs]
    ground_truth = {t["task_id"]: str(t["a"] + t["b"]) for t in H1_TASKS}     # harness label store
    attack_labels = {rec.log_id: False for rec in log_recs}                   # all behavior_class honest
    return log_recs, tasks_by_id, agent_ids, ground_truth, attack_labels


def run_h1(verbose: bool = True) -> dict:
    """Run the all-honest H1 baseline end to end (offline; no Ollama)."""
    if verbose:
        _hr("H1 -- ALL-HONEST BASELINE  (Component D-, offline ScriptedModelClient)")
        print("5 honest agents at effort tiers " + ", ".join(EFFORT_TIERS) + "; each solves "
              + str(len(H1_TASKS)) + " GSM8K-style tasks.\nEvery tier grounds its answer in a "
              "re-executable calculator op -> all ACCEPT (minimal-but-valid is not penalised).")
    ledger = SimulatedLedger()
    log_recs, tasks_by_id, agent_ids, ground_truth, attack_labels = build_h1(ledger)
    if verbose:
        print(f"\ncommitted {len(log_recs)} distinct logs "
              f"({len(set(agent_ids))} agents x {len(tasks_by_id)} tasks).")
    return run_round(ledger, log_recs, tasks_by_id, agent_ids, ground_truth, attack_labels,
                     harness_rng=random.Random(HARNESS_SEED), round_tick=len(H1_TASKS),
                     verbose=verbose)


if __name__ == "__main__":
    run_h1()
