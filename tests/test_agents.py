#!/usr/bin/env python3
"""
test_agents.py -- pytest cases for the Agent Layer (Component B).

Covers the acceptance check, all WITHOUT a live LLM (canned model responses):
  * a valid emitted log passes validation, commits, and verify_log_integrity is True;
  * a malformed log is rejected (validate_log + the commit gate), several ways;
  * the committed log appears in get_history (tx_type == 'log_commit');
  * model/temperature/seed are configurable and recorded in metadata;
  * structured-emission failure is handled (retry recovers; persistent failure raises and
    commits nothing);
  * tamper-evidence: mutating a stored log flips verify_log_integrity / verify() to False;
  * the section-2.3 worked example validates (golden).

Run:  pytest -v test_agents.py
"""
from __future__ import annotations

import copy
import json

import mesa
import pytest

import agents
from agents import (
    Agent, AgentConfig, ScriptedModelClient, SchemaError, StructuredEmissionError,
    assemble_log, validate_log, finalize_and_commit, make_did,
)
from sim_ledger import SimulatedLedger


TASK = {
    "task_id": "gsm8k-00042",
    "task_type": "discrete",
    "dataset": "GSM8K",
    "prompt": "A box has 12 red and 25 blue marbles. How many marbles total?",
}

# A valid reasoning payload (the "model" output) -- mirrors the section-2.3 example shape.
VALID_PAYLOAD = {
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Total = red + blue.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute 12 + 25.",
         "depends_on": [0], "produces": "37", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "Total marbles is 37.",
         "depends_on": [1], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
         "output": "37", "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": "37", "derived_from_step": 2},
}
VALID_PAYLOAD_STR = json.dumps(VALID_PAYLOAD)


class _BareModel(mesa.Model):
    """Headless Mesa model just to host an Agent in tests."""
    def __init__(self):
        super().__init__(rng=0)


def _emitted_anchorless_log(seed=123, temperature=0.7, sim_tick=42, model_id="llama3.1:8b"):
    """Assemble a valid anchorless log directly from the canned payload (no LLM, no Agent)."""
    return assemble_log(
        agent_id=make_did(0), model_id=model_id, task=TASK,
        reasoning_payload=copy.deepcopy(VALID_PAYLOAD),
        seed=seed, temperature=temperature, sim_tick=sim_tick, runtime_ms=12.5,
        timestamp="2026-06-03T10:15:30Z",
    )


# ---------------------------------------------------------------------------
# 1. valid emitted log: passes validation, commits, verify_log_integrity True
# ---------------------------------------------------------------------------

def test_valid_log_passes_validation_commits_and_verifies():
    ledger = SimulatedLedger()
    log = _emitted_anchorless_log()

    ok, errors = validate_log(log)
    assert ok, errors
    assert "hash" not in log and "log_id" not in log  # emitted anchorless

    log_id = finalize_and_commit(log, ledger)

    assert log["log_id"] == log_id == log["hash"]
    assert ledger.verify_log_integrity(log_id) is True
    assert ledger.get_log(log_id) == log
    assert ledger.log_exists(log_id) is True
    assert ledger.verify() is True
    # full anchored log also validates under require_anchors=True
    assert validate_log(log, require_anchors=True)[0]


def test_valid_log_via_mesa_agent_path():
    """End-to-end through the Mesa Agent subclass with a canned client (no LLM)."""
    ledger = SimulatedLedger()
    model = _BareModel()
    cfg = AgentConfig(agent_id=make_did(1), model_id="llama3.1:8b", seed=7, temperature=0.2)
    agent = Agent(model, cfg, ScriptedModelClient(VALID_PAYLOAD_STR), ledger)

    final_output, log_id, log = agent.solve_and_commit(TASK, ledger, sim_tick=5)

    assert final_output == {"value": "37", "derived_from_step": 2}
    assert ledger.verify_log_integrity(log_id) is True
    assert log["agent_id"] == cfg.agent_id
    assert agent.results[-1]["log_id"] == log_id
    # the agent is a real Mesa agent in the model's AgentSet
    assert agent in model.agents and isinstance(agent.unique_id, int)


def test_model_temperature_seed_configurable_and_recorded():
    log = _emitted_anchorless_log(seed=999, temperature=0.0, model_id="llama3.1:70b")
    assert log["model_id"] == "llama3.1:70b"
    assert log["metadata"]["seed"] == 999
    assert log["metadata"]["temperature"] == 0.0
    # seed may be null (LLM seed not pinned) and still validate
    log2 = _emitted_anchorless_log(seed=None)
    log2["metadata"]["seed"] = None
    assert validate_log(log2)[0]


