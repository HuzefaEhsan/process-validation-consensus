#!/usr/bin/env python3
"""gsm8k_logs.py -- convert real GSM8K test problems into schema-valid section-2 action logs
WITHOUT an LLM, by mechanically translating each reference solution's calculator annotations
(<<expr=value>>) into calculator tool operations.

This module is NEW. It imports the audited modules (validation, agents) and modifies none of them.
It plugs into the existing harness exclusively through the payload-generator seam: every builder
below has the seam signature  generator(effort: str, task: dict) -> raw reasoning-payload JSON str.

WHAT THE SCRIPTED AGENT "KNOWS"
  The honest scripted agent's reasoning source is the GSM8K reference solution (carried in the
  harness-private task key "reference"; `agents.assemble_log` copies only task_id / task_type /
  dataset / prompt into a log, so the reference never enters a committed log). The ground-truth
  '#### answer' line is NOT given to generators: it goes only to the harness label store. The
  generators read the answer off the last annotation's recorded value, which eligibility rule (b)
  guarantees equals the '####' answer.

ELIGIBILITY (a problem is used only if ALL hold; the first failing rule is its primary drop reason)
  (a) every annotation re-executes under validation.py's whitelisted executor to exactly its
      recorded value (discrete, exact canonical comparison, tolerance 0);
  (b) the '#### answer' equals the value of the LAST annotation (the terminal grounding op);
  (c) ADDED BEYOND THE TWO REQUESTED RULES -- complete recorded derivation: under the dataflow
      wiring below, every annotation step lies in the dependency cone of the last annotation.
      A reference solution that computes part of its derivation in prose (comparisons, inverse
      reasoning, identity annotations such as <<15=15>>) leaves a calculator result that never
      feeds the answer; its faithful log fails Process Coherence (orphan) and Tool Utilization.
      These problems are reported as a finding (see experiments.py sensitivity block), not hidden.
  (d) safety net: every one of the five honest tiers built for the problem is ACCEPTED by
      validation.validate. (Expected to never trigger once (a)-(c) hold; counted if it does.)

DATAFLOW WIRING (step 3 of the P2 spec)
  One tool_call step + one calculator op per annotation. Step i depends on EVERY earlier step j
  whose recorded value equals (canonically) a numeric literal in expression i. Linking to all
  matching earlier steps, not only the most recent one, resolves value collisions (two different
  quantities that are both 9) conservatively: it never drops a true literal-reuse edge, and a
  spurious edge is harmless (earlier-only edges keep the DAG valid). This is a literal-value
  heuristic; transformed reuse (e.g. 50% written as .5) is not detected and shows up under (c).

HONEST EFFORT TIERS (all variations of one valid derivation; all must pass the predicate)
  minimal  : 1 step, 1 op -- the whole derivation MERGED into one calculator expression (earlier
             results substituted as sub-expressions); the tool_call step is itself terminal.
  terse    : K tool_call steps (one per annotation); the last tool_call step is terminal.
  standard : K tool_call steps + a terminal decision step grounded (C4) in the last op.
  verbose  : a planning inference step, then per annotation an inference step (the reference
             sentence) followed by its tool_call step, then the terminal decision step.
  evidence : a non-deterministic 'retriever' op read by an evidence_use step (consumed downstream
             by the first computations), K tool_call steps with NUMERIC op outputs, decision step.

FREE-RIDER VARIANTS (reuse adversaries.py semantics; each schema-valid, each rejected)
  bare_assertion : answer asserted with no op; value = answer - 1 (wrong, as adversaries.py)  -> C4
  forged_op      : a calculator op whose inputs compute a different value but whose recorded output
                   is the CORRECT answer (last literal of the final expression decremented by 1,
                   falling back to "(expr)-1")                                                -> C1
  orphan_op      : a real, correct decoy op nothing in the answer cone uses; value = answer + 1 -> TU
  wrong_block    : DIAGNOSTIC ONLY (out-of-scope crossover sweep): shared wrong value answer + 10,
                   bare-assertion structure (fails C4).
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import validation  # audited Component C: whitelisted executor + predicate (imported, never modified)
import agents      # audited Component B: assemble_log / validate_log (imported, never modified)

# ----------------------------------------------------------------------------------------------
# Dataset provenance (pinned)
# ----------------------------------------------------------------------------------------------
DATASET = "GSM8K"
SOURCE_REPO = "https://github.com/openai/grade-school-math"
SOURCE_COMMIT = "3101c7d5072418e28b9008a6636bde82a006892c"
SOURCE_PATH = "grade_school_math/data/test.jsonl"
SOURCE_URL = ("https://raw.githubusercontent.com/openai/grade-school-math/"
              f"{SOURCE_COMMIT}/{SOURCE_PATH}")
SOURCE_SHA256 = "3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14"
SOURCE_N_ROWS = 1319

# Provenance only: the reported runs contain NO model. model_id is never read by the predicate
# or the consensus decision logic (grep: validation.py / metrics.py / sim_ledger.py never mention
# it; consensus.py only in the build_h1 population builder).
SCRIPTED_MODEL_ID = "scripted-gsm8k-reference"

EFFORT_TIERS = ["minimal", "terse", "standard", "verbose", "evidence"]
VARIANTS = ["bare_assertion_wrong", "bare_assertion_correct", "forged_op", "orphan_op"]
WRONG_BLOCK_OFFSET = 10

# Drop reasons in precedence order (the first failing rule is the primary reason).
R_NO_SOLUTION_ANSWER = "no_final_answer_line"
R_NO_ANN = "no_calculator_annotations"
R_MALFORMED = "malformed_annotation"
R_NOT_EXECUTABLE = "annotation_not_executable"
R_MISMATCH = "annotation_value_mismatch"
R_ANSWER_NE_LAST = "final_answer_not_last_op"
R_INCOMPLETE = "incomplete_recorded_derivation"      # rule (c) -- added, flagged
R_TIER_REJECTED = "converted_log_rejected"           # rule (d) -- safety net
DROP_REASONS = [R_NO_SOLUTION_ANSWER, R_NO_ANN, R_MALFORMED, R_NOT_EXECUTABLE, R_MISMATCH,
                R_ANSWER_NE_LAST, R_INCOMPLETE, R_TIER_REJECTED]

_ANN_RE = re.compile(r"<<([^<>]*)>>")
_NUM_TOKEN_RE = re.compile(r"\d+\.\d*|\.\d+|\d+")


def _eq(a: Any, b: Any) -> bool:
    """The predicate's own discrete equality (exact canonical match, tolerance 0)."""
    return validation._values_equal(a, b, task_type="discrete", tol=0.0)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rows(path: str, *, check_sha256: bool = True) -> list[dict]:
    if check_sha256:
        got = file_sha256(path)
        if got != SOURCE_SHA256:
            raise ValueError(f"{path}: sha256 {got} != pinned {SOURCE_SHA256}")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ----------------------------------------------------------------------------------------------
