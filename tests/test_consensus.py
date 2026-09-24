"""test_consensus.py -- Component D (D-) test suite, over REAL committed ledger logs.

Every fixture builds genuine section-2 logs and commits them through the real substrate
(agents.finalize_and_commit, or ledger.commit_log for the deliberately schema-invalid specimen
that finalize_and_commit correctly refuses). No mocking of the ledger, predicate, or agent layer.

Cases (plan Step 5 / playbook A.4 acceptance):
  test_honest_validators_agree            -- honest validators emit ONE agreeing verdict set
                                             (same decision + identical recompute_digest)
  test_aggregate_refuses_before_reveal    -- aggregation refuses to read verdicts pre-reveal
  test_supermajority_reject_is_flagged    -- a log a supermajority rejects is flagged (F4)
  test_reputation_is_asymmetric_bounded_keyed -- the F6 curve: asymmetric, bounded, outcome-keyed
  test_round_completes_with_excluded_logs -- a peer set with one SchemaError + one F2Pending log
                                             still completes the round, both excluded
  test_metrics_equality_parity            -- metrics canonical equality == validation._values_equal

Out of scope here (later build-order steps, by design): equivocation / non-reveal POLICY (F5) and
the full bounded-Byzantine-VALIDATOR scenario + metric H4 (step 8, with the Option-B fallback).
"""

from __future__ import annotations

import json

import pytest

import agents
import consensus
import metrics
import validation
from sim_ledger import SimulatedLedger

# ----------------------------------------------------------------------------------------------
# Task fixtures (task_type/dataset/prompt are harness-owned; ground truth never enters a log)
# ----------------------------------------------------------------------------------------------
TASK_D = {"task_id": "t-discrete", "task_type": "discrete", "dataset": "GSM8K",
          "prompt": "12 + 25 = ?"}
TASK_O = {"task_id": "t-open", "task_type": "open_ended", "dataset": "ELI5",
          "prompt": "Explain why the sky is blue."}


def _dids(n: int) -> list[str]:
    return [agents.make_did(i, salt="test") for i in range(n)]


def _commit_honest(ledger: SimulatedLedger, did: str, task: dict, a: int, b: int,
                   effort: str = "standard") -> str:
    """Commit a genuine, schema-valid, causally-sufficient honest log (reuses the H1 payload
    generator). Validators ACCEPT it under the mechanical predicate."""
    payload = json.loads(consensus.make_payload(effort, a, b))
    log = agents.assemble_log(agent_id=did, model_id=agents.DEFAULT_MODEL, task=task,
                              reasoning_payload=payload, seed=1, temperature=0.7, sim_tick=1)
    return agents.finalize_and_commit(log, ledger)


def _commit_freerider(ledger: SimulatedLedger, did: str, task: dict, answer: str = "37") -> str:
    """Commit a schema-VALID but causally-INSUFFICIENT log: a bare decision asserts the answer
    with NO re-executable grounding op, so causal-sufficiency C4 fails -> the predicate REJECTS it
    (decision == 'reject'). This is the free-rider (H3) pattern; it commits cleanly (schema is
    fine) but does not validate."""
    payload = {
        "reasoning_steps": [
            {"step_index": 0, "step_type": "decision", "content": f"The answer is {answer}.",
             "depends_on": [], "produces": answer, "tool_op_ref": None},
        ],
        "tool_operations": [],
        "final_output": {"value": answer, "derived_from_step": 0},
    }
    log = agents.assemble_log(agent_id=did, model_id=agents.DEFAULT_MODEL, task=task,
                              reasoning_payload=payload, seed=1, temperature=0.7, sim_tick=1)
    return agents.finalize_and_commit(log, ledger)


def _commit_open_ended(ledger: SimulatedLedger, did: str, task: dict) -> str:
    """Commit a schema-valid open_ended log. validate_log passes (so it commits), but the
    predicate raises F2PendingError (open-ended causal-sufficiency is co-gated by F2, deferred)."""
    payload = {
        "reasoning_steps": [
            {"step_index": 0, "step_type": "inference",
             "content": "Rayleigh scattering favours shorter wavelengths.",
             "depends_on": [], "produces": "blue", "tool_op_ref": None},
        ],
        "tool_operations": [],
        "final_output": {"value": "blue", "derived_from_step": 0},
    }
    log = agents.assemble_log(agent_id=did, model_id=agents.DEFAULT_MODEL, task=task,
                              reasoning_payload=payload, seed=1, temperature=0.7, sim_tick=1)
    return agents.finalize_and_commit(log, ledger)


