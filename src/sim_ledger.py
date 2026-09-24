#!/usr/bin/env python3
"""
sim_ledger.py -- SimulatedLedger (Component A) for the Byzantine-resilient
peer/process-validation consensus prototype.

Substrate-only, per prototype_plan.md (sections 0-3):
  * append-only, content-addressed (SHA-256 over RFC 8785 / JCS), hash-chained;
  * stores action logs, peer attestations (verdicts), verdict-set seals, and
    reputation state + history;
  * Web3.py-compatible surface: content-addressed put/get (commit_log/get_log)
    plus event-log/tx semantics (append_tx/get_history/head) -- swappable for a
    Solidity contract + event log later;
  * verdict commit-reveal with an ENFORCED commit->reveal phase boundary
    (no reveals until commits close) for verdict independence;
  * records (audit-only) the harness-seeded peer validator set per log;
  * public refutability and a whole-ledger verify() that detects ANY tampering
    and structurally checks the reputation -> seal -> verdicts -> logs chain.

It performs NO node-consensus and NO protocol logic, and exposes NO value to
agents as a coordination input: head(), the tx history, and the recorded peer-set
seed are AUDIT-ONLY (section 0.2 / flag F3). The supermajority OUTCOME, the seal's
membership, roster admissibility, and the initial reputation value (0.5) are all
computed in the consensus layer (D); the ledger only persists what D decides.

Enforcement model is "refute, don't prevent": writes are not blocked on semantic
grounds; correctness is RECOMPUTED from the immutable chain by an external auditor.
verify() does the CHEAP structural half (hashes line up, anchors resolve); deep
correctness (do the sealed verdicts justify the outcome and the reputation delta?)
is the auditor's job, re-derived offline from the logs the seal points to.

Dependency (hard, pinned): rfc8785==0.1.4. A fallback canonicaliser is intentionally
NOT provided -- cross-environment hash identity underpins public refutability, so a
silent substitute could fork the hash space. Missing dependency -> import error.

Concurrency constraint (recorded): the COMMIT/REVEAL phase is GLOBAL and assumes
BATCH-LOCKSTEP rounds -- one global commit phase then one global reveal phase per
batch, ordered by sim_tick. D MUST NOT overlap rounds. Generalising to round_id-keyed
phases is a cheap isolated refactor, to be done only if concurrent/per-task rounds
are later required.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Optional

# --- Canonicalisation: RFC 8785 (JCS) is a HARD dependency (flag F8). No silent fallback.
try:
    import rfc8785  # pip install rfc8785==0.1.4  (Trail of Bits; RFC 8785-compliant)
except ImportError as _e:  # raise loudly rather than substitute a non-identical serialiser
    raise ImportError(
        "sim_ledger requires 'rfc8785' (RFC 8785 / JCS canonical JSON). Cross-environment "
        "hash identity underpins public refutability, so no fallback serialiser is provided. "
        "Install with:  pip install rfc8785==0.1.4"
    ) from _e

def _jcs(obj) -> bytes:
    """RFC 8785 canonical JSON bytes -- the single source of hash identity."""
    return rfc8785.dumps(obj)

_GENESIS = "0" * 64                       # prev_hash anchor for the first tx (empty chain)
_INIT_MARKER = "genesis-init"             # reserved reason_ref for an initial reputation write

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _now() -> str:                        # RFC 3339 / ISO 8601 UTC
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PhaseError(RuntimeError):
    """Raised when commit_verdict/reveal_verdict is called in the wrong lifecycle phase.
    A phase violation is a caller/orchestration bug (D used the API out of order) and is kept
    DISTINCT from a cryptographic reveal outcome (match -> True; mismatch/missing -> False +
    recorded event), so a lifecycle bug can never be mistaken for adversarial behaviour and
    inflate detection metrics. Subclasses RuntimeError for backward compatibility."""


class SimulatedLedger:
    """Tamper-evident substrate. Stores (logs / verdicts / peer-sets / seals / reputation),
    all anchored to one append-only, hash-chained transaction log (the audit chain).
    Verdict commit-reveal runs a two-phase lifecycle: COMMIT (commits accepted, no reveals)
    -> REVEAL (reveals accepted, commits closed). The phase is GLOBAL and assumes
    batch-lockstep rounds (one commit then one reveal per batch); D must not overlap rounds."""

    def __init__(self) -> None:
        self._logs: dict[str, dict] = {}        # log_id -> action log
        self._commits: dict[str, dict] = {}     # commit_id -> commitment record
        self._by_vlog: dict[tuple, list] = {}   # (validator_id, log_id) -> [commit_id]
        self._revealed: dict[str, dict] = {}    # commit_id -> {"verdict","salt"}
        self._peer_sets: dict[tuple, dict] = {} # (task_id, log_id) -> assignment record
        self._seals: dict[str, dict] = {}       # seal_id -> sealed-verdict-set record
        self._rep: dict[str, float] = {}        # agent_id -> current reputation (no default)
        self._rep_hist: dict[str, list] = {}    # agent_id -> [history dicts]
        self._chain: list[dict] = []            # append-only hash-chained tx log
        self._phase: str = "commit"             # verdict lifecycle phase (5a)

    # ===== Hashing / content address (section 2.2) =====
    def recompute_hash(self, log: dict) -> str:
        """SHA-256 hex over JCS(log) with the derived id fields removed. The spec says
        'remove hash'; we also remove 'log_id' because it equals hash -- leaving either
        in would make the content address self-referential/uncomputable."""
        body = {k: v for k, v in log.items() if k not in ("hash", "log_id")}
        return _sha256_hex(_jcs(body))

    @staticmethod
    def _commitment(verdict: dict, salt: str) -> str:
        """Section 3.2 binding: commitment = SHA-256( JCS(Verdict) || salt )."""
        return _sha256_hex(_jcs(verdict) + salt.encode("utf-8"))

    # ===== Append-only hash-chained tx log (Web3 event-log analogue; audit only) =====
    def append_tx(self, tx_type: str, payload: dict, sim_tick: int) -> str:
        rec = {"index": len(self._chain), "tx_type": tx_type, "payload": payload,
               "sim_tick": sim_tick, "timestamp": _now(),
               "prev_hash": self._chain[-1]["tx_hash"] if self._chain else _GENESIS}
        rec["tx_hash"] = _sha256_hex(_jcs(rec))   # binds prev_hash -> tamper-evident chain
        self._chain.append(rec)
        return rec["tx_hash"]

    def get_history(self, filter: Optional[dict] = None) -> list:
        if not filter:
            return list(self._chain)
        return [tx for tx in self._chain if all(tx.get(k) == v for k, v in filter.items())]

    def head(self) -> dict:
        """Latest tx head -- AUDIT/VERIFICATION ONLY. Never an agent input (0.2)."""
        return self._chain[-1] if self._chain else {
            "index": -1, "tx_hash": _GENESIS, "tx_type": "genesis"}

    # ===== Action Log Store (content-addressed, append-only) =====
    def commit_log(self, log: dict) -> str:
        h = self.recompute_hash(log)
        if log.get("hash") != h or log.get("log_id") != h:   # both anchors must equal the
            raise ValueError("commit_log: log['hash']/['log_id'] != content hash (rejected)")
        #   content hash (section 2.1: "log_id MUST equal hash"); this is exactly what
        #   verify()/verify_log_integrity re-check, so the store never admits what they reject.
        if h in self._logs:                        # append-only / immutability enforcement
            if _jcs(self._logs[h]) != _jcs(log):
                raise ValueError("commit_log: different log under existing id (rejected)")
            return h                               # idempotent re-commit of identical bytes
        self._logs[h] = log                        # store keyed by its own content hash
        self.append_tx("log_commit", {"log_id": h, "task_id": log["task"]["task_id"],
                                      "agent_id": log["agent_id"]},
                       log["metadata"]["sim_tick"])
        return h

    def get_log(self, log_id: str) -> Optional[dict]:
        return self._logs.get(log_id)

    def log_exists(self, log_id: str) -> bool:
        return log_id in self._logs

    # ===== Verdict commit-reveal phase control (5a: verdict independence) =====
    @property
    def phase(self) -> str:
        return self._phase

    def begin_commit_phase(self, sim_tick: int = 0) -> None:
        """Open a (new) batch's commit round: commits accepted, reveals rejected."""
        self._phase = "commit"
        self.append_tx("phase", {"phase": "commit"}, sim_tick)

    def begin_reveal_phase(self, sim_tick: int = 0) -> None:
        """Close commits and open reveals. Guarantees every commitment is locked
        (published as a hiding hash) BEFORE any reveal -> verdicts are independent."""
        self._phase = "reveal"
        self.append_tx("phase", {"phase": "reveal"}, sim_tick)

    # ===== Verdict commit-reveal (peer attestations) =====
    def commit_verdict(self, task_id: str, log_id: str, validator_id: str,
                       commitment: str, sim_tick: int) -> str:
        """COMMIT phase only -> raises PhaseError otherwise (a lifecycle/caller bug)."""
        if self._phase != "commit":
            raise PhaseError("commit_verdict: commits are closed (not in COMMIT phase)")
        rec = {"task_id": task_id, "log_id": log_id, "validator_id": validator_id,
               "commitment": commitment, "sim_tick": sim_tick}
        commit_id = self.append_tx("verdict_commit", rec, sim_tick)   # tx_hash == commit_id
        self._commits[commit_id] = rec
        self._by_vlog.setdefault((validator_id, log_id), []).append(commit_id)
        return commit_id                           # differing 2nd commit kept = equivocation

    def reveal_verdict(self, commit_id: str, verdict: dict, salt: str) -> bool:
        """REVEAL phase only -> raises PhaseError otherwise (a lifecycle/caller bug, distinct
        from a crypto result). Match -> persist + return True; mismatch / missing commit ->
        record a reveal_fail event and return False (expected runtime/adversary data, F5)."""
        if self._phase != "reveal":                # no reveals until commits close (5a)
            raise PhaseError("reveal_verdict: reveals not open (not in REVEAL phase)")
        rec = self._commits.get(commit_id)
        tick = rec["sim_tick"] if rec else len(self._chain)
        if rec is None or self._commitment(verdict, salt) != rec["commitment"]:
            self.append_tx("reveal_fail",                # non-reveal / mismatch event (F5)
                           {"commit_id": commit_id,
                            "reason": "no_commit" if rec is None else "mismatch"}, tick)
            return False
        self._revealed[commit_id] = {"verdict": verdict, "salt": salt}
        self.append_tx("verdict_reveal", {"commit_id": commit_id, "verdict": verdict}, tick)
        return True

    def get_verdicts(self, log_id: str, phase: str = "revealed") -> list:
        if phase == "committed":
            return [r for r in self._commits.values() if r["log_id"] == log_id]
        if phase == "revealed":
            return [self._revealed[c]["verdict"] for c in self._revealed
                    if self._commits[c]["log_id"] == log_id]
        raise ValueError("phase must be 'committed' or 'revealed'")

    def is_equivocating(self, validator_id: str, log_id: str) -> bool:
        """True if a validator committed >1 distinct commitment for the same log."""
        cids = self._by_vlog.get((validator_id, log_id), [])
        return len({self._commits[c]["commitment"] for c in cids}) > 1

    # ===== Peer-set assignment record (5b: recorded on-ledger; admissibility enforced in D) =====
    def assign_peer_set(self, task_id: str, log_id: str, validators: list,
                        seed: int, sim_tick: int) -> str:
        """Record the harness-seeded peer validator set for a log. The seed is stored
        for reproducibility only (audit, not an agent randomness input -- 0.2 / F3).
        Whether only these validators' verdicts COUNT is enforced in the consensus layer."""
        rec = {"task_id": task_id, "log_id": log_id,
               "validators": list(validators), "seed": seed}
        assignment_id = self.append_tx("peer_set", rec, sim_tick)
        self._peer_sets[(task_id, log_id)] = {**rec, "assignment_id": assignment_id}
        return assignment_id

    def get_peer_set(self, task_id: str, log_id: str) -> Optional[dict]:
        return self._peer_sets.get((task_id, log_id))

    # ===== Verdict-set seal (5c: recorded on-ledger; correctness verified by AUDIT) =====
    def seal_verdict_set(self, task_id: str, log_id: str, verdict_commit_ids: list,
                         outcome: str, sim_tick: int) -> str:
        """Record (audit-only) a sealed verdict set: the verdict commit_ids D included and
        the supermajority OUTCOME D computed. The ledger does NO protocol logic -- it only
        persists the seal (hash-chained) and returns seal_id (= the tx hash). This is the
        anchor a reputation update cites, completing the refutability chain
        reputation -> seal -> verdicts -> logs; an auditor re-derives the outcome off-ledger."""
        rec = {"task_id": task_id, "log_id": log_id,
               "verdict_commit_ids": list(verdict_commit_ids), "outcome": outcome}
        seal_id = self.append_tx("verdict_seal", rec, sim_tick)
        self._seals[seal_id] = {**rec, "seal_id": seal_id, "sim_tick": sim_tick}
        return seal_id

    def get_seal(self, seal_id: str) -> Optional[dict]:
        return self._seals.get(seal_id)

    # ===== Reputation State Store (+ append-only history) =====
    def get_reputation(self, agent_id: str) -> Optional[float]:
        """Stored reputation, or None if the agent was never initialised. The default
        starting value (0.5) is the consensus layer's policy, not the ledger's (F6)."""
        return self._rep.get(agent_id)

    def _record_reputation(self, agent_id: str, value: float, reason_ref: str,
                           sim_tick: int) -> None:
        prev = self._rep.get(agent_id)             # None on the first (init) write
        tx = self.append_tx("reputation_set",
                            {"agent_id": agent_id, "value": value, "prev": prev,
                             "reason_ref": reason_ref}, sim_tick)
        self._rep_hist.setdefault(agent_id, []).append(
            {"value": value, "prev": prev, "reason_ref": reason_ref, "tx_hash": tx})
        self._rep[agent_id] = value

    def init_reputation(self, agent_id: str, value: float, sim_tick: int = 0) -> None:
        """Genesis path: record an agent's initial reputation with prev=None and the reserved
        reason_ref 'genesis-init' (NO empty genesis verdict-set is minted). The value (0.5) is
        D's policy. Re-initialising an already-known agent is rejected."""
        if agent_id in self._rep:
            raise ValueError("init_reputation: agent already initialised; use set_reputation")
        self._record_reputation(agent_id, value, _INIT_MARKER, sim_tick)

    def set_reputation(self, agent_id: str, value: float, reason_ref: str,
                       sim_tick: Optional[int] = None) -> None:
        """Update reputation anchored to reason_ref = a sealed-verdict-set id (minted by
        seal_verdict_set; the supermajority outcome is D's). Per the refute-don't-prevent
        model, unsealed reason_refs are NOT blocked here -- verify() flags them at audit time.
        Prior values are retained in history. sim_tick is optional and backward-compatible:
        pass the round's Mesa tick; if omitted it inherits the anchoring seal's tick, falling
        back to the chain position only as a last resort (e.g. a non-seal reason_ref)."""
        if sim_tick is None:
            seal = self._seals.get(reason_ref)
            sim_tick = seal["sim_tick"] if seal else len(self._chain)
        self._record_reputation(agent_id, value, reason_ref, sim_tick)

    def get_reputation_history(self, agent_id: str) -> list:
        return list(self._rep_hist.get(agent_id, []))

    # ===== Verification / public refutability =====
    def verify_log_integrity(self, log_id: str) -> bool:
        log = self._logs.get(log_id)
        return bool(log) and self.recompute_hash(log) == log_id == log.get("hash") \
            == log.get("log_id")

    def verify_verdict(self, commit_id: str) -> bool:
        rv = self._revealed.get(commit_id)
        return bool(rv) and self._commitment(rv["verdict"], rv["salt"]) == \
            self._commits[commit_id]["commitment"]

    def verify(self) -> bool:
        """Whole-ledger structural integrity. Returns False on ANY mutation. Cheap by design;
        deep semantic correctness (sealed verdicts justify the outcome and the reputation
        delta) is recomputed by an external auditor from the immutable chain."""
        for lid, log in self._logs.items():                       # 1. content addressing
            if self.recompute_hash(log) != lid:
                return False
            if log.get("hash") != lid or log.get("log_id") != lid:  # hardening: anchors == key
                return False
        prev = _GENESIS                                           # 2. hash-chain linkage
        for tx in self._chain:
            body = {k: v for k, v in tx.items() if k != "tx_hash"}
            if tx["prev_hash"] != prev or _sha256_hex(_jcs(body)) != tx["tx_hash"]:
                return False
            prev = tx["tx_hash"]
        for cid, rv in self._revealed.items():                    # 3. verdict bindings
            if self._commitment(rv["verdict"], rv["salt"]) != self._commits[cid]["commitment"]:
                return False
        for tx in self._chain:                                    # 4. reputation anchor resolves
            if tx["tx_type"] == "reputation_set":
                rr = tx["payload"]["reason_ref"]
                if rr != _INIT_MARKER and rr not in self._seals:  # -> a recorded seal or genesis
                    return False
        return True