# Parsing + eligibility
# ----------------------------------------------------------------------------------------------
@dataclass
class Annotation:
    expr: str
    value: str          # the reference solution's recorded result, verbatim (stripped)
    sentence: str       # the reference line with <<...>> removed (used as step content)


@dataclass
class Problem:
    index: int                       # 0-based line index in test.jsonl
    question: str
    answer: Optional[str]            # '#### ' answer, thousands separators removed
    annotations: list[Annotation] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    deps: list[list[int]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)     # ALL failing rules (non-exclusive)
    details: list[dict] = field(default_factory=list)    # per-failure diagnostics
    merged_expr: Optional[str] = None
    forged_fallback: Optional[bool] = None

    @property
    def task_id(self) -> str:
        return f"gsm8k-test-{self.index:04d}"

    @property
    def primary_reason(self) -> Optional[str]:
        for r in DROP_REASONS:
            if r in self.reasons:
                return r
        return None

    @property
    def eligible(self) -> bool:
        return not self.reasons


def parse_row(index: int, row: dict) -> Problem:
    solution = row["answer"]
    body, sep, tail = solution.rpartition("####")
    answer = tail.strip().replace(",", "") if sep else None
    p = Problem(index=index, question=row["question"], answer=answer or None)
    for line in (body if sep else solution).splitlines():
        sentence = _ANN_RE.sub("", line).strip()
        for m in _ANN_RE.finditer(line):
            parts = m.group(1).split("=")
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                p.malformed.append(m.group(1))
                continue
            p.annotations.append(Annotation(parts[0].strip(), parts[1].strip(), sentence))
    return p


def _is_num_const(n) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)


def numeric_constants(expr: str) -> list:
    """Numeric literals of an expression. A unary minus applied directly to a literal is read as ONE
    negative literal (audit fix D8: '-30/3' has literal -30, not 30), so a negative earlier result
    is wired to its consumer like any other value."""
    out: list = []

    class _V(ast.NodeVisitor):
        def visit_UnaryOp(self, node):  # noqa: N802
            if isinstance(node.op, ast.USub) and _is_num_const(node.operand):
                out.append(-node.operand.value)
                return
            self.generic_visit(node)

        def visit_Constant(self, node):  # noqa: N802
            if _is_num_const(node):
                out.append(node.value)

    _V().visit(ast.parse(expr, mode="eval"))
    return out


