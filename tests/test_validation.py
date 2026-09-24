#!/usr/bin/env python3
"""
test_validation.py -- pytest suite for the Validation Predicate (Component C, validation.py).

METHODOLOGY (matches A.3): every log under test is a REAL committed section-2 log. We do not
hand-mint dicts and feed them straight to the predicate; instead we script the model output
(`ScriptedModelClient`), drive the audited Component-B path (`Agent.solve_and_commit` ->
`produce_log` -> `assemble_log` -> `finalize_and_commit`), and pull the committed log back out
of the `SimulatedLedger`. So the predicate is always exercised on bytes that actually passed the
section-2 schema check AND the ledger's content-address commit -- exactly the substrate public
refutability presumes. (The one structural-precondition test deliberately bypasses the ledger to
feed an INVALID dict, proving the predicate refuses it.)

The forged / free-rider logs are STRUCTURALLY VALID (they pass validate_log and commit cleanly) but
CAUSALLY BAD -- that separation is the whole architecture: schema validity is necessary, causal
sufficiency is the actual gate. A bad actor can emit well-formed JSON; it cannot make a forged
computation re-execute.

MANDATED CASES (a)-(e) from the build order are marked [MANDATE a]..[MANDATE e].
Additional checks are marked [SUPPLEMENTARY]. Nothing here fabricates a passing log or a citation.
"""
from __future__ import annotations

import json

import pytest

from sim_ledger import SimulatedLedger
from agents import (
    AgentConfig, ConsensusModel, ScriptedModelClient, make_did,
    assemble_log, SchemaError, DEFAULT_MODEL,
)
import validation
from validation import validate, validate_committed, Verdict, F2PendingError, F2_PENDING


# ==============================================================================================
# Tasks (the harness-owned task echo; the "model" only authors the reasoning payload)
# ==============================================================================================
DISCRETE_TASK = {
    "task_id": "gsm8k-00042",
    "task_type": "discrete",
    "dataset": "GSM8K",
    "prompt": "A box has 12 red and 25 blue marbles. How many marbles total?",
}

CONTINUOUS_TASK = {
    "task_id": "cont-00007",
    "task_type": "continuous",
    "dataset": "synthetic-continuous",
    "prompt": "Compute 22 / 7 to six decimal places.",
}

OPEN_TASK = {
    "task_id": "open-00001",
    "task_type": "open_ended",
    "dataset": "synthetic-open",
    "prompt": "In one sentence, explain why the sky appears blue.",
}


# ==============================================================================================
# Helper: build ONE real committed log via the Component-B agent path, return (log, log_id, ledger)
# ==============================================================================================
def commit_log(payload: dict, task: dict, *, ledger: SimulatedLedger | None = None,
               idx: int = 0, seed: int = 101, sim_tick: int = 1):
    """Script `payload` as the model output, run the real agent commit path, and return the
    COMMITTED log dict (read back from the ledger), its log_id, and the ledger.

    `behavior_class` is irrelevant to validation (it is a harness label, never written into the
    log), so it stays "honest" here even for adversarial payloads -- the payload itself is what is
    adversarial, which is the point: nothing in the committed bytes announces intent.
    """
    ledger = ledger or SimulatedLedger()
    cfg = AgentConfig(agent_id=make_did(idx), model_id=DEFAULT_MODEL, seed=seed,
                      temperature=0.7, behavior_class="honest")
    client = ScriptedModelClient(json.dumps(payload))
    model = ConsensusModel([cfg], [client], ledger, task, seed=7)
    agent = list(model.agents)[0]
    _final, log_id, _log = agent.solve_and_commit(task, ledger, sim_tick)
    committed = ledger.get_log(log_id)
    assert committed is not None, "log was not committed to the ledger"
    assert ledger.verify_log_integrity(log_id), "committed log fails content-address integrity"
    return committed, log_id, ledger


# ==============================================================================================
# Canonical reasoning payloads (the "model output")
# ==============================================================================================

