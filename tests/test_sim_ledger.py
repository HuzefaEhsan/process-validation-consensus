#!/usr/bin/env python3
"""
test_sim_ledger.py -- pytest suite for SimulatedLedger (Component A).

Covers the acceptance surface plus the confirmed design: in-object hash/log_id
anchors checked by verify(); enforced commit->reveal phase boundary; on-ledger
peer-set record; verdict-set seals with audit-time (not write-time) enforcement;
distinct genesis init path; None-valued (uninitialised) reputation.

Run:  pytest -q test_sim_ledger.py
"""
import copy
import secrets

import pytest

from sim_ledger import SimulatedLedger, PhaseError, _jcs, _sha256_hex, _now

AGENT = "did:key:z6MkpTHonest1"
VALIDATOR = "did:key:z6MkValidator1"


# ----------------------------- factories / fixtures -----------------------------
def build_log(answer: str = "37", tick: int = 42, agent: str = AGENT) -> dict:
    """A schema-conformant GSM8K-style log (section 2.3) with its content anchors set."""
    log = {
        "schema_version": "1.0", "log_id": "", "hash": "",
        "agent_id": agent, "model_id": "llama3.1:8b-instruct",
        "task": {"task_id": "gsm8k-00042", "task_type": "discrete", "dataset": "GSM8K",
                 "prompt": "A box has 12 red and 25 blue marbles. How many total?"},
        "reasoning_steps": [
            {"step_index": 0, "step_type": "inference", "content": "Total = red + blue.",
             "depends_on": [], "produces": None, "tool_op_ref": None},
            {"step_index": 1, "step_type": "tool_call", "content": "Compute 12 + 25.",
             "depends_on": [0], "produces": answer, "tool_op_ref": "op-0"},
            {"step_index": 2, "step_type": "decision", "content": "Total marbles is " + answer,
             "depends_on": [1], "produces": answer, "tool_op_ref": None}],
        "tool_operations": [
            {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
             "output": answer, "deterministic": True, "evidence_hash": None}],
        "final_output": {"value": answer, "derived_from_step": 2},
        "metadata": {"timestamp": _now(), "sim_tick": tick, "seed": 12345,
                     "temperature": 0.7, "runtime_ms": 812},
    }
    body = {k: v for k, v in log.items() if k not in ("hash", "log_id")}
    log["log_id"] = log["hash"] = _sha256_hex(_jcs(body))   # == ledger.recompute_hash(log)
    return log


def build_verdict(log_id: str, task_id: str, validator: str = VALIDATOR,
                  decision: str = "accept") -> dict:
    return {
        "verdict_id": "v-0001", "task_id": task_id, "log_id": log_id,
        "validator_id": validator, "decision": decision,
        "rule_results": {"process_coherence": True, "tool_utilization": True,
                         "causal_sufficiency": True, "cross_agent_coherence": 0.91},
        "recompute_digest": _sha256_hex(("rc::" + log_id).encode()),
        "sim_tick": 42, "timestamp": _now()}


@pytest.fixture
def led() -> SimulatedLedger:
    return SimulatedLedger()


@pytest.fixture
def committed(led):
    """Ledger with one committed log; returns (ledger, log, log_id)."""
    log = build_log()
    return led, log, led.commit_log(log)


def _full_attestation(led, log_id, task_id, validator=VALIDATOR, decision="accept"):
    """Run a complete commit->reveal attestation; returns commit_id."""
    verdict = build_verdict(log_id, task_id, validator, decision)
    salt = secrets.token_hex(16)
    commitment = SimulatedLedger._commitment(verdict, salt)
    commit_id = led.commit_verdict(task_id, log_id, validator, commitment, 42)
    led.begin_reveal_phase(43)
    assert led.reveal_verdict(commit_id, verdict, salt) is True
    return commit_id


def _seal(led, log, log_id, commit_id, outcome="accept"):
    return led.seal_verdict_set(log["task"]["task_id"], log_id, [commit_id], outcome, 44)


# ------------------------------ canonicalisation --------------------------------
def test_rfc8785_is_the_backend():
    import rfc8785  # hard dependency; absence raises at import of sim_ledger
    assert rfc8785 is not None


def test_jcs_is_deterministic_and_bytes():
    assert _jcs({"b": 1, "a": 2}) == _jcs({"a": 2, "b": 1})   # key-order independent
    assert isinstance(_jcs({"a": 1}), bytes)


# --------------------------- action log / content address ------------------------
def test_commit_log_is_content_addressed(committed):
    led, log, log_id = committed
    assert log_id == led.recompute_hash(log) == log["hash"] == log["log_id"]
    assert led.log_exists(log_id) is True
    assert led.get_log(log_id) is log
    assert led.verify_log_integrity(log_id) is True