def wire_dependencies(anns: list[Annotation]) -> list[list[int]]:
    """Step i depends on every earlier step j whose recorded value equals a numeric literal of
    expression i (canonical equality)."""
    deps = []
    for i, a in enumerate(anns):
        consts = numeric_constants(a.expr)
        deps.append(sorted({j for c in consts for j in range(i) if _eq(c, anns[j].value)}))
    return deps


def cone(deps: list[list[int]], terminal: int) -> set[int]:
    seen, stack = set(), [terminal]
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        stack.extend(deps[i])
    return seen


def merged_expression(anns: list[Annotation], deps: list[list[int]]) -> str:
    """Inline every earlier result used by the last expression (recursively) so the whole
    derivation becomes ONE calculator expression. Substitution replaces a literal by an expression
    that evaluates to exactly the same number (rule (a)), so the value is unchanged."""
    memo: dict[int, str] = {}

    def build(i: int) -> str:
        if i in memo:
            return memo[i]
        tree = ast.parse(anns[i].expr, mode="eval")

        class _Sub(ast.NodeTransformer):
            def visit_UnaryOp(self, node):  # noqa: N802  (audit fix D8: negative literal)
                if isinstance(node.op, ast.USub) and _is_num_const(node.operand):
                    src = [j for j in deps[i] if _eq(-node.operand.value, anns[j].value)]
                    if src:
                        return ast.parse("(" + build(max(src)) + ")", mode="eval").body
                    return node
                return self.generic_visit(node)

            def visit_Constant(self, node):  # noqa: N802
                if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                    src = [j for j in deps[i] if _eq(node.value, anns[j].value)]
                    if src:
                        return ast.parse(build(max(src)), mode="eval").body
                return node

        memo[i] = ast.unparse(ast.fix_missing_locations(_Sub().visit(tree)))
        return memo[i]

    return build(len(anns) - 1)


def check_problem(p: Problem) -> Problem:
    """Apply rules (a)-(c) (rule (d) is applied by `check_tiers`). Fills p.reasons/p.details."""
    if p.answer is None or not isinstance(validation._canon_value(p.answer), (int, float)):
        p.reasons.append(R_NO_SOLUTION_ANSWER)
    if not p.annotations and not p.malformed:
        p.reasons.append(R_NO_ANN)
    if p.malformed:
        p.reasons.append(R_MALFORMED)
        p.details.append({"rule": R_MALFORMED, "annotations": p.malformed})
    exec_ok = True
    for k, a in enumerate(p.annotations):
        try:
            got = validation._safe_arith(a.expr)
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
            exec_ok = False
            if R_NOT_EXECUTABLE not in p.reasons:
                p.reasons.append(R_NOT_EXECUTABLE)
            p.details.append({"rule": R_NOT_EXECUTABLE, "annotation": k, "expr": a.expr,
                              "recorded": a.value, "error": type(e).__name__})
            continue
        if not _eq(got, a.value):
            exec_ok = False
            if R_MISMATCH not in p.reasons:
                p.reasons.append(R_MISMATCH)
            p.details.append({"rule": R_MISMATCH, "annotation": k, "expr": a.expr,
                              "recorded": a.value, "recomputed": repr(got)})
    if p.annotations and p.answer is not None and not _eq(p.annotations[-1].value, p.answer):
        p.reasons.append(R_ANSWER_NE_LAST)
        p.details.append({"rule": R_ANSWER_NE_LAST, "last_op_value": p.annotations[-1].value,
                          "final_answer": p.answer,
                          "answer_in_earlier_op": any(_eq(a.value, p.answer)
                                                      for a in p.annotations[:-1])})
    if p.annotations and not p.malformed:
        try:
            p.deps = wire_dependencies(p.annotations)
        except SyntaxError:
            p.deps = []
        if p.deps:
            c = cone(p.deps, len(p.annotations) - 1)
            outside = [i for i in range(len(p.annotations)) if i not in c]
            if outside:
                p.reasons.append(R_INCOMPLETE)
                identity = [i for i in range(len(p.annotations))
                            if re.fullmatch(r"\s*[\d.]+\s*", p.annotations[i].expr)]
                p.details.append({"rule": R_INCOMPLETE, "steps_outside_answer_cone": outside,
                                  "has_identity_annotation": bool(identity)})
        if exec_ok and p.deps:
            try:
                p.merged_expr = merged_expression(p.annotations, p.deps)
            except (SyntaxError, RecursionError):
                p.merged_expr = None
    return p


