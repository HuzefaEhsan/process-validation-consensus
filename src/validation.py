#!/usr/bin/env python3
"""
validation.py -- Validation Predicate (Component C) for the Byzantine-resilient
peer/process-validation consensus prototype.  (playbook A.3; resolves prototype_plan.md
section 5, flag F1 for the `discrete` and `continuous` tiers.)

WHAT THIS IS
------------
The *validity gate* of the consensus: a mechanical, objective, and independently
RECOMPUTABLE judgement of a SINGLE committed section-2 action log.  It emits a section-3.2
`Verdict`.  There is **no LLM-as-judge anywhere** in this module -- the predicate re-executes
the log's deterministic tool operations and walks the recorded reasoning DAG; it never asks a
model to score anything.  Because any third party can re-run this predicate over the immutable,
content-addressed log and recompute the same `recompute_digest`, a FALSE validator verdict is
publicly refutable (Fact Sheet: "a false validator verdict is publicly refutable").

This module does NOT modify and only IMPORTS Components A (`sim_ledger.py`) and B (`agents.py`).
It reuses agents.`validate_log` (the audited structural section-2 check) FIRST; causal-sufficiency
runs ONLY on a structurally valid log.

THE THREE GATE RULE FAMILIES (all on one log; all booleans; ALL must pass to ACCEPT)
-----------------------------------------------------------------------------------
  (1) Causal-Sufficiency  -- THE CORE.  Does the recorded reasoning reproduce final_output?
        * discrete   : re-execute every `deterministic == true` tool op, walk the
                       produces/depends_on/tool_op_ref DAG, require the chain to reproduce
                       `final_output.value` by EXACT match (the step at `derived_from_step`
                       must actually yield it).
        * continuous : identical, accepted within a STATED numeric tolerance.
        * open_ended : INTERFACE + a clearly-marked F2-pending STUB (no fabricated scorer).
                       The mechanical residual/grounding DESIGN is documented in
                       `_causal_sufficiency_open_ended`; its reach is explicitly bounded by the
                       steganography-impossibility results (iMEC out of scope marks where any
                       log-inspection check provably stops).  Co-gated by F2 (its task set +
                       semantic/rubric scorer).  Calling `validate` on an open_ended log raises
                       `F2PendingError` rather than inventing a decision.
  (2) Process Coherence   -- DAG acyclic; every `depends_on` resolves to an EARLIER step; no
                             ORPHAN steps (no dead reasoning that nothing consumes).
  (3) Tool Utilization    -- every claimed tool result is actually USED downstream (referenced
                             by a step that feeds the answer).  A free-rider that fabricates an
                             answer with an unused / disconnected tool op fails here.

CROSS-AGENT COHERENCE is computed ONLY as a selection / tie-break SIGNAL, and ONLY when `peers`
is supplied.  It is **NEVER a gate**: honest stochastic divergence (a self-consistent log whose
answer differs from the majority) must never be failed on majority-mismatch -- that is the
consensus layer's (D) concern, not the predicate's (Fact Sheet condition iii; FPR < 5%).  It is
deliberately EXCLUDED from `recompute_digest` (it is peer-set-dependent, so including it would
break single-log public refutability).

KNOWN FAILURE MODE (stated, not hidden)
---------------------------------------
The predicate is itself a *checkable channel a colluder could target*: (a) it inspects causal
sufficiency, not the natural-language semantics of `content` strings, so the covertext-modification
tier can still carry payload that a single-log check cannot read out, and (b) a corrupt VALIDATOR
could emit a verdict that contradicts the honest recomputation.  Two substrate properties bound
this, and neither is implemented here -- they are Components A/D, cited for completeness:
  * commit-reveal of verdicts (ledger-enforced) stops a validator from adaptively flipping its
    verdict after seeing peers; and
  * public recomputability -- `recompute_digest` over the IMMUTABLE content-addressed log lets
    anyone re-derive the gate decision, so a false accept/reject is detectable and attributable.
The boundary is hard: the iMEC (information-theoretically-secure) collusion tier is provably
beyond ANY log-inspection method and is OUT OF SCOPE (Fact Sheet; cite the steganography
impossibility result verbatim from Literature_Matrix.md).

F1 STATUS (flagged honestly)
----------------------------
  RESOLVED for `discrete` (and `continuous`) now -- recompute is mechanical and tested below.
  `open_ended` is DESIGNED-BUT-DEFERRED, co-gated by F2 (task set + semantic/rubric scorer).
Nothing in this module fabricates a citation, a result, or a passing log.

Stack: Python 3.11 (runs on 3.12).  Canonicalisation for the digest is RFC 8785 (JCS) via the
same hard-pinned `rfc8785==0.1.4` the ledger uses -- the single source of hash identity, so the
digest is byte-reproducible across environments.
"""
from __future__ import annotations