# (a) HONEST valid discrete: 12 + 25 -> 37 via one calculator op. Causally sufficient.
PAYLOAD_HONEST = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Total marbles = red + blue.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute 12 + 25.",
         "depends_on": [0], "produces": "37", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "The total is 37.",
         "depends_on": [1], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
         "output": "37", "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": "37", "derived_from_step": 2},
}

# (b) FREE-RIDER: asserts 37 with NO grounding computation (no tool op at all). Schema-valid;
#     causally INSUFFICIENT -- the answer is a bare claim, reproduced by nothing (C4 fails).
PAYLOAD_FREERIDER_BARE = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "This is a simple addition.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "decision", "content": "The answer is 37.",
         "depends_on": [0], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [],
    "final_output": {"value": "37", "derived_from_step": 1},
}

# (c) FORGED: the committed computation is 12 + 24 (= 36), but the op claims output 37 and the
#     final answer is 37. Output-inspection sees a "correct" 37; re-execution catches the forged
#     op (12 + 24 != 37). This is the headline case output-based methods are blind to.
PAYLOAD_FORGED = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Sum the two counts.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute the sum.",
         "depends_on": [0], "produces": "37", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "The total is 37.",
         "depends_on": [1], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+24"},
         "output": "37", "deterministic": True, "evidence_hash": None},  # 12+24 == 36, NOT 37
    ],
    "final_output": {"value": "37", "derived_from_step": 2},
}

# (d) HONEST-BUT-DIVERGENT: same answer (37) via a DIFFERENT valid two-op path: 10+25=35, 35+2=37.
#     Both ops re-execute; the answer is grounded. Must ACCEPT on its own merits -- the FPR<5% /
#     condition-(iii) guard (honest divergence is NOT a Byzantine signal).
PAYLOAD_DIVERGENT = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference",
         "content": "Split 12 into 10 + 2 to add in two easy steps.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute 10 + 25.",
         "depends_on": [0], "produces": "35", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "tool_call", "content": "Add the remaining 2: 35 + 2.",
         "depends_on": [1], "produces": "37", "tool_op_ref": "op-1"},
        {"step_index": 3, "step_type": "decision", "content": "The total is 37.",
         "depends_on": [2], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "10+25"},
         "output": "35", "deterministic": True, "evidence_hash": None},
        {"op_id": "op-1", "tool_name": "calculator", "inputs": {"expr": "35+2"},
         "output": "37", "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": "37", "derived_from_step": 3},
}

# (e) OPEN-ENDED: schema-valid open_ended log. The predicate must REFUSE to score it (F2-pending).
PAYLOAD_OPEN = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference",
         "content": "Sunlight scatters off air molecules.", "depends_on": [],
         "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "decision",
         "content": "Short-wavelength blue light scatters most, so the sky looks blue.",
         "depends_on": [0], "produces": "Rayleigh scattering favors blue light.",
         "tool_op_ref": None},
    ],
    "tool_operations": [],
    "final_output": {"value": "Rayleigh scattering favors blue light.", "derived_from_step": 1},
}

# [SUPPLEMENTARY] UNUSED-OP free-rider: a real, CORRECT calculator op (12+25=37) grounds the
#   answer, but a SECOND correct op (99+1=100) is attached and referenced by nobody -- computed
#   and discarded. C1 passes (both ops re-execute); Tool Utilization fails on the orphan op.
PAYLOAD_UNUSED_OP = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Compute the sum.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute 12 + 25.",
         "depends_on": [0], "produces": "37", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "The total is 37.",
         "depends_on": [1], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
         "output": "37", "deterministic": True, "evidence_hash": None},       # USED, correct
        {"op_id": "op-1", "tool_name": "calculator", "inputs": {"expr": "99+1"},
         "output": "100", "deterministic": True, "evidence_hash": None},      # UNUSED, correct
    ],
    "final_output": {"value": "37", "derived_from_step": 2},
}