# ----------------------------------------------------------------------------------------------
# Task dict (harness-private 'reference' never reaches a log)
# ----------------------------------------------------------------------------------------------
def make_task(p: Problem, position: int) -> dict:
    return {
        "task_id": p.task_id, "task_type": "discrete", "dataset": DATASET, "prompt": p.question,
        # ---- harness-private keys (agents.assemble_log copies only the four keys above) ----
        "gsm8k_index": p.index,
        "position": position,  # 0-based position in the selected task list (round-robin key)
        "reference": {"annotations": [{"expr": a.expr, "value": a.value, "sentence": a.sentence}
                                      for a in p.annotations],
                      "deps": [list(d) for d in p.deps],
                      "merged_expr": p.merged_expr},
    }


# ----------------------------------------------------------------------------------------------
# Honest payload generator (seam signature: (effort, task) -> JSON string)
# ----------------------------------------------------------------------------------------------
def _typed_number(v: str):
    c = validation._canon_value(v)
    return c if isinstance(c, (int, float)) and not isinstance(c, bool) else v


def _step(i, stype, content, depends_on, produces=None, ref=None) -> dict:
    return {"step_index": i, "step_type": stype, "content": content,
            "depends_on": sorted(set(depends_on)), "produces": produces, "tool_op_ref": ref}


def _calc_op(k: int, expr: str, output, op_prefix="op") -> dict:
    return {"op_id": f"{op_prefix}-{k}", "tool_name": "calculator", "inputs": {"expr": expr},
            "output": output, "deterministic": True, "evidence_hash": None}


def _dump(steps, ops, value, derived) -> str:
    return json.dumps({"reasoning_steps": steps, "tool_operations": ops,
                       "final_output": {"value": value, "derived_from_step": derived}})