import ast
import hashlib
import operator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# RFC 8785 (JCS): the SAME canonical-JSON source the ledger uses, so any third party recomputes
# a byte-identical `recompute_digest`. Hard dependency, no fallback (matches sim_ledger.py / F8).
import rfc8785

# ---- Reuse Components A and B (IMPORTED, never modified) --------------------------------------
from sim_ledger import SimulatedLedger                  # Component A (substrate) -- type + audit
from agents import validate_log, SchemaError            # Component B -- the audited section-2 check


PREDICATE_VERSION = "C-1.0"          # bumped if the recompute SEMANTICS change (binds the digest)
DEFAULT_CONTINUOUS_TOLERANCE = 1e-9  # STATED default for `continuous`; a tunable predicate parameter
F2_PENDING = "F2-pending"            # sentinel returned by the open_ended causal-sufficiency stub


# ==============================================================================================
# Exceptions
# ==============================================================================================
class F2PendingError(NotImplementedError):
    """Raised by `validate` for an `open_ended` log: causal-sufficiency for open-ended tasks is
    co-gated by F2 (its task set + semantic/rubric scorer) and is DESIGNED-BUT-DEFERRED.  We do
    NOT fabricate a decision.  The mechanical gates that ARE task-type-independent (Process
    Coherence, Tool Utilization) are still computed and attached as `partial_rule_results`."""

    def __init__(self, message: str, *, log_id: Optional[str] = None,
                 partial_rule_results: Optional[dict] = None) -> None:
        super().__init__(message)
        self.log_id = log_id
        self.partial_rule_results = partial_rule_results or {}


class _UnrecomputableOp(Exception):
    """Internal: a `deterministic == true` op names a tool with no registered re-executor, so it
    cannot be mechanically reproduced.  Treated as a re-execution FAILURE (the predicate cannot
    vouch for an op it cannot recompute), never silently passed."""


# ==============================================================================================
# Verdict (section 3.2)
# ==============================================================================================
@dataclass
class Verdict:
    """Section-3.2 Verdict.  The PREDICATE owns `decision`, `rule_results`, and `recompute_digest`
    (and copies `task_id`/`log_id` from the log).  `validator_id`/`sim_tick`/`timestamp`/`verdict_id`
    are CONTEXT the consensus layer (D) supplies when it packages this verdict for commit-reveal;
    they are optional here and -- crucially -- are NOT part of `recompute_digest`, so two honest
    validators on the same log produce the same digest (public refutability)."""
    log_id: str
    task_id: str
    decision: str                                   # "accept" | "reject"
    rule_results: dict                              # see _verdict below (matches 3.2 shape)
    recompute_digest: str                           # SHA-256 of the recomputation transcript
    validator_id: Optional[str] = None              # DID; D supplies
    verdict_id: Optional[str] = None                # D supplies / derived
    sim_tick: Optional[int] = None                  # D supplies (round tick)
    timestamp: Optional[str] = None                 # RFC 3339; metadata only (NOT in the digest)

    def to_dict(self) -> dict:
        return {
            "verdict_id": self.verdict_id,
            "task_id": self.task_id,
            "log_id": self.log_id,
            "validator_id": self.validator_id,
            "decision": self.decision,
            "rule_results": self.rule_results,
            "recompute_digest": self.recompute_digest,
            "sim_tick": self.sim_tick,
            "timestamp": self.timestamp,
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ==============================================================================================
# Value canonicalisation + comparison
#   discrete   -> EXACT match on canonical values (so "37", 37, 37.0 all denote the same answer)
#   continuous -> within a stated absolute tolerance
# ==============================================================================================
def _canon_value(x: Any) -> Any:
    """Canonical comparison form.  Numeric strings parse to numbers (int preferred), so the
    schema's allowed string/number representations of one answer compare equal; non-numeric
    strings compare as strings; objects/lists are returned as-is (compared structurally)."""
    if isinstance(x, bool):                          # bool is an int subclass; keep distinct
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
    ca, cb = _canon_value(a), _canon_value(b)
    a_num = isinstance(ca, (int, float)) and not isinstance(ca, bool)
    b_num = isinstance(cb, (int, float)) and not isinstance(cb, bool)
    if a_num and b_num:
        if task_type == "continuous":
            return abs(float(ca) - float(cb)) <= tol
        return ca == cb                              # discrete: exact (37 == 37.0 is True)
    return ca == cb                                  # non-numeric: exact canonical equality


def _json_safe(x: Any) -> Any:
    """Coerce a value into a JSON-serialisable form for the (JCS-canonicalised) transcript."""
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return str(x)


# ==============================================================================================
# Deterministic tool RE-EXECUTORS  (the "mechanical" core)
#   Registry keyed by tool_name. Only `deterministic == true` ops are re-executed (section 2.2:
#   "Determinism flag is binding").  A deterministic op naming an UNREGISTERED tool is treated as
#   non-reproducible -> _UnrecomputableOp -> the op fails re-execution (we never pass what we
#   cannot recompute).  Extending coverage is per-tool and mechanical.
# ==============================================================================================
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
           ast.FloorDiv: operator.floordiv}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _sum_fn(*a):
    # calculator notation: sum(12, 25) == 37 (sum of positional args); sum([..]) also works.
    if len(a) == 1 and isinstance(a[0], (list, tuple)):
        return sum(a[0])
    return sum(a)