# ---------------------------------------------------------------------------
# 2. malformed logs are rejected (validate_log) and never committed
# ---------------------------------------------------------------------------

def _mutate(builder):
    log = _emitted_anchorless_log()
    builder(log)
    return log


@pytest.mark.parametrize("name,builder", [
    ("missing_required_field",
     lambda lg: lg.pop("model_id")),
    ("bad_agent_id_not_did",
     lambda lg: lg.__setitem__("agent_id", "agent-1")),
    ("wrong_type_step_index",
     lambda lg: lg["reasoning_steps"][1].__setitem__("step_index", "1")),
    ("forward_dependency",
     lambda lg: lg["reasoning_steps"][0].__setitem__("depends_on", [2])),
    ("self_dependency",
     lambda lg: lg["reasoning_steps"][1].__setitem__("depends_on", [1])),
    ("unresolved_tool_op_ref",
     lambda lg: lg["reasoning_steps"][1].__setitem__("tool_op_ref", "op-404")),
    ("tool_call_without_ref",
     lambda lg: lg["reasoning_steps"][1].__setitem__("tool_op_ref", None)),
    ("derived_from_step_out_of_range",
     lambda lg: lg["final_output"].__setitem__("derived_from_step", 99)),
    ("non_bool_determinism",
     lambda lg: lg["tool_operations"][0].__setitem__("deterministic", "true")),
    ("missing_metadata_seed_key",
     lambda lg: lg["metadata"].pop("seed")),
    ("empty_reasoning_steps",
     lambda lg: lg.__setitem__("reasoning_steps", [])),
    ("bad_task_type_enum",
     lambda lg: lg["task"].__setitem__("task_type", "freeform")),
    ("ground_truth_label_leak",
     lambda lg: lg.__setitem__("is_honest", True)),
    ("nested_effort_label_leak",
     lambda lg: lg["metadata"].__setitem__("effort_label", "low")),
])
def test_malformed_log_is_rejected(name, builder):
    log = _mutate(builder)
    ok, errors = validate_log(log)
    assert not ok, f"{name}: expected rejection but validate_log passed"
    assert errors, f"{name}: rejected but no error message"

    # the commit gate must also refuse it (nothing reaches the store)
    ledger = SimulatedLedger()
    with pytest.raises(SchemaError):
        finalize_and_commit(log, ledger)
    assert ledger.get_history({"tx_type": "log_commit"}) == []


def test_finalize_rejects_pre_anchored_log():
    """finalize_and_commit expects an anchorless log; pre-set anchors are refused."""
    ledger = SimulatedLedger()
    log = _emitted_anchorless_log()
    log["hash"] = "00"
    log["log_id"] = "00"
    with pytest.raises(SchemaError):
        finalize_and_commit(log, ledger)


# ---------------------------------------------------------------------------
# 3. committed log appears in get_history
# ---------------------------------------------------------------------------

def test_committed_log_appears_in_get_history():
    ledger = SimulatedLedger()
    log = _emitted_anchorless_log(sim_tick=42)
    log_id = finalize_and_commit(log, ledger)

    commits = ledger.get_history({"tx_type": "log_commit"})
    assert len(commits) == 1
    tx = commits[0]
    assert tx["payload"]["log_id"] == log_id
    assert tx["payload"]["task_id"] == TASK["task_id"]
    assert tx["payload"]["agent_id"] == log["agent_id"]
    assert tx["sim_tick"] == 42
    # and it is discoverable in the unfiltered chain too
    assert any(t["tx_hash"] == tx["tx_hash"] for t in ledger.get_history())


# ---------------------------------------------------------------------------
# 4. structured-emission failure handling (retry recovers; persistent fails)
# ---------------------------------------------------------------------------

def test_retry_recovers_from_malformed_then_valid():
    """First model reply is garbage, second is valid JSON -> produce_log recovers."""
    ledger = SimulatedLedger()
    model = _BareModel()
    cfg = AgentConfig(agent_id=make_did(2))
    client = ScriptedModelClient("sorry, here is the answer: 37", VALID_PAYLOAD_STR)
    agent = Agent(model, cfg, client, ledger, max_retries=2)

    _, log_id, _ = agent.solve_and_commit(TASK, ledger, sim_tick=1)
    assert ledger.verify_log_integrity(log_id) is True