def honest_payload(effort: str, task: dict) -> str:
    ref = task["reference"]
    anns, deps = ref["annotations"], ref["deps"]
    K = len(anns)
    answer = anns[-1]["value"]

    if effort == "minimal":
        steps = [_step(0, "tool_call", f"Compute {ref['merged_expr']}; that is the answer.", [],
                       answer, "op-0")]
        return _dump(steps, [_calc_op(0, ref["merged_expr"], answer)], answer, 0)

    if effort == "terse":
        steps = [_step(i, "tool_call", f"Compute {a['expr']}.", deps[i], a["value"], f"op-{i}")
                 for i, a in enumerate(anns)]
        ops = [_calc_op(i, a["expr"], a["value"]) for i, a in enumerate(anns)]
        return _dump(steps, ops, answer, K - 1)

    if effort == "standard":
        steps = [_step(i, "tool_call", a["sentence"] or f"Compute {a['expr']}.", deps[i],
                       a["value"], f"op-{i}") for i, a in enumerate(anns)]
        steps.append(_step(K, "decision", f"The answer is {answer}.", [K - 1], answer))
        ops = [_calc_op(i, a["expr"], a["value"]) for i, a in enumerate(anns)]
        return _dump(steps, ops, answer, K)

    if effort == "verbose":
        steps = [_step(0, "inference",
                       "Identify what the question asks and the quantities it gives.", [])]
        tool_idx: dict[int, int] = {}
        for i, a in enumerate(anns):
            inf_i = len(steps)
            pred = [tool_idx[j] for j in deps[i]] or [0]
            steps.append(_step(inf_i, "inference", a["sentence"] or f"Next compute {a['expr']}.",
                               pred))
            tool_idx[i] = inf_i + 1
            steps.append(_step(inf_i + 1, "tool_call",
                               f"Use the calculator: {a['expr']} = {a['value']}.", [inf_i],
                               a["value"], f"op-{i}"))
        steps.append(_step(len(steps), "decision",
                           f"Combining the computed results, the answer is {answer}.",
                           [tool_idx[K - 1]], answer))
        ops = [_calc_op(i, a["expr"], a["value"]) for i, a in enumerate(anns)]
        return _dump(steps, ops, answer, len(steps) - 1)

    if effort == "evidence":
        prompt = task["prompt"]
        quantities = _NUM_TOKEN_RE.findall(prompt.replace(",", ""))
        ev_op = {"op_id": "op-ev", "tool_name": "retriever", "inputs": {"source": "task.prompt"},
                 "output": {"quantities": quantities},
                 "deterministic": False,
                 "evidence_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}
        steps = [_step(0, "evidence_use", "Read the stated quantities from the problem text.", [],
                       None, "op-ev")]
        for i, a in enumerate(anns):
            pred = [1 + j for j in deps[i]] or [0]
            steps.append(_step(1 + i, "tool_call", f"Calculate {a['expr']}.", pred,
                               a["value"], f"op-{i}"))
        steps.append(_step(K + 1, "decision", f"Answer: {answer}.", [K], answer))
        ops = [ev_op] + [_calc_op(i, a["expr"], _typed_number(a["value"]))
                         for i, a in enumerate(anns)]
        return _dump(steps, ops, answer, K + 1)

    raise ValueError(f"unknown effort tier {effort!r}")


# ----------------------------------------------------------------------------------------------
# Free-rider generators (adversaries.py semantics generalised to multi-step GSM8K derivations)
# ----------------------------------------------------------------------------------------------
def _answer_of(task: dict) -> str:
    return task["reference"]["annotations"][-1]["value"]


def _shift(value: str, delta: int) -> str:
    c = validation._canon_value(value)
    if isinstance(c, int) and not isinstance(c, bool):
        return str(c + delta)
    return repr(float(c) + delta)


def forged_expression(expr: str, answer: str) -> tuple[str, bool]:
    """Return (forged_expr, used_fallback). Decrement the LAST numeric literal of the final
    expression (adversaries.py: a + (b-1)); fall back to '(expr)-1' if that does not parse,
    does not execute, or happens to reproduce the answer."""
    toks = list(_NUM_TOKEN_RE.finditer(expr))
    if toks:
        m = toks[-1]
        c = validation._canon_value(m.group(0))
        new = c - 1
        if new >= 0:
            new_s = str(new) if isinstance(new, int) else repr(new)
            cand = expr[:m.start()] + new_s + expr[m.end():]
            try:
                got = validation._safe_arith(cand)
                if not _eq(got, answer):
                    return cand, False
            except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
                pass
    return f"({expr})-1", True


def _bare_assertion(val: str) -> str:
    steps = [_step(0, "inference", "Recall the quantities mentioned in the question.", []),
             _step(1, "decision", f"So the answer is {val}.", [0], val)]
    return _dump(steps, [], val, 1)


def free_rider_bare_assertion_wrong(effort: str, task: dict) -> str:
    """adversaries.py semantics: asserted answer = answer - 1, no grounding op (fails C4)."""
    return _bare_assertion(_shift(_answer_of(task), -1))


def free_rider_bare_assertion_correct(effort: str, task: dict) -> str:
    """BLUEPRINT D3: asserts the CORRECT answer with no grounding op (fails C4). Byte-identical in
    structure to the wrong-answer variant; only the asserted value differs."""
    return _bare_assertion(_answer_of(task))


free_rider_bare_assertion = free_rider_bare_assertion_wrong      # backward-compatible name


def free_rider_forged_op(effort: str, task: dict) -> str:
    val = _answer_of(task)                                     # CORRECT-looking answer
    last = task["reference"]["annotations"][-1]["expr"]
    forged, _ = forged_expression(last, val)
    steps = [_step(0, "inference", "Identify the quantities to combine.", []),
             _step(1, "tool_call", f"Compute {forged} with the calculator.", [0], val, "op-0"),
             _step(2, "decision", f"The answer is {val}.", [1], val)]
    return _dump(steps, [_calc_op(0, forged, val)], val, 2)


def free_rider_orphan_op(effort: str, task: dict) -> str:
    last = task["reference"]["annotations"][-1]
    val = _shift(last["value"], +1)                            # wrong; ignores the decoy op
    steps = [_step(0, "inference", "Consider the quantities in the problem.", []),
             _step(1, "tool_call", f"Compute {last['expr']}.", [0], last["value"], "op-0"),
             _step(2, "decision", f"The answer is {val}.", [0], val)]   # NOT on the op
    return _dump(steps, [_calc_op(0, last["expr"], last["value"])], val, 2)


def free_rider_wrong_block(effort: str, task: dict) -> str:
    """DIAGNOSTIC ONLY (out-of-scope crossover sweep): shared wrong value answer + 10."""
    val = _shift(_answer_of(task), WRONG_BLOCK_OFFSET)
    steps = [_step(0, "inference", "State an answer for the question.", []),
             _step(1, "decision", f"The answer is {val}.", [0], val)]
    return _dump(steps, [], val, 1)


VARIANT_GENERATORS = {
    "bare_assertion_wrong": free_rider_bare_assertion_wrong,
    "bare_assertion_correct": free_rider_bare_assertion_correct,
    "forged_op": free_rider_forged_op,
    "orphan_op": free_rider_orphan_op,
}
INTENDED_GATE = {"bare_assertion_wrong": "C4", "bare_assertion_correct": "C4", "forged_op": "C1",
                 "orphan_op": "tool_utilization"}


def variant_for(position: int, slot: int, n_free_riders: int) -> str:
    """Round-robin over (task position, free-rider slot): consecutive attacker logs cycle through
    the four variants, so per-variant counts differ by at most one (equal iff 4 | F*T)."""
    return VARIANTS[(position * n_free_riders + slot) % len(VARIANTS)]


def make_round_robin_generator(slot: int, n_free_riders: int):
    def _gen(effort: str, task: dict) -> str:
        return VARIANT_GENERATORS[variant_for(task["position"], slot, n_free_riders)](effort, task)
    return _gen


# ----------------------------------------------------------------------------------------------
# H1b (BLUEPRINT D2): an honest, validly derived DIVERGENT output ("misread operand").
# One operand of the reference derivation that is NOT the result of an earlier step is changed by
# delta; every downstream calculator step is recomputed with the whitelisted executor, so the chain
# re-executes exactly and stays grounded -- a valid derivation of a DIFFERENT (wrong) answer.
# ----------------------------------------------------------------------------------------------
DIVERGENCE_DELTAS = (1, 2, -1, 10)
_EXEC_ERR = (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError)


def _num_str(v) -> str:
    if isinstance(v, bool):
        raise TypeError("bool is not a calculator value")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def _is_clean(v) -> bool:
    s = _num_str(v)
    return "e" not in s and (("." not in s) or len(s.split(".", 1)[1]) <= 2)


def _num_consts(tree) -> list:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)]