def test_commit_log_rejects_wrong_hash(led):
    log = build_log()
    log["hash"] = "deadbeef" * 8
    with pytest.raises(ValueError):
        led.commit_log(log)


def test_commit_log_rejects_wrong_logid(led):
    """log_id must equal hash at commit (section 2.1) -- else the store would admit a
    record verify() immediately rejects."""
    log = build_log()
    log["log_id"] = "f" * 64                      # hash stays correct, log_id does not
    with pytest.raises(ValueError):
        led.commit_log(log)


def test_commit_log_is_idempotent_for_identical_bytes(led):
    log = build_log()
    log_id = led.commit_log(log)
    assert led.commit_log(copy.deepcopy(log)) == log_id   # same content -> no error


# ----------------------- commit-reveal phase boundary (5a) -----------------------
def test_phase_starts_in_commit(led):
    assert led.phase == "commit"


def test_reveal_before_commits_close_raises(committed):
    led, log, log_id = committed
    verdict = build_verdict(log_id, log["task"]["task_id"])
    salt = secrets.token_hex(16)
    commitment = SimulatedLedger._commitment(verdict, salt)
    cid = led.commit_verdict(log["task"]["task_id"], log_id, VALIDATOR, commitment, 42)
    with pytest.raises(PhaseError):             # reveals are not open during COMMIT
        led.reveal_verdict(cid, verdict, salt)


def test_commit_after_commits_close_raises(committed):
    led, log, log_id = committed
    led.begin_reveal_phase(43)                  # commits now closed
    assert led.phase == "reveal"
    with pytest.raises(PhaseError):
        led.commit_verdict(log["task"]["task_id"], log_id, VALIDATOR, "x" * 64, 44)


def test_phase_error_is_runtimeerror():
    assert issubclass(PhaseError, RuntimeError)   # backward-compatible with raises(RuntimeError)


def test_commit_reveal_happy_path(committed):
    led, log, log_id = committed
    assert len(led.get_verdicts(log_id, "committed")) == 0
    commit_id = _full_attestation(led, log_id, log["task"]["task_id"])
    assert led.verify_verdict(commit_id) is True
    revealed = led.get_verdicts(log_id, "revealed")
    assert len(revealed) == 1 and revealed[0]["decision"] == "accept"
    assert revealed[0]["rule_results"]["cross_agent_coherence"] == 0.91


def test_reveal_mismatch_returns_false(committed):
    led, log, log_id = committed
    verdict = build_verdict(log_id, log["task"]["task_id"])
    salt = secrets.token_hex(16)
    commitment = SimulatedLedger._commitment(verdict, salt)
    cid = led.commit_verdict(log["task"]["task_id"], log_id, VALIDATOR, commitment, 42)
    led.begin_reveal_phase(43)
    assert led.reveal_verdict(cid, verdict, "wrong-salt") is False   # crypto mismatch
    assert led.get_history(filter={"tx_type": "reveal_fail"})        # event recorded


def test_equivocation_detected(committed):
    led, log, log_id = committed
    led.commit_verdict(log["task"]["task_id"], log_id, VALIDATOR, "a" * 64, 1)
    led.commit_verdict(log["task"]["task_id"], log_id, VALIDATOR, "b" * 64, 1)
    assert led.is_equivocating(VALIDATOR, log_id) is True


# ------------------------- peer-set record (5b: on-ledger) -----------------------
def test_peer_set_recorded_on_ledger(committed):
    led, log, log_id = committed
    roster = ["did:key:zV1", "did:key:zV2", "did:key:zV3"]
    aid = led.assign_peer_set(log["task"]["task_id"], log_id, roster, seed=999, sim_tick=1)
    ps = led.get_peer_set(log["task"]["task_id"], log_id)
    assert ps["validators"] == roster and ps["seed"] == 999 and ps["assignment_id"] == aid
    assert led.get_history(filter={"tx_type": "peer_set"})


# --------------------- verdict-set seal (5c: record + audit) ---------------------
def test_seal_verdict_set_recorded(committed):
    led, log, log_id = committed
    cid = _full_attestation(led, log_id, log["task"]["task_id"])
    seal_id = _seal(led, log, log_id, cid, outcome="accept")
    seal = led.get_seal(seal_id)
    assert seal["outcome"] == "accept" and seal["verdict_commit_ids"] == [cid]
    assert led.get_history(filter={"tx_type": "verdict_seal"})


# ------------------ reputation: None default + genesis + seal anchor -------------
def test_unknown_agent_reputation_is_none(led):
    assert led.get_reputation("did:key:never-seen") is None


