#!/usr/bin/env python3
"""
demo_ledger.py -- narrative end-to-end walkthrough of SimulatedLedger (Component A).

A documentation aid for the thesis chapter: it runs the *current* API in the order a
single task flows through the substrate. The authoritative correctness checks live in
test_sim_ledger.py (pytest); this script just tells the story in one readable run.

Run:  python3 demo_ledger.py
"""
import secrets

from sim_ledger import SimulatedLedger, PhaseError, _jcs, _sha256_hex, _now

AGENT = "did:key:z6MkpTHonest1"
VALIDATOR = "did:key:z6MkValidator1"


def build_log(answer="37", tick=42, agent=AGENT):
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
            {"step_index": 2, "step_type": "decision", "content": f"Total marbles is {answer}.",
             "depends_on": [1], "produces": answer, "tool_op_ref": None}],
        "tool_operations": [
            {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
             "output": answer, "deterministic": True, "evidence_hash": None}],
        "final_output": {"value": answer, "derived_from_step": 2},
        "metadata": {"timestamp": _now(), "sim_tick": tick, "seed": 12345,
                     "temperature": 0.7, "runtime_ms": 812},
    }
    body = {k: v for k, v in log.items() if k not in ("hash", "log_id")}
    log["log_id"] = log["hash"] = _sha256_hex(_jcs(body))
    return log


def build_verdict(log_id, task_id, validator=VALIDATOR, decision="accept"):
    return {
        "verdict_id": "v-0001", "task_id": task_id, "log_id": log_id,
        "validator_id": validator, "decision": decision,
        "rule_results": {"process_coherence": True, "tool_utilization": True,
                         "causal_sufficiency": True, "cross_agent_coherence": 0.91},
        "recompute_digest": _sha256_hex(("rc::" + log_id).encode()),
        "sim_tick": 42, "timestamp": _now()}


def sep(title):
    print("\n" + "=" * 70 + "\n" + title + "\n" + "=" * 70)


def short(did):
    return did.split(":")[-1]


if __name__ == "__main__":
    led = SimulatedLedger()

    # 1) Commit the action log (content-addressed, immutable, hash-chained).
    sep("1) COMMIT ACTION LOG  (content-addressed, append-only)")
    log = build_log()
    log_id = led.commit_log(log)
    task_id = log["task"]["task_id"]
    print("committed log_id      :", log_id)
    print("verify_log_integrity  :", led.verify_log_integrity(log_id))

    # 2) Record the harness-seeded peer validator set (5b: recorded; D enforces who counts).
    sep("2) ASSIGN PEER SET  (recorded on-ledger; admissibility enforced in D)")
    roster = [VALIDATOR, "did:key:z6MkValidator2", "did:key:z6MkValidator3"]
    led.assign_peer_set(task_id, log_id, roster, seed=20260603, sim_tick=42)
    ps = led.get_peer_set(task_id, log_id)
    print(f"peer set (k={len(ps['validators'])})        :", ", ".join(short(v) for v in ps["validators"]))
    print("harness seed (audit)  :", ps["seed"], "(reproducibility only — not an agent input)")

    # 3) Commit-reveal with the ENFORCED commit -> reveal phase boundary (5a).
    sep("3) COMMIT-REVEAL  (enforced commit -> reveal phase boundary)")
    verdict = build_verdict(log_id, task_id)
    salt = secrets.token_hex(16)
    commitment = SimulatedLedger._commitment(verdict, salt)   # validator publishes this
    print("phase                 :", led.phase)
    commit_id = led.commit_verdict(task_id, log_id, VALIDATOR, commitment, 42)
    print("committed (hidden)    :", len(led.get_verdicts(log_id, "committed")),
          "commitment — verdict still secret")
    try:                                                      # the boundary in action:
        led.reveal_verdict(commit_id, verdict, salt)
        print("reveal during COMMIT  : (unexpectedly allowed!)")
    except PhaseError:
        print("reveal during COMMIT  : blocked (PhaseError) — commits must close first")
    led.begin_reveal_phase(43)
    print("phase                 :", led.phase, "(commits now closed)")
    print("reveal matched        :", led.reveal_verdict(commit_id, verdict, salt))
    print("verify_verdict        :", led.verify_verdict(commit_id))
    rv = led.get_verdicts(log_id, "revealed")[0]
    print("revealed verdict      :", rv["decision"], "| coherence",
          rv["rule_results"]["cross_agent_coherence"])

    # 4) Seal the verdict set (5c: D computes the outcome; the ledger only records it).
    sep("4) SEAL VERDICT SET  (audit anchor; supermajority outcome computed in D)")
    seal_id = led.seal_verdict_set(task_id, log_id, [commit_id], outcome="accept", sim_tick=44)
    print("seal_id               :", seal_id)
    print("sealed outcome        :", led.get_seal(seal_id)["outcome"],
          "over", len(led.get_seal(seal_id)["verdict_commit_ids"]), "revealed verdict(s)")

    # 5) Reputation: genesis init (prev=None) then a seal-anchored update.
    sep("5) REPUTATION  (genesis init + seal-anchored update)")
    print("reputation (unknown)  :", led.get_reputation(AGENT), "(default 0.5 is D's policy)")
    led.init_reputation(AGENT, 0.5, sim_tick=42)
    print("after init_reputation :", led.get_reputation(AGENT), "(reason_ref = genesis-init)")
    led.set_reputation(AGENT, 0.55, reason_ref=seal_id, sim_tick=44)
    print("after seal-anchored ↑ :", led.get_reputation(AGENT), "(reason_ref = seal_id)")
    print("history reason_refs   :", [h["reason_ref"][:14] for h in
                                      led.get_reputation_history(AGENT)])

    # 6) The append-only, hash-linked audit chain.
    sep("6) TRANSACTION CHAIN  (append-only, hash-linked)")
    for tx in led.get_history():
        print(f"  #{tx['index']:<2} {tx['tx_type']:<15} tick={tx['sim_tick']:<3} "
              f"prev={tx['prev_hash'][:8]} hash={tx['tx_hash'][:8]}")
    print("head                  :", led.head()["tx_type"], "@", led.head()["tx_hash"][:8])

    # 7) Whole-ledger verify + tamper detection (incl. the in-object anchor hardening).
    sep("7) WHOLE-LEDGER VERIFY + TAMPER DETECTION")
    print("verify()              :", led.verify(), "(clean)")
    led._logs[log_id]["final_output"]["value"] = "41"         # content tamper 37 -> 41
    print("verify()  [content]   :", led.verify(), "— mutated final_output 37 -> 41")
    led._logs[log_id]["final_output"]["value"] = "37"         # restore the exact original
    print("verify()  [restored]  :", led.verify(), "— original content restored")
    led._logs[log_id]["hash"] = "0" * 64                      # tamper ONLY the in-object anchor
    print("verify()  [anchor]    :", led.verify(),
          "— mutated in-object hash anchor (caught by hardening)")