def _commit_schema_invalid(ledger: SimulatedLedger, did: str, task: dict) -> str:
    """Commit a log that is otherwise valid but carries a FORBIDDEN section-0.7 ground-truth key
    ('is_colluder'). finalize_and_commit REFUSES such a log (it validates first), which is the
    point -- so we commit it DIRECTLY via ledger.commit_log (which enforces the content-address
    but NOT the schema: refute, don't prevent). Anchors are set so verify_log_integrity passes;
    the predicate then raises SchemaError when D validates it."""
    log = {
        "schema_version": agents.SCHEMA_VERSION,
        "agent_id": did,
        "model_id": agents.DEFAULT_MODEL,
        "task": {k: task[k] for k in ("task_id", "task_type", "dataset", "prompt")},
        "reasoning_steps": [
            {"step_index": 0, "step_type": "tool_call", "content": "Compute 12 + 25.",
             "depends_on": [], "produces": "37", "tool_op_ref": "op-0"},
        ],
        "tool_operations": [
            {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
             "output": "37", "deterministic": True, "evidence_hash": None},
        ],
        "final_output": {"value": "37", "derived_from_step": 0},
        "metadata": {"timestamp": "2026-06-29T00:00:00Z", "sim_tick": 1, "seed": 1,
                     "temperature": 0.7},
        "is_colluder": True,                       # <-- forbidden ground-truth key (section 0.7)
    }
    # sanity: the schema check really does reject it (so the specimen tests what we think)
    ok, _ = agents.validate_log(log, require_anchors=False)
    assert ok is False
    h = ledger.recompute_hash(log)
    log["log_id"] = h
    log["hash"] = h
    return ledger.commit_log(log)                  # content-address enforced; schema is not


def _recs(ledger: SimulatedLedger, ids_authors: list[tuple[str, str, str]]) -> list[consensus.LogRec]:
    """Build LogRec list from (log_id, task_id, author_id) triples."""
    return [consensus.LogRec(log_id=lid, task_id=tid, author_id=aid)
            for lid, tid, aid in ids_authors]


# ==============================================================================================
# (a) Honest validators emit ONE agreeing verdict set: same decision + identical digest
# ==============================================================================================
def test_honest_validators_agree():
    ledger = SimulatedLedger()
    dids = _dids(4)
    log_id = _commit_honest(ledger, dids[0], TASK_D, 12, 25, effort="standard")

    # three DISTINCT validators recompute the predicate over the same committed log
    verdicts = [validation.validate_committed(ledger, log_id, validator_id=v, sim_tick=1)
                for v in dids[1:4]]

    decisions = {v.decision for v in verdicts}
    digests = {v.recompute_digest for v in verdicts}
    assert decisions == {"accept"}                 # all agree on the decision
    assert len(digests) == 1                        # identical recompute_digest -> publicly refutable
    # they are nonetheless distinct attestations (validator identity differs, excluded from digest)
    assert len({v.validator_id for v in verdicts}) == 3
    assert len({v.verdict_id for v in verdicts}) == 3