def _prompt_numbers(prompt: str) -> list:
    return [validation._canon_value(t) for t in _NUM_TOKEN_RE.findall(prompt.replace(",", ""))]


def _try_divergence(anns: list[dict], deps: list[list[int]], i: int, ci: int, delta: int) -> Optional[dict]:
    """Change constant #ci (ast.walk order) of step i by delta and recompute downstream steps.
    Returns the new annotation list, or None if the candidate is invalid."""
    K = len(anns)
    tree = ast.parse(anns[i]["expr"], mode="eval")
    consts = _num_consts(tree)
    node = consts[ci]
    old = node.value
    if any(_eq(old, anns[j]["value"]) for j in deps[i]):       # wired to an earlier result
        return None
    node.value = old + delta
    if node.value < 0:
        return None
    new_exprs = {i: ast.unparse(tree)}
    new_vals: dict[int, Any] = {}
    try:
        new_vals[i] = validation._safe_arith(new_exprs[i])
    except _EXEC_ERR:
        return None
    changed = {i}
    for m in range(i + 1, K):
        t = ast.parse(anns[m]["expr"], mode="eval")
        touched = False
        for n in _num_consts(t):
            src = [j for j in deps[m] if _eq(n.value, anns[j]["value"])]
            if src and max(src) in changed:                       # same resolution as merged_expression
                n.value = new_vals[max(src)]
                touched = True
        if touched:
            new_exprs[m] = ast.unparse(t)
            try:
                new_vals[m] = validation._safe_arith(new_exprs[m])
            except _EXEC_ERR:
                return None
            changed.add(m)
    out = []
    for k, a in enumerate(anns):
        if k in changed:
            try:
                vs = _num_str(new_vals[k])
            except TypeError:
                return None
            out.append({"expr": new_exprs[k], "value": vs, "sentence": ""})
        else:
            out.append(dict(a))
    return {"annotations": out, "changed": sorted(changed), "old": old, "new": old + delta,
            "clean": all(_is_clean(new_vals[k]) for k in changed)}