# [SUPPLEMENTARY] CONTINUOUS accept: 22/7 ~= 3.142857; recorded 3.142857 is within 1e-5 of the
#   re-executed value (3.142857142857143). Accepted under a STATED tolerance.
PAYLOAD_CONT_OK = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Divide 22 by 7.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute 22 / 7.",
         "depends_on": [0], "produces": 3.142857, "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "The value is about 3.142857.",
         "depends_on": [1], "produces": 3.142857, "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "22/7"},
         "output": 3.142857, "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": 3.142857, "derived_from_step": 2},
}

# [SUPPLEMENTARY] CONTINUOUS reject: same computation, but the recorded op output (3.15) is OUTSIDE
#   tolerance of the re-executed 22/7 -- a forged/erroneous continuous result the tolerance catches.
PAYLOAD_CONT_BAD = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Divide 22 by 7.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute 22 / 7.",
         "depends_on": [0], "produces": 3.15, "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "The value is about 3.15.",
         "depends_on": [1], "produces": 3.15, "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "22/7"},
         "output": 3.15, "deterministic": True, "evidence_hash": None},  # 22/7 ~ 3.1428, NOT 3.15
    ],
    "final_output": {"value": 3.15, "derived_from_step": 2},
}


# ==============================================================================================
# (a) MANDATE -- honest valid discrete log -> ACCEPT
# ==============================================================================================
def test_a_honest_discrete_accepts():
    log, log_id, ledger = commit_log(PAYLOAD_HONEST, DISCRETE_TASK)
    v = validate(log)
    assert isinstance(v, Verdict)
    assert v.decision == "accept"
    assert v.rule_results["process_coherence"] is True
    assert v.rule_results["tool_utilization"] is True
    assert v.rule_results["causal_sufficiency"] is True
    # no peers supplied -> the cross-agent signal is absent (None), never fabricated
    assert v.rule_results["cross_agent_coherence"] is None
    assert v.log_id == log_id
    assert v.task_id == DISCRETE_TASK["task_id"]
    assert len(v.recompute_digest) == 64  # SHA-256 hex


# ==============================================================================================
# (b) MANDATE -- free-rider, causally insufficient (answer reproduced by nothing) -> REJECT
# ==============================================================================================
def test_b_freerider_bare_assertion_rejects():
    log, _id, _ledger = commit_log(PAYLOAD_FREERIDER_BARE, DISCRETE_TASK)
    v = validate(log)
    assert v.decision == "reject"
    # The structure is fine; the answer is simply not GROUNDED in any re-executed computation.
    assert v.rule_results["process_coherence"] is True
    assert v.rule_results["tool_utilization"] is True       # vacuous: no tool ops to misuse
    assert v.rule_results["causal_sufficiency"] is False    # the linchpin: C4 (grounding) fails


# ==============================================================================================
# (c) MANDATE -- forged log whose steps do not reproduce the output -> REJECT (the headline)
# ==============================================================================================
def test_c_forged_op_rejects_even_though_answer_looks_correct():
    log, _id, _ledger = commit_log(PAYLOAD_FORGED, DISCRETE_TASK)
    # The final answer (37) is exactly the "right" GSM8K answer, so an OUTPUT comparison would pass.
    assert log["final_output"]["value"] == "37"
    v = validate(log)
    assert v.decision == "reject"
    assert v.rule_results["causal_sufficiency"] is False
    # Confirm the rejection is specifically the re-execution mismatch (12+24 != 37), i.e. C1 --
    # the predicate caught a forged tool output that output-inspection cannot see.
    full = validation._causal_sufficiency_numeric(log, task_type="discrete", tol=0.0)[1]
    assert full["subchecks"]["C1_deterministic_ops_reexecute"] is False
    reexec = full["op_reexec"][0]
    assert reexec["recomputed"] == 36 and reexec["match"] is False


# ==============================================================================================
# (d) MANDATE -- honest-but-DIVERGENT valid log (different correct path, same answer) -> ACCEPT
#     The condition-(iii) / FPR<5% guard: the one most likely to be wrong.
# ==============================================================================================
def test_d_divergent_valid_path_accepts():
    log, _id, _ledger = commit_log(PAYLOAD_DIVERGENT, DISCRETE_TASK)
    v = validate(log)
    assert v.decision == "accept"
    assert v.rule_results["process_coherence"] is True
    assert v.rule_results["tool_utilization"] is True
    assert v.rule_results["causal_sufficiency"] is True
    # Same answer as the honest log, reached by a genuinely different re-executable computation.
    assert log["final_output"]["value"] == "37"
    assert len(log["tool_operations"]) == 2