_FUNCS = {"sum": _sum_fn, "min": min, "max": max, "abs": abs, "round": round}


def _eval_arith_node(node: ast.AST):
    """Evaluate a whitelisted arithmetic AST node.  No `eval`, no names, no attributes, no
    builtins beyond the small `_FUNCS` whitelist -- a closed, auditable recomputation."""
    if isinstance(node, ast.Expression):
        return _eval_arith_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"non-numeric constant: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval_arith_node(node.left), _eval_arith_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval_arith_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval_arith_node(a) for a in node.args])
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_arith_node(e) for e in node.elts]
    raise ValueError(f"unsupported arithmetic expression node: {type(node).__name__}")


def _safe_arith(expr: str):
    return _eval_arith_node(ast.parse(expr, mode="eval"))


def _recompute_calculator(inputs: dict):
    """Re-execute a `calculator` op from its recorded inputs.  Accepts the `{"expr": "<string>"}`
    form the agents emit (e.g. "12+25", "12 + 25", "sum(12, 25)")."""
    if isinstance(inputs, dict) and isinstance(inputs.get("expr"), str):
        return _safe_arith(inputs["expr"])
    raise _UnrecomputableOp(f"calculator: unsupported inputs shape {inputs!r}")


_DETERMINISTIC_TOOLS = {
    "calculator": _recompute_calculator,
    # NOTE (scope): the prototype's only deterministic tool is the arithmetic calculator (GSM8K).
    # Non-deterministic tools (e.g. a retriever) are NOT re-executed -- they are recorded evidence
    # (deterministic == false), checked only for downstream USE (Tool Utilization).
}


def _reexecute_op(op: dict):
    fn = _DETERMINISTIC_TOOLS.get(op.get("tool_name"))
    if fn is None:
        raise _UnrecomputableOp(f"no registered re-executor for deterministic tool "
                                f"{op.get('tool_name')!r}")
    return fn(op.get("inputs"))


# ==============================================================================================
# DAG helpers (operate on the already-structurally-valid `reasoning_steps`)
# ==============================================================================================
def _ancestors(steps: list[dict], idx: int) -> set[int]:
    """Transitive `depends_on` closure of step `idx`, INCLUDING `idx` itself."""
    seen: set[int] = set()
    stack = [idx]
    n = len(steps)
    while stack:
        i = stack.pop()
        if i in seen or not (0 <= i < n):
            continue
        seen.add(i)
        for d in steps[i].get("depends_on", []):
            if isinstance(d, int) and not isinstance(d, bool):
                stack.append(d)
    return seen


def _depended_upon(steps: list[dict]) -> set[int]:
    out: set[int] = set()
    for st in steps:
        for d in st.get("depends_on", []):
            if isinstance(d, int) and not isinstance(d, bool):
                out.add(d)
    return out