def test_retry_recovers_from_schema_invalid_then_valid():
    """A parseable-but-schema-invalid reply (forward dependency) then a valid one."""
    bad = copy.deepcopy(VALID_PAYLOAD)
    bad["reasoning_steps"][0]["depends_on"] = [2]  # forward ref -> schema-invalid
    ledger = SimulatedLedger()
    model = _BareModel()
    agent = Agent(model, AgentConfig(agent_id=make_did(3)),
                  ScriptedModelClient(json.dumps(bad), VALID_PAYLOAD_STR), ledger, max_retries=1)
    _, log_id, _ = agent.solve_and_commit(TASK, ledger, sim_tick=1)
    assert ledger.verify_log_integrity(log_id) is True


def test_persistent_malformed_raises_and_commits_nothing():
    ledger = SimulatedLedger()
    model = _BareModel()
    agent = Agent(model, AgentConfig(agent_id=make_did(4)),
                  ScriptedModelClient("not json at all"), ledger, max_retries=2)
    with pytest.raises(StructuredEmissionError):
        agent.solve_and_commit(TASK, ledger, sim_tick=1)
    assert ledger.get_history({"tx_type": "log_commit"}) == []


def test_json_extraction_tolerates_fences_and_prose():
    wrapped = "Here is my answer:\n```json\n" + VALID_PAYLOAD_STR + "\n```\nDone."
    obj = agents.extract_json_object(wrapped)
    assert obj["final_output"]["value"] == "37"


# ---------------------------------------------------------------------------
# 5. tamper-evidence from the agent layer (defense the framing relies on)
# ---------------------------------------------------------------------------

def test_tampering_a_committed_log_is_detected():
    ledger = SimulatedLedger()
    log = _emitted_anchorless_log()
    log_id = finalize_and_commit(log, ledger)
    assert ledger.verify_log_integrity(log_id) is True

    # mutate the stored log's content WITHOUT updating the anchor (simulated tamper)
    ledger.get_log(log_id)["reasoning_steps"][0]["content"] = "tampered"
    assert ledger.verify_log_integrity(log_id) is False
    assert ledger.verify() is False


def test_three_agents_commit_distinct_logs():
    """Same task, three honest agents -> three individually content-addressed logs
    (process-as-unit; no agreement on a single log hash)."""
    ledger = run_ledger = SimulatedLedger()
    model = _BareModel()
    log_ids = set()
    for i, payload in enumerate([agents._DEMO_PAYLOAD_A, agents._DEMO_PAYLOAD_B,
                                 agents._DEMO_PAYLOAD_C]):
        a = Agent(model, AgentConfig(agent_id=make_did(10 + i)),
                  ScriptedModelClient(payload), ledger)
        _, lid, _ = a.solve_and_commit(TASK, ledger, sim_tick=1)
        log_ids.add(lid)
    assert len(log_ids) == 3
    assert run_ledger.verify() is True


# ---------------------------------------------------------------------------
# 6. golden: the section-2.3 worked example validates
# ---------------------------------------------------------------------------

def test_section_2_3_example_validates():
    """The plan's section-2.3 example (illustrative '9f2c...' anchors) passes the SCHEMA
    validator. Its anchors are placeholders, so we check structure with require_anchors=False
    (the cryptographic anchor identity is the ledger's job, not the schema validator's)."""
    example = {
        "schema_version": "1.0",
        "log_id": "9f2c",
        "agent_id": "did:key:z6MkpT",
        "model_id": "llama3.1:8b-instruct",
        "task": {
            "task_id": "gsm8k-00042", "task_type": "discrete", "dataset": "GSM8K",
            "prompt": "A box has 12 red and 25 blue marbles. How many marbles total?",
        },
        "reasoning_steps": [
            {"step_index": 0, "step_type": "inference", "content": "Total = red + blue.",
             "depends_on": [], "produces": None, "tool_op_ref": None},
            {"step_index": 1, "step_type": "tool_call", "content": "Compute 12 + 25.",
             "depends_on": [0], "produces": "37", "tool_op_ref": "op-0"},
            {"step_index": 2, "step_type": "decision", "content": "Total marbles is 37.",
             "depends_on": [1], "produces": "37", "tool_op_ref": None},
        ],
        "tool_operations": [
            {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
             "output": "37", "deterministic": True, "evidence_hash": None},
        ],
        "final_output": {"value": "37", "derived_from_step": 2},
        "metadata": {"timestamp": "2026-06-03T10:15:30Z", "sim_tick": 42, "seed": 12345,
                     "temperature": 0.7, "runtime_ms": 812},
        "hash": "9f2c",
    }
    ok, errors = validate_log(example)
    assert ok, errors