# ==============================================================================================
# (e) MANDATE -- open_ended -> F2-pending stub behaves as designed (NO fabricated score)
# ==============================================================================================
def test_e_open_ended_raises_f2_pending():
    log, log_id, _ledger = commit_log(PAYLOAD_OPEN, OPEN_TASK)
    with pytest.raises(F2PendingError) as ei:
        validate(log)
    err = ei.value
    # The mechanical, task-type-independent gates ARE computed and attached; only the open-ended
    # causal-sufficiency scorer is withheld (co-gated by F2) -- represented by the sentinel.
    assert err.log_id == log_id
    assert err.partial_rule_results["causal_sufficiency"] == F2_PENDING
    assert err.partial_rule_results["process_coherence"] is True
    assert err.partial_rule_results["tool_utilization"] is True
    # And the stub itself never returns a bool decision.
    assert validation._causal_sufficiency_open_ended(log) == F2_PENDING


# ==============================================================================================
# [SUPPLEMENTARY] unused (orphan) tool op -> Tool Utilization fails -> REJECT
# ==============================================================================================
def test_supp_unused_tool_op_rejects_on_tool_utilization():
    log, _id, _ledger = commit_log(PAYLOAD_UNUSED_OP, DISCRETE_TASK)
    v = validate(log)
    assert v.decision == "reject"
    # C1 is fine (both ops re-execute correctly); the failure is the discarded op specifically.
    assert v.rule_results["tool_utilization"] is False
    tu = validation._tool_utilization(log)[1]
    assert tu["per_op"]["op-0"]["feeds_answer"] is True
    assert tu["per_op"]["op-1"]["feeds_answer"] is False


# ==============================================================================================
# [SUPPLEMENTARY] continuous within stated tolerance -> ACCEPT
# ==============================================================================================
def test_supp_continuous_within_tolerance_accepts():
    log, _id, _ledger = commit_log(PAYLOAD_CONT_OK, CONTINUOUS_TASK)
    v = validate(log, tolerance=1e-5)
    assert v.decision == "accept"
    assert v.rule_results["causal_sufficiency"] is True


# ==============================================================================================
# [SUPPLEMENTARY] continuous outside tolerance -> REJECT (tolerance is a real gate, not cosmetic)
# ==============================================================================================
def test_supp_continuous_outside_tolerance_rejects():
    log, _id, _ledger = commit_log(PAYLOAD_CONT_BAD, CONTINUOUS_TASK)
    v = validate(log, tolerance=1e-5)
    assert v.decision == "reject"
    assert v.rule_results["causal_sufficiency"] is False


# ==============================================================================================
# [SUPPLEMENTARY] cross-agent coherence is a SIGNAL, never a gate:
#   an honest, self-consistent log whose answer DIVERGES from all peers still ACCEPTS, and the
#   signal reports the low agreement (0.0) alongside -- it does not affect the decision.
# ==============================================================================================
def test_supp_cross_agent_coherence_is_signal_not_gate():
    ledger = SimulatedLedger()
    focal, _fid, _ = commit_log(PAYLOAD_HONEST, DISCRETE_TASK, ledger=ledger, idx=0)

    # two peers that solve the SAME task but (honestly) reach a different answer, 100, via 99+1.
    peer_payload = {
        "reasoning_steps": [
            {"step_index": 0, "step_type": "inference", "content": "Add the two counts.",
             "depends_on": [], "produces": None, "tool_op_ref": None},
            {"step_index": 1, "step_type": "tool_call", "content": "Compute 99 + 1.",
             "depends_on": [0], "produces": "100", "tool_op_ref": "op-0"},
            {"step_index": 2, "step_type": "decision", "content": "The total is 100.",
             "depends_on": [1], "produces": "100", "tool_op_ref": None},
        ],
        "tool_operations": [
            {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "99+1"},
             "output": "100", "deterministic": True, "evidence_hash": None},
        ],
        "final_output": {"value": "100", "derived_from_step": 2},
    }
    peer1, _p1, _ = commit_log(peer_payload, DISCRETE_TASK, ledger=ledger, idx=1)
    peer2, _p2, _ = commit_log(peer_payload, DISCRETE_TASK, ledger=ledger, idx=2)

    v = validate(focal, peers=[peer1, peer2])
    assert v.decision == "accept"                          # gates pass; majority-mismatch ignored
    assert v.rule_results["cross_agent_coherence"] == 0.0  # signal present, reports disagreement

    # And the signal is EXCLUDED from the digest: the digest with peers == the digest without.
    v_no_peers = validate(focal)
    assert v.recompute_digest == v_no_peers.recompute_digest