# ==============================================================================================
# Rule family (2): Process Coherence  -- gate
# ==============================================================================================
def _process_coherence(log: dict) -> tuple[bool, dict]:
    """DAG acyclic + every depends_on resolves to an EARLIER step + no orphan steps.
    (validate_log already guarantees contiguous indices and earlier-only depends_on; this is an
    INDEPENDENT re-check plus the orphan check validate_log does not perform.)"""
    steps = log["reasoning_steps"]
    n = len(steps)
    term = log["final_output"]["derived_from_step"]

    earlier_ok = True
    for i, st in enumerate(steps):
        for d in st.get("depends_on", []):
            if not (isinstance(d, int) and not isinstance(d, bool) and 0 <= d < i):
                earlier_ok = False                   # forward/self ref -> not a DAG (and not earlier)
    acyclic = earlier_ok                             # earlier-only edges => acyclic by construction

    depended = _depended_upon(steps)
    # An orphan = a non-terminal step that no other step depends on (dead reasoning that nothing
    # consumes). Honest linear/branching chains connect every step toward the answer -> none.
    orphans = sorted(i for i in range(n) if i != term and i not in depended)

    ok = acyclic and earlier_ok and (0 <= term < n) and not orphans
    transcript = {"acyclic": acyclic, "depends_on_all_earlier": earlier_ok,
                  "terminal_index_valid": 0 <= term < n, "orphan_steps": orphans}
    return ok, transcript


# ==============================================================================================
# Rule family (3): Tool Utilization  -- gate
# ==============================================================================================
def _tool_utilization(log: dict) -> tuple[bool, dict]:
    """Every claimed tool result must be USED downstream: referenced by a reasoning step that
    feeds the answer (is in the terminal step's dependency cone).  An op referenced by nobody, or
    only by orphan steps that do not reach the answer, is computed-but-discarded -> fail."""
    steps = log["reasoning_steps"]
    ops = log["tool_operations"]
    term = log["final_output"]["derived_from_step"]
    anc = _ancestors(steps, term)

    per_op = {}
    ok = True
    for op in ops:
        op_id = op["op_id"]
        refs = [i for i, st in enumerate(steps) if st.get("tool_op_ref") == op_id]
        used = any(i in anc for i in refs)           # referenced AND that ref feeds the answer
        per_op[op_id] = {"referenced_by": refs, "feeds_answer": used}
        if not used:
            ok = False
    transcript = {"per_op": per_op}                  # vacuously True when tool_operations == []
    return ok, transcript