def _divergence_search(task: dict, allow_negative_answer: bool) -> Optional[dict]:
    """Deterministic search for the first valid misread-operand derivation. Candidate order:
    operands that literally appear in the question first, then later steps first, then operand
    order, then DIVERGENCE_DELTAS; the first candidate whose recomputed values are 'clean'
    (integers or <= 2 decimals) wins, else the first valid one. Validity: every step re-executes,
    the literal-value dependency wiring is unchanged, the final value differs from the reference
    answer, the merged expression reproduces it, and ALL five honest tiers are accepted."""
    ref = task["reference"]
    anns, deps = ref["annotations"], ref["deps"]
    answer = anns[-1]["value"]
    pnums = _prompt_numbers(task["prompt"])
    cands = []
    for i in range(len(anns)):
        for ci, n in enumerate(_num_consts(ast.parse(anns[i]["expr"], mode="eval"))):
            in_prompt = any(_eq(n.value, q) for q in pnums)
            for di, d in enumerate(DIVERGENCE_DELTAS):
                cands.append((not in_prompt, -i, ci, di, i, d, in_prompt))
    # pass 1: dependency wiring must be unchanged; pass 2: the recomputed wiring may DROP links
    # (only ever a spurious link created by a value collision in the reference, e.g. two steps
    # that both evaluate to 80), never add one. The divergent log uses the recomputed wiring.
    for wiring_rule in ("identical", "subset"):
        first_valid = None
        for _, _, ci, _, i, d, in_prompt in sorted(cands):
            r = _try_divergence(anns, deps, i, ci, d)
            if r is None or _eq(r["annotations"][-1]["value"], answer):
                continue
            if not allow_negative_answer and _is_negative(r["annotations"][-1]["value"]):
                continue                                          # audit O4: non-negativity preference
            objs = [Annotation(expr=a["expr"], value=a["value"], sentence=a["sentence"])
                    for a in r["annotations"]]
            try:
                new_deps = wire_dependencies(objs)
                if wiring_rule == "identical" and new_deps != deps:
                    continue
                if not all(set(nd) <= set(od) for nd, od in zip(new_deps, deps)):
                    continue
                merged = merged_expression(objs, new_deps)
                if not _eq(validation._safe_arith(merged), r["annotations"][-1]["value"]):
                    continue
            except (*_EXEC_ERR, RecursionError):
                continue
            dref = {"annotations": r["annotations"], "deps": [list(x) for x in new_deps],
                    "merged_expr": merged}
            dtask = {**task, "reference": dref}
            if any(gate_breakdown(build_log(honest_payload(t, dtask), dtask))["decision"] != "accept"
                   for t in EFFORT_TIERS):
                continue
            dref["meta"] = {"step": i, "operand": _num_str(r["old"]), "new_operand": _num_str(r["new"]),
                            "delta": d, "operand_in_question": in_prompt, "clean": r["clean"],
                            "steps_recomputed": r["changed"],
                            "divergent_answer": r["annotations"][-1]["value"],
                            "reference_answer": answer, "wiring": wiring_rule,
                            "dropped_links": sum(len(od) - len(nd) for nd, od in zip(new_deps, deps))}
            if r["clean"]:
                return dref
            if first_valid is None:
                first_valid = dref
        if first_valid is not None:
            return first_valid
    return None


def _is_negative(v) -> bool:
    try:
        return float(v) < 0
    except (TypeError, ValueError):
        return False


def divergent_reference(task: dict) -> Optional[dict]:
    """Audit O4: search with a NON-NEGATIVITY PREFERENCE on the divergent answer. A candidate whose
    final (wrong) answer is below zero is implausible as an honest misreading and is rejected in favour
    of the next candidate in the same order. The unconstrained search is run first; only if it selects a
    negative answer is the constrained search run (skipping candidates only removes options, so a
    non-negative unconstrained pick is also the constrained pick). If no non-negative candidate exists
    the unconstrained pick is kept and flagged (preference, not a hard rule). The meta records what the
    preference changed, so the effect is auditable from results.json alone."""
    base = _divergence_search(task, allow_negative_answer=True)
    if base is None or not _is_negative(base["annotations"][-1]["value"]):
        chosen, rejected, fallback = base, None, False
    else:
        pref = _divergence_search(task, allow_negative_answer=False)
        chosen, rejected, fallback = (base, None, True) if pref is None else (pref, base, False)
    if chosen is None:
        return None
    rm = rejected["meta"] if rejected is not None else None
    chosen["meta"].update({
        "nonneg_changed_selection": rejected is not None,
        "nonneg_rejected_candidate": (f"step {rm['step']}: {rm['operand']} -> {rm['new_operand']} (delta {rm['delta']})"
                                      if rm else ""),
        "nonneg_rejected_answer": rm["divergent_answer"] if rm else "",
        "nonneg_fallback_negative_kept": fallback})
    return chosen