def test_init_reputation_marks_genesis(led):
    assert led.get_reputation(AGENT) is None
    led.init_reputation(AGENT, 0.5, sim_tick=0)        # 0.5 is D's policy; passed in
    assert led.get_reputation(AGENT) == 0.5
    hist = led.get_reputation_history(AGENT)
    assert len(hist) == 1 and hist[0]["prev"] is None and hist[0]["reason_ref"] == "genesis-init"
    assert led.verify() is True                        # genesis-init is whitelisted


def test_init_reputation_rejects_reinit(led):
    led.init_reputation(AGENT, 0.5, 0)
    with pytest.raises(ValueError):
        led.init_reputation(AGENT, 0.7, 1)


def test_set_reputation_anchors_to_seal_and_verifies(committed):
    led, log, log_id = committed
    cid = _full_attestation(led, log_id, log["task"]["task_id"])
    seal_id = _seal(led, log, log_id, cid)
    led.init_reputation(AGENT, 0.5, 0)
    led.set_reputation(AGENT, 0.6, reason_ref=seal_id)   # update anchored to a real seal
    assert led.get_reputation(AGENT) == 0.6
    hist = led.get_reputation_history(AGENT)
    assert hist[-1]["prev"] == 0.5 and hist[-1]["reason_ref"] == seal_id
    assert led.verify() is True


def test_set_reputation_uses_explicit_sim_tick(committed):
    led, log, log_id = committed
    cid = _full_attestation(led, log_id, log["task"]["task_id"])
    seal_id = _seal(led, log, log_id, cid)
    led.init_reputation(AGENT, 0.5, 0)
    led.set_reputation(AGENT, 0.6, reason_ref=seal_id, sim_tick=99)
    rep_tx = led.get_history(filter={"tx_type": "reputation_set"})[-1]
    assert rep_tx["sim_tick"] == 99


def test_set_reputation_inherits_seal_tick(committed):
    led, log, log_id = committed
    cid = _full_attestation(led, log_id, log["task"]["task_id"])
    seal_id = led.seal_verdict_set(log["task"]["task_id"], log_id, [cid], "accept", 77)
    led.init_reputation(AGENT, 0.5, 0)
    led.set_reputation(AGENT, 0.6, reason_ref=seal_id)          # no tick -> inherit seal's 77
    rep_tx = led.get_history(filter={"tx_type": "reputation_set"})[-1]
    assert rep_tx["sim_tick"] == 77


def test_verify_detects_unanchored_reputation(committed):
    led, log, log_id = committed
    led.set_reputation(AGENT, 0.6, reason_ref="not-a-real-seal")  # write NOT blocked...
    assert led.verify() is False                                  # ...but audit catches it


# ------------------------------- hash-chain audit --------------------------------
def test_chain_is_hash_linked(committed):
    led, log, log_id = committed
    _full_attestation(led, log_id, log["task"]["task_id"])
    led.init_reputation(AGENT, 0.5, 0)
    chain = led.get_history()
    assert len(chain) >= 4
    prev = "0" * 64
    for tx in chain:
        assert tx["prev_hash"] == prev
        prev = tx["tx_hash"]
    assert led.head()["tx_hash"] == chain[-1]["tx_hash"]


# ------------------------------- tamper detection --------------------------------
def test_clean_ledger_verifies(committed):
    led, log, log_id = committed
    cid = _full_attestation(led, log_id, log["task"]["task_id"])
    led.assign_peer_set(log["task"]["task_id"], log_id, [VALIDATOR], seed=1, sim_tick=1)
    seal_id = _seal(led, log, log_id, cid)
    led.init_reputation(AGENT, 0.5, 0)
    led.set_reputation(AGENT, 0.55, reason_ref=seal_id)
    assert led.verify() is True


def test_verify_detects_log_content_tamper(committed):
    led, log, log_id = committed
    assert led.verify() is True
    led._logs[log_id]["final_output"]["value"] = "41"        # 37 -> 41
    assert led.verify() is False
    assert led.verify_log_integrity(log_id) is False


def test_verify_detects_inobject_hash_anchor_tamper(committed):
    """recompute_hash ignores 'hash'/'log_id'; the hardening catches anchor edits."""
    led, log, log_id = committed
    led._logs[log_id]["hash"] = "0" * 64                     # edit redundant in-object anchor
    assert led.verify() is False


def test_verify_detects_inobject_logid_anchor_tamper(committed):
    led, log, log_id = committed
    led._logs[log_id]["log_id"] = "0" * 64
    assert led.verify() is False


def test_verify_detects_chain_tamper(committed):
    led, log, log_id = committed
    led._chain[0]["payload"]["agent_id"] = "did:key:evil"    # mutate a recorded tx
    assert led.verify() is False