# ==============================================================================================
# Rule family (1): Causal-Sufficiency  -- THE CORE gate
# ==============================================================================================
def _causal_sufficiency_numeric(log: dict, *, task_type: str, tol: float) -> tuple[bool, dict]:
    """`discrete` / `continuous` causal sufficiency.

    C1  every `deterministic == true` op RE-EXECUTES to its recorded output
        (exact for discrete, within `tol` for continuous).  Catches a FORGED tool output even
        when the final answer looks correct -- the case output-inspection is blind to.
    C2  every step that invokes a tool and claims a `produces` carries the op's actual value.
    C3  the terminal step (`final_output.derived_from_step`) yields `final_output.value`
        (its `produces` matches -- EXACT for discrete / within `tol` for continuous).
    C4  that final value is GROUNDED: reproduced by a re-executed deterministic op that the
        terminal step transitively depends on.  Catches a free-rider whose answer is a bare
        assertion not produced by any actual (re-executable) computation.

    Note (scope, stated): mechanical recomputability REQUIRES deterministic computations to be
    expressed as re-executable tool ops; a bare numeric claim in an `inference`/`decision` step
    is the model's assertion, not independently reproducible, so for the GSM8K-style discrete
    tier the answer must trace to re-executed deterministic ops (C4).  This is exactly what keeps
    the check mechanical instead of LLM-judgement."""
    steps = log["reasoning_steps"]
    ops = {op["op_id"]: op for op in log["tool_operations"]}
    fo = log["final_output"]
    term_idx = fo["derived_from_step"]
    final_val = fo["value"]

    # ---- C1: re-execute deterministic ops; record recomputed value per op --------------------
    reexec = []
    recomputed: dict[str, Any] = {}                  # op_id -> verified value (None if unverified)
    c1_ok = True
    for op_id, op in ops.items():
        if op.get("deterministic") is True:
            try:
                val = _reexecute_op(op)
                match = _values_equal(val, op["output"], task_type=task_type, tol=tol)
                reason = "ok" if match else "mismatch"
            except (_UnrecomputableOp, ValueError, ZeroDivisionError, TypeError) as e:
                val, match, reason = None, False, f"unrecomputable:{type(e).__name__}"
            recomputed[op_id] = val if match else None
            reexec.append({"op_id": op_id, "tool": op.get("tool_name"),
                           "recomputed": _json_safe(val), "recorded": _json_safe(op["output"]),
                           "match": match, "reason": reason})
            if not match:
                c1_ok = False
        else:
            recomputed[op_id] = op["output"]         # recorded evidence; not re-executed (binding flag)

    # ---- C2: tool-invoking steps must carry the op's actual produced value --------------------
    c2_ok = True
    c2_checks = []
    for i, st in enumerate(steps):
        ref = st.get("tool_op_ref")
        if ref is not None and st.get("produces") is not None:
            opval = recomputed.get(ref)
            ok = (opval is not None) and _values_equal(st["produces"], opval,
                                                        task_type=task_type, tol=tol)
            c2_checks.append({"step": i, "op": ref, "produces": _json_safe(st["produces"]),
                              "op_value": _json_safe(opval), "match": ok})
            if not ok:
                c2_ok = False

    # ---- C3: terminal step actually yields final_output.value --------------------------------
    term_prod = steps[term_idx].get("produces")
    c3_ok = (term_prod is not None) and _values_equal(term_prod, final_val,
                                                      task_type=task_type, tol=tol)

    # ---- C4: final value grounded in a re-executed deterministic op in the terminal's cone ----
    anc = _ancestors(steps, term_idx)
    grounded = False
    grounding_op = None
    for i in sorted(anc):
        ref = steps[i].get("tool_op_ref")
        if ref is not None and ops.get(ref, {}).get("deterministic") is True:
            val = recomputed.get(ref)
            if val is not None and _values_equal(val, final_val, task_type=task_type, tol=tol):
                grounded, grounding_op = True, ref
                break
    c4_ok = grounded

    ok = c1_ok and c2_ok and c3_ok and c4_ok
    transcript = {
        "task_type": task_type,
        "tolerance": tol if task_type == "continuous" else 0,
        "op_reexec": reexec,
        "step_tool_checks": c2_checks,
        "terminal": {"index": term_idx, "produces": _json_safe(term_prod),
                     "final_value": _json_safe(final_val), "reproduced": c3_ok},
        "grounding": {"grounded": c4_ok, "op_id": grounding_op},
        "subchecks": {"C1_deterministic_ops_reexecute": c1_ok,
                      "C2_tool_steps_consistent": c2_ok,
                      "C3_output_reproduced_by_terminal": c3_ok,
                      "C4_output_grounded_in_recomputed_op": c4_ok},
    }
    return ok, transcript


def _causal_sufficiency_open_ended(log: dict) -> str:
    """OPEN-ENDED causal sufficiency -- INTERFACE + clearly-marked STUB.  Returns the F2_PENDING
    sentinel; `validate` turns that into a raised `F2PendingError`.  We do NOT fabricate a scorer.

    DESIGN (the open half of F1, co-gated by F2 -- its task set + semantic/rubric scorer):
      For tasks with MULTIPLE valid answers there is no single re-executable computation to
      reproduce `final_output.value`, so causal sufficiency becomes a mechanical RESIDUAL /
      GROUNDING check, NOT an LLM judgement:
        (i)  GROUNDING -- every claim/step is grounded either internally (it `depends_on` an
             earlier step) or externally (it cites a recorded tool/evidence op via `tool_op_ref`);
             no ungrounded leaps.  [The graph half of this is already mechanical -- it is exactly
             Process Coherence's no-orphan/earlier-only check plus Tool Utilization -- and both
             DO run for open_ended logs.]
        (ii) ENTAILMENT -- `final_output` must be ENTAILED by its dependency cone: the terminal
             step's asserted result must be supported by the steps/evidence it derives from, with
             no claim introduced that no prior step or recorded op supports.  Deciding entailment
             over NATURAL-LANGUAGE content is what requires F2: a task-specific
             semantic-equivalence / rubric oracle for the open-ended set (NOT an LLM-as-judge at
             the validator, but a fixed, recorded scorer), without which "is this claim supported?"
             cannot be adjudicated mechanically.
      REACH BOUNDARY (hard, by impossibility): even a perfect grounding+entailment check is bounded
      by the steganography results -- the covertext-modification tier is statistically detectable
      under active checking, but the iMEC (information-theoretically-secure) tier embeds payload
      with NO structural/statistical signature in the log and is therefore provably beyond ANY
      log-inspection method.  iMEC is OUT OF SCOPE.  (Cite the impossibility result verbatim from
      Literature_Matrix.md; not reproduced here to avoid fabricating a citation.)

    STATUS: DESIGNED-BUT-DEFERRED.  Blocked on F2.  Returns F2_PENDING; never a fabricated bool."""
    return F2_PENDING