# ==============================================================================================
# (b) Aggregation refuses to read verdicts before the reveal phase (verdict independence)
# ==============================================================================================
def test_aggregate_refuses_before_reveal():
    ledger = SimulatedLedger()
    dids = _dids(4)
    rng = __import__("random").Random(1)
    log_id = _commit_honest(ledger, dids[0], TASK_D, 12, 25)
    recs = _recs(ledger, [(log_id, TASK_D["task_id"], dids[0])])
    tasks = {TASK_D["task_id"]: TASK_D}

    consensus.init_population_reputation(ledger, dids)
    consensus.assign_peer_sets(ledger, recs, dids, k=3, harness_rng=rng, sim_tick=1)
    consensus.compute_log_verdicts(ledger, recs, sim_tick=1)
    consensus.commit_all_verdicts(ledger, recs, harness_rng=rng, sim_tick=1)
    assert ledger.phase == "commit"                 # committed, NOT yet revealed

    # aggregation must refuse: commitments are locked but not yet revealed
    with pytest.raises(consensus.ConsensusError):
        consensus.aggregate_round(ledger, recs, tasks, threshold=consensus.SUPERMAJORITY_THRESHOLD)

    # after a proper reveal, aggregation proceeds
    consensus.reveal_all_verdicts(ledger, recs, sim_tick=1)
    cons, _ = consensus.aggregate_round(ledger, recs, tasks,
                                        threshold=consensus.SUPERMAJORITY_THRESHOLD)
    # consensus is the CANONICAL answer (numeric-string "37" normalises to 37); compare with the
    # predicate's own equality so the assertion is representation-agnostic.
    assert metrics._values_equal(cons[TASK_D["task_id"]], "37", task_type="discrete", tol=0)


# ==============================================================================================
# (c) A log a supermajority of its peer set rejects is FLAGGED (detection, F4)
# ==============================================================================================
def test_supermajority_reject_is_flagged():
    ledger = SimulatedLedger()
    dids = _dids(4)
    fr_id = _commit_freerider(ledger, dids[0], TASK_D, answer="37")
    recs = _recs(ledger, [(fr_id, TASK_D["task_id"], dids[0])])
    tasks = {TASK_D["task_id"]: TASK_D}
    gt = {TASK_D["task_id"]: "37"}
    attacks = {fr_id: True}                          # harness label: this author free-rides

    res = consensus.run_round(ledger, recs, tasks, dids, gt, attacks,
                              harness_rng=__import__("random").Random(2), verbose=False)

    rec = recs[0]
    assert all(e["status"] == "reject" for e in rec.verdicts)   # every validator rejected
    assert rec.outcome == "reject"
    assert rec.flagged is True                       # supermajority reject -> flagged
    assert res["flagged_by_log"][fr_id] is True
    assert res["detection_rate"] == 1.0              # the one attacker was caught
    assert res["consensus_by_task"][TASK_D["task_id"]] is None   # no accepted log to aggregate
    assert res["ledger_ok"] is True


# ==============================================================================================
# (d) Reputation update is asymmetric, bounded in (0,1), and keyed to the VALIDATION OUTCOME
# ==============================================================================================
def test_reputation_is_asymmetric_bounded_keyed():
    up = consensus.reputation_update(0.5, "accept")
    down = consensus.reputation_update(0.5, "reject")
    # asymmetric: from the same point, a reject moves MORE than an accept (reject costs more)
    assert (0.5 - down) > (up - 0.5)
    assert up == pytest.approx(0.55)
    assert down == pytest.approx(0.35)

    # keyed to outcome: only accept/reject move reputation; deferred / no_consensus are no-ops
    assert consensus.reputation_update(0.5, "deferred") == 0.5
    assert consensus.reputation_update(0.5, "no_consensus") == 0.5

    # bounded + diminishing: many accepts approach but never reach 1; many rejects approach 0
    r = 0.5
    for _ in range(500):
        r = consensus.reputation_update(r, "accept")
    assert r < 1.0 and r <= consensus.REP_CEIL
    r = 0.5
    for _ in range(500):
        r = consensus.reputation_update(r, "reject")
    assert r > 0.0 and r >= consensus.REP_FLOOR

    # integration: keyed to outcome, anchored to a seal -> the ledger's verify() holds. An ACCEPT
    # author's reputation rises; a REJECT (free-rider) author's falls; both citing real seals.
    ledger = SimulatedLedger()
    dids = _dids(4)
    good = _commit_honest(ledger, dids[0], TASK_D, 12, 25)
    bad = _commit_freerider(ledger, dids[1], TASK_D, answer="37")
    recs = _recs(ledger, [(good, TASK_D["task_id"], dids[0]),
                          (bad, TASK_D["task_id"], dids[1])])
    tasks = {TASK_D["task_id"]: TASK_D}
    res = consensus.run_round(ledger, recs, tasks, dids,
                              {TASK_D["task_id"]: "37"}, {good: False, bad: True},
                              harness_rng=__import__("random").Random(3), verbose=False)
    assert res["final_reputation"][dids[0]] > consensus.REP_DEFAULT     # accept -> up
    assert res["final_reputation"][dids[1]] < consensus.REP_DEFAULT     # reject -> down
    assert res["ledger_ok"] is True                  # every reputation write anchors to a seal