# ==============================================================================================
# [SUPPLEMENTARY] recompute_digest is stable & reproducible (the public-refutability object):
#   re-validating the same immutable log yields the same digest and the same verdict_id.
# ==============================================================================================
def test_supp_recompute_digest_is_reproducible():
    log, _id, _ledger = commit_log(PAYLOAD_HONEST, DISCRETE_TASK)
    v1 = validate(log, validator_id="did:key:zVALIDATOR1")
    v2 = validate(log, validator_id="did:key:zVALIDATOR1")
    assert v1.recompute_digest == v2.recompute_digest
    assert v1.verdict_id == v2.verdict_id
    # A DIFFERENT validator recomputes the SAME digest (digest excludes validator_id) -- which is
    # exactly what makes a dishonest verdict on this log publicly refutable.
    v3 = validate(log, validator_id="did:key:zVALIDATOR2")
    assert v3.recompute_digest == v1.recompute_digest
    assert v3.verdict_id != v1.verdict_id                  # verdict_id is per-(validator, log)


# ==============================================================================================
# [SUPPLEMENTARY] structural precondition: a STRUCTURALLY INVALID log is refused with SchemaError
#   (causal-sufficiency runs ONLY on a structurally valid log). Bypasses the ledger on purpose,
#   since finalize_and_commit would itself reject this dict.
# ==============================================================================================
def test_supp_structurally_invalid_log_raises_schema_error():
    bad_payload = {
        "reasoning_steps": [
            # step 0 forward-references step 1 -- a DAG violation validate_log rejects.
            {"step_index": 0, "step_type": "inference", "content": "Forward reference.",
             "depends_on": [1], "produces": None, "tool_op_ref": None},
            {"step_index": 1, "step_type": "decision", "content": "Answer.",
             "depends_on": [], "produces": "37", "tool_op_ref": None},
        ],
        "tool_operations": [],
        "final_output": {"value": "37", "derived_from_step": 1},
    }
    invalid_log = assemble_log(
        agent_id=make_did(0), model_id=DEFAULT_MODEL, task=DISCRETE_TASK,
        reasoning_payload=bad_payload, seed=101, temperature=0.7, sim_tick=1,
    )
    with pytest.raises(SchemaError):
        validate(invalid_log)


# ==============================================================================================
# [SUPPLEMENTARY] validate_committed ties the predicate to the immutable substrate:
#   it verifies content-address integrity before judging, and refuses a tampered log.
# ==============================================================================================
def test_supp_validate_committed_matches_and_rejects_tampered():
    log, log_id, ledger = commit_log(PAYLOAD_HONEST, DISCRETE_TASK)

    direct = validate(log)
    vialedger = validate_committed(ledger, log_id)
    assert vialedger.decision == direct.decision == "accept"
    assert vialedger.recompute_digest == direct.recompute_digest

    # Tamper the stored bytes (the ledger hands back the live object): the content hash no longer
    # matches the stored log_id, so validate_committed must refuse rather than judge altered bytes.
    ledger.get_log(log_id)["final_output"]["value"] = "999"
    assert ledger.verify_log_integrity(log_id) is False
    with pytest.raises(ValueError):
        validate_committed(ledger, log_id)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