# ==============================================================================================
# Cross-Agent Coherence  -- SELECTION / TIE-BREAK SIGNAL ONLY (never a gate; excluded from digest)
# ==============================================================================================
def _cross_agent_coherence(log: dict, peers: list[dict], *, tol: float) -> float:
    """Fraction of peer logs (same task) whose `final_output.value` canonically matches this log's.
    A SIGNAL for the consensus layer's tie-break / clustering only.  It does NOT influence
    `decision`: a self-consistent honest log that diverges from the majority returns a LOW signal
    but still ACCEPTS on its gates (Fact Sheet condition iii).  Range [0.0, 1.0]; 0.0 if no peers."""
    task_type = log["task"]["task_type"]
    mine = log["final_output"]["value"]
    others = [p for p in peers if p is not log]
    if not others:
        return 0.0
    agree = sum(1 for p in others
                if _values_equal(mine, p.get("final_output", {}).get("value"),
                                 task_type=task_type, tol=tol))
    return agree / len(others)


# ==============================================================================================
# recompute_digest -- SHA-256 over the JCS-canonical recomputation TRANSCRIPT
#   Covers ONLY the single-log gate recomputation + the gate decision.  EXCLUDES validator_id,
#   timestamp, sim_tick, and the peer-dependent cross_agent_coherence signal -- so two honest
#   validators (and any third-party auditor) recompute a byte-identical digest over the immutable
#   log.  THIS is the object that makes a false verdict publicly refutable.
# ==============================================================================================
def _recompute_digest(transcript: dict) -> str:
    return hashlib.sha256(rfc8785.dumps(transcript)).hexdigest()