# ==============================================================================================
# (e) A peer set containing one SchemaError + one F2Pending log still completes the round,
#     with BOTH excluded (and the honest logs aggregating normally)
# ==============================================================================================
def test_round_completes_with_excluded_logs():
    ledger = SimulatedLedger()
    dids = _dids(5)
    h0 = _commit_honest(ledger, dids[0], TASK_D, 12, 25, effort="minimal")
    h1 = _commit_honest(ledger, dids[1], TASK_D, 12, 25, effort="verbose")
    bad = _commit_schema_invalid(ledger, dids[2], TASK_D)        # validate -> SchemaError
    od = _commit_open_ended(ledger, dids[3], TASK_O)             # validate -> F2PendingError

    recs = _recs(ledger, [
        (h0, TASK_D["task_id"], dids[0]),
        (h1, TASK_D["task_id"], dids[1]),
        (bad, TASK_D["task_id"], dids[2]),
        (od, TASK_O["task_id"], dids[3]),
    ])
    tasks = {TASK_D["task_id"]: TASK_D, TASK_O["task_id"]: TASK_O}
    gt = {TASK_D["task_id"]: "37"}                  # open_ended task is unscored (deferred)
    attacks = {h0: False, h1: False, bad: True, od: False}

    # The round must COMPLETE despite a schema-invalid and a deferred log in the batch.
    res = consensus.run_round(ledger, recs, tasks, dids, gt, attacks,
                              harness_rng=__import__("random").Random(4), verbose=False)

    by_id = {r.log_id: r for r in recs}
    # honest logs validate and aggregate
    assert by_id[h0].status == "ok" and by_id[h0].outcome == "accept"
    assert by_id[h1].status == "ok" and by_id[h1].outcome == "accept"
    assert metrics._values_equal(res["consensus_by_task"][TASK_D["task_id"]], "37",
                                 task_type="discrete", tol=0)   # from the two honest logs only

    # schema-invalid log: excluded from aggregation, rejected, flagged, author reputation decremented
    assert by_id[bad].status == "schema_invalid"
    assert by_id[bad].outcome == "reject"
    assert by_id[bad].flagged is True
    assert res["final_reputation"][dids[2]] < consensus.REP_DEFAULT

    # F2-pending (open_ended) log: deferred, excluded from aggregation AND detection, NO reputation
    assert by_id[od].status == "open_ended_deferred"
    assert by_id[od].outcome == "deferred"
    assert by_id[od].flagged is False
    assert by_id[od].seal_id is None
    assert ledger.get_reputation(dids[3]) == consensus.REP_DEFAULT          # unchanged
    assert len(ledger.get_reputation_history(dids[3])) == 1                 # only genesis-init
    assert res["consensus_by_task"][TASK_O["task_id"]] is None              # deferred task

    assert res["ledger_ok"] is True                  # whole-ledger integrity holds throughout


# ==============================================================================================
# (f) Parity: metrics canonical equality MUST match the predicate's (guards silent drift)
# ==============================================================================================
@pytest.mark.parametrize("a,b,task_type", [
    ("37", 37, "discrete"),
    (37, 37.0, "discrete"),
    ("37", "37.0", "discrete"),
    ("37", 38, "discrete"),
    ("blue", "blue", "discrete"),
    ("blue", "red", "discrete"),
    (True, 1, "discrete"),          # bool is NOT a number -> not equal to 1
    (1.0, 1.0 + 1e-10, "continuous"),
    (1.0, 1.0 + 1e-6, "continuous"),
    ("3.14159", 3.14159, "continuous"),
])
def test_metrics_equality_parity(a, b, task_type):
    tol = validation.DEFAULT_CONTINUOUS_TOLERANCE
    assert metrics._canon_value(a) == validation._canon_value(a)
    assert metrics._canon_value(b) == validation._canon_value(b)
    assert (metrics._values_equal(a, b, task_type=task_type, tol=tol)
            == validation._values_equal(a, b, task_type=task_type, tol=tol))


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