def divergent_task(task: dict) -> dict:
    """The task as seen by a divergent honest agent (harness-private; never enters a log)."""
    return {**task, "reference": task["divergent_reference"]}


def attach_divergence(task: dict) -> dict:
    task["divergent_reference"] = divergent_reference(task)
    return task


# ----------------------------------------------------------------------------------------------
# Single-log checks (used by eligibility rule (d), the tests and the sensitivity block)
# ----------------------------------------------------------------------------------------------
PINNED_TIMESTAMP = "2026-01-01T00:00:00Z"   # same pin as adversaries.FIXED_TIMESTAMP
PINNED_RUNTIME_MS = 0.0


def build_log(payload_json: str, task: dict, *, agent_id: Optional[str] = None,
              model_id: str = SCRIPTED_MODEL_ID, sim_tick: int = 1) -> dict:
    return agents.assemble_log(
        agent_id=agent_id or agents.make_did(0), model_id=model_id, task=task,
        reasoning_payload=agents.extract_json_object(payload_json), seed=None, temperature=None,
        sim_tick=sim_tick, runtime_ms=PINNED_RUNTIME_MS, timestamp=PINNED_TIMESTAMP)


def gate_breakdown(log: dict) -> dict:
    """Decision + the three gates + C1..C4 sub-checks, read from the predicate's own functions
    (nothing re-implemented)."""
    v = validation.validate(log)
    _, cst = validation._causal_sufficiency_numeric(log, task_type="discrete", tol=0.0)
    sub = cst["subchecks"]
    return {"decision": v.decision,
            "process_coherence": v.rule_results["process_coherence"],
            "tool_utilization": v.rule_results["tool_utilization"],
            "causal_sufficiency": v.rule_results["causal_sufficiency"],
            "C1": sub["C1_deterministic_ops_reexecute"], "C2": sub["C2_tool_steps_consistent"],
            "C3": sub["C3_output_reproduced_by_terminal"],
            "C4": sub["C4_output_grounded_in_recomputed_op"],
            "recompute_digest": v.recompute_digest}


def tier_decisions(p: Problem) -> dict[str, dict]:
    task = make_task(p, 0)
    return {t: gate_breakdown(build_log(honest_payload(t, task), task)) for t in EFFORT_TIERS}


def check_tiers(p: Problem) -> Problem:
    """Rule (d): every honest tier must be ACCEPTED by the audited predicate."""
    if p.reasons or p.merged_expr is None:
        if not p.reasons:
            p.reasons.append(R_TIER_REJECTED)
            p.details.append({"rule": R_TIER_REJECTED, "why": "merged expression unavailable"})
        return p
    rejected = {t: d for t, d in tier_decisions(p).items() if d["decision"] != "accept"}
    if rejected:
        p.reasons.append(R_TIER_REJECTED)
        p.details.append({"rule": R_TIER_REJECTED, "tiers": sorted(rejected)})
    return p


# ----------------------------------------------------------------------------------------------
# Whole-dataset conversion
# ----------------------------------------------------------------------------------------------
def convert(rows: list[dict], n_select: int = 100) -> dict:
    """Scan EVERY problem (so drop counts are reported for the whole file and for the prefix
    actually scanned to reach n_select eligible problems). Selection = first n_select eligible
    problems by index (deterministic)."""
    problems = [check_tiers(check_problem(parse_row(i, r))) for i, r in enumerate(rows)]
    eligible = [p for p in problems if p.eligible]
    selected = eligible[:n_select]
    if len(selected) < n_select:
        raise RuntimeError(f"only {len(selected)} eligible problems (< {n_select})")
    last_idx = selected[-1].index
    for p in selected:
        _, p.forged_fallback = forged_expression(p.annotations[-1].expr, p.annotations[-1].value)

    def _counts(ps):
        primary = {r: 0 for r in DROP_REASONS}
        any_ = {r: 0 for r in DROP_REASONS}
        for p in ps:
            if p.reasons:
                primary[p.primary_reason] += 1
                for r in set(p.reasons):
                    any_[r] += 1
        return {"scanned": len(ps), "eligible": sum(1 for p in ps if p.eligible),
                "dropped": sum(1 for p in ps if not p.eligible),
                "dropped_by_primary_reason": primary, "failing_rule_counts_non_exclusive": any_}

    return {"problems": problems, "selected": selected,
            "report": {"whole_file": _counts(problems),
                       "selection_prefix": {**_counts([p for p in problems if p.index <= last_idx]),
                                            "last_index_scanned": last_idx}}}