# ==============================================================================================
# Public predicate
# ==============================================================================================
def validate(log: dict, peers: Optional[list[dict]] = None, *,
             validator_id: Optional[str] = None, sim_tick: Optional[int] = None,
             timestamp: Optional[str] = None,
             tolerance: Optional[float] = None) -> Verdict:
    """Mechanically judge ONE committed section-2 action log and emit a section-3.2 `Verdict`.

    Order (per A.3):
      0. STRUCTURAL precondition -- reuse agents.`validate_log` FIRST.  A structurally invalid log
         is a precondition violation (the ledger's commit path would not have admitted it), so we
         raise `SchemaError` rather than mint a misleading reject Verdict.  Causal-sufficiency runs
         ONLY on a structurally valid log.
      1. GATES -- Process Coherence, Tool Utilization, Causal-Sufficiency (by task_type).
      2. SIGNAL -- Cross-Agent Coherence, only if `peers` is given; never a gate.
      3. DIGEST -- SHA-256 over the JCS-canonical recomputation transcript (gates only).

    `peers`: list of peer LOG dicts for the SAME task (their final outputs feed the tie-break
    signal only).  `tolerance`: absolute tolerance for `continuous` (defaults to
    DEFAULT_CONTINUOUS_TOLERANCE); ignored for `discrete` (exact match).

    Raises `F2PendingError` for `open_ended` logs (no fabricated scorer); `SchemaError` if the log
    fails the section-2 structural check."""
    # ---- 0. structural precondition (REUSE validate_log) -------------------------------------
    ok, errors = validate_log(log, require_anchors=False)
    if not ok:
        raise SchemaError("validation.validate: log is not structurally valid (section-2 check "
                          "must pass before causal-sufficiency):\n  - " + "\n  - ".join(errors))

    task_type = log["task"]["task_type"]
    log_id = log.get("log_id") or _recompute_unanchored_id(log)
    task_id = log["task"]["task_id"]
    tol = DEFAULT_CONTINUOUS_TOLERANCE if (task_type == "continuous" and tolerance is None) \
        else (tolerance or 0.0)

    # ---- 1. supporting gates (task-type-independent, fully mechanical) -----------------------
    pc_ok, pc_t = _process_coherence(log)
    tu_ok, tu_t = _tool_utilization(log)

    # ---- 1. core gate: causal sufficiency (by task_type) -------------------------------------
    if task_type in ("discrete", "continuous"):
        cs_ok, cs_t = _causal_sufficiency_numeric(log, task_type=task_type, tol=tol)
    elif task_type == "open_ended":
        # INTERFACE present; STUB returns the sentinel; we refuse to fabricate a decision.
        sentinel = _causal_sufficiency_open_ended(log)
        partial = {"process_coherence": pc_ok, "tool_utilization": tu_ok,
                   "causal_sufficiency": sentinel}
        raise F2PendingError(
            "open_ended causal-sufficiency is co-gated by F2 (task set + semantic/rubric scorer) "
            "and is designed-but-deferred; refusing to fabricate a verdict. Process Coherence and "
            "Tool Utilization were computed and are attached as partial_rule_results.",
            log_id=log_id, partial_rule_results=partial)
    else:  # validate_log already restricts task_type to the enum; defensive only.
        raise SchemaError(f"unknown task_type {task_type!r}")

    # ---- 2. selection signal (NEVER a gate) --------------------------------------------------
    cac = _cross_agent_coherence(log, peers, tol=tol) if peers else None

    # ---- decision: ALL THREE GATES must pass; the signal is excluded -------------------------
    decision = "accept" if (pc_ok and tu_ok and cs_ok) else "reject"

    # ---- 3. recompute_digest over the gate recomputation only (peer signal EXCLUDED) ----------
    transcript = {
        "predicate_version": PREDICATE_VERSION,
        "log_id": log_id,
        "task_id": task_id,
        "task_type": task_type,
        "gates": {
            "process_coherence": {"result": pc_ok, "detail": pc_t},
            "tool_utilization": {"result": tu_ok, "detail": tu_t},
            "causal_sufficiency": {"result": cs_ok, "detail": cs_t},
        },
        "decision": decision,
    }
    digest = _recompute_digest(transcript)

    rule_results = {
        "process_coherence": pc_ok,                  # gate
        "tool_utilization": tu_ok,                   # gate
        "causal_sufficiency": cs_ok,                 # gate (the linchpin)
        "cross_agent_coherence": cac,                # SELECTION / tie-break signal ONLY (or None)
    }
    return Verdict(
        log_id=log_id, task_id=task_id, decision=decision, rule_results=rule_results,
        recompute_digest=digest, validator_id=validator_id, sim_tick=sim_tick,
        timestamp=timestamp or _now(),
        verdict_id=_derive_verdict_id(log_id, digest, validator_id),
    )


def _recompute_unanchored_id(log: dict) -> str:
    """Best-effort id for an (uncommitted, anchorless) log: the same content hash the ledger would
    assign.  For committed logs `log['log_id']` is already present and is used instead."""
    body = {k: v for k, v in log.items() if k not in ("hash", "log_id")}
    return hashlib.sha256(rfc8785.dumps(body)).hexdigest()


def _derive_verdict_id(log_id: str, digest: str, validator_id: Optional[str]) -> str:
    """A stable, content-derived verdict id.  Distinct per (validator, log, recomputation) when a
    validator_id is supplied; D may override when packaging for commit-reveal."""
    seed = f"{validator_id or ''}|{log_id}|{digest}".encode("utf-8")
    return "verdict-" + hashlib.sha256(seed).hexdigest()[:16]


def validate_committed(ledger: SimulatedLedger, log_id: str,
                       peers: Optional[list[dict]] = None, **kw) -> Verdict:
    """Convenience: fetch a log from the ledger, verify its content-address (integrity), then
    `validate` it.  This ties the predicate to the IMMUTABLE substrate -- the recomputation runs
    over exactly the bytes the ledger verifies, which is what public refutability presumes.
    Raises ValueError if the log is missing or fails `verify_log_integrity`."""
    if not ledger.log_exists(log_id):
        raise ValueError(f"validate_committed: no log {log_id!r} on the ledger")
    if not ledger.verify_log_integrity(log_id):
        raise ValueError(f"validate_committed: log {log_id!r} fails integrity (content-address "
                         f"mismatch) -- refusing to validate a tampered log")
    return validate(ledger.get_log(log_id), peers, **kw)
