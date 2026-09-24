#!/usr/bin/env python3
"""
agents.py -- Agent Layer (Component B) for the Byzantine-resilient
peer/process-validation consensus prototype.

Grounded in the IMPLEMENTED sim_ledger.py (Component A) and prototype_plan.md v1.1.

What this module does (and only this):
  * Defines `Agent`, a Mesa Agent subclass. Given a task (task_id, task_type, prompt,
    dataset) and an agent config (DID, model_id, seed, temperature, behavior_class),
    an honest agent reasons via a LangChain -> Ollama client and emits BOTH a final
    output AND a structured action log conforming exactly to the prototype_plan.md
    section 2 schema.
  * Commits each log to the SimulatedLedger via the *log-commit* path ONLY:
    assemble WITHOUT anchors -> `ledger.recompute_hash(log)` -> set log_id == hash to
    that value -> `ledger.commit_log(log)` (which enforces hash == log_id == content-hash).
  * Provides `validate_log(...)`, a purely MECHANICAL schema validator (types, required
    fields, DAG discipline, determinism flag, no ground-truth leakage), structured so it
    can be lifted verbatim into the Step-2 schema module.

Framing constraints honored (Fact Sheet / plan section 0):
  * Process, not output, is the unit of agreement: this layer only produces and commits the
    structured *log*; it never makes agents agree on an output or a single log hash.
  * Substrate-only, log commit is PHASE-INDEPENDENT: this module never touches the verdict
    commit/reveal phase (that is Component D). Only `commit_log` is used here.
  * Not a randomness beacon: nothing from the ledger (head/history/seed) is read as an agent
    input. The only randomness an agent sees is its own sampling seed (recorded in metadata).
  * No ground-truth leakage (section 0.7): honesty/behavior/effort/collusion labels live in the
    experiment label store and the agent *config* -- NEVER in a committed log. `validate_log`
    additionally scans for and rejects such keys as defense-in-depth.

Stack (committed; do not substitute without a recorded decision):
  Python 3.11 (runs on 3.12) | Mesa (population + scheduling) | LangChain (orchestration) |
  Ollama serving Llama 3.1 8B Instruct (model + temperature configurable; default
  `llama3.1:8b`; seed-controlled) | the custom rfc8785-backed SimulatedLedger.

Testability without a live LLM:
  The LLM is reached ONLY through an injectable `model_client(prompt: str) -> str`. The whole
  assemble -> validate -> commit path therefore runs on a *canned* model response. The live
  path (`make_ollama_client`) is a guarded import, so this module loads and the demo/tests run
  with no LangChain/Ollama installed. See `ScriptedModelClient`.

Honest status of structured-log emission from an 8B model: see the `STRUCTURED EMISSION`
section near `build_prompt` / `produce_log`. Anything not yet pinned is marked
"to be finalized during implementation" rather than guessed.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import mesa

from sim_ledger import SimulatedLedger  # Component A (must sit beside this file)

_MESA_MAJOR = int(mesa.__version__.split(".")[0])

DEFAULT_MODEL = "llama3.1:8b"   # the Ollama tag (Llama 3.1 8B Instruct); configurable
DEFAULT_TEMPERATURE = 0.7       # configurable; framing assumes NO determinism (honest divergence)


def _now() -> str:
    """RFC 3339 / ISO 8601 UTC timestamp (matches the ledger's tx timestamps)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# SECTION 1.  SECTION-2 SCHEMA + MECHANICAL VALIDATOR
# ---------------------------------------------------------------------------
# LIFT-INTO-schema.py CANDIDATE (build-order step 2). This block is deliberately
# self-contained -- constants + type predicates + validate_log -- so it can be
# moved verbatim into the Step-2 schema module and shared with Component C.
# It is a SCHEMA validator (structure/types/DAG/determinism/no-leakage). The
# CRYPTOGRAPHIC anchor identity (hash == log_id == content-hash) is the LEDGER's
# job (commit_log enforces it; verify_log_integrity re-checks it) and is NOT
# duplicated here. The CAUSAL-SUFFICIENCY recompute is Component C's job (flag F1).
# ===========================================================================

SCHEMA_VERSION = "1.0"
TASK_TYPES = {"discrete", "continuous", "open_ended"}
STEP_TYPES = {"inference", "tool_call", "evidence_use", "decision"}

# Section 0.7: ground truth is invisible to the protocol. These keys must NEVER appear
# anywhere in a committed log. The real guarantee is that `assemble_log` never writes them;
# this denylist is a defense-in-depth scan so a hand-built / adversarial log is still rejected.
FORBIDDEN_LOG_KEYS = {
    "honesty", "is_honest", "behavior", "behaviour", "behavior_class", "behavior_label",
    "effort", "effort_label", "is_low_effort", "collusion", "collusion_group",
    "collusion_membership", "is_colluder", "is_free_rider", "is_freerider", "is_adversary",
    "adversary", "ground_truth", "groundtruth", "ground_truth_label", "true_label", "role_label",
}

_HEX_RE = re.compile(r"^[0-9a-f]+$")
# W3C DID, generic syntax: did:<method>:<method-specific-id>. Mechanical prefix/shape check
# only -- full did:key multibase decode is out of scope (identity is presupposed; no Sybil
# resistance claimed, section 0.6).
_DID_RE = re.compile(r"^did:[a-z0-9]+:.+")


class SchemaError(ValueError):
    """Raised when a log fails the section-2 schema validation."""


def _is_str(x: Any) -> bool:
    return isinstance(x, str)


def _is_int(x: Any) -> bool:
    # bool is a subclass of int in Python -- exclude it so flags can't masquerade as integers.
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_obj(x: Any) -> bool:
    return isinstance(x, dict)


def _is_str_or_num(x: Any) -> bool:
    return _is_str(x) or _is_num(x)


def _is_str_num_obj(x: Any) -> bool:
    return _is_str(x) or _is_num(x) or _is_obj(x)


def _scan_forbidden(obj: Any, path: str, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in FORBIDDEN_LOG_KEYS:
                out.append(f"{path}.{k}: forbidden ground-truth/label key (violates section 0.7)")
            _scan_forbidden(v, f"{path}.{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_forbidden(v, f"{path}[{i}]", out)


def validate_log(log: Any, *, require_anchors: bool = False) -> tuple[bool, list[str]]:
    """Mechanically validate a log against the section-2 schema.

    Returns (is_valid, errors). `errors` is empty iff valid.

    Checks: required top-level fields and their types; the `task` sub-object; the ORDERED
    `reasoning_steps` (0-based contiguous `step_index`; enum `step_type`; `depends_on` is a
    list of indices that reference only EARLIER steps -- DAG discipline, no forward refs;
    `tool_op_ref` present and either null or resolving to a real `op_id`; optional `produces`
    typed); `tool_operations` (unique `op_id`; the binding `deterministic` boolean; typed
    fields); `final_output` (`value` typed by nothing here but present; `derived_from_step`
    resolving to a real step index); `metadata` (timestamp/sim_tick/seed/temperature present
    and typed; optional runtime_ms); and a section-0.7 forbidden-key scan.

    Anchors: `log_id`/`hash` are the LEDGER's derived identity. With `require_anchors=False`
    (default -- the agent emits an *anchorless* log, then the ledger sets the anchors at
    commit) they are not required, but if present must be strings and mutually equal. With
    `require_anchors=True` they must be present, lowercase-hex, and equal (used when checking
    a fully formed / stored log). The cryptographic check that they equal the content hash is
    `commit_log` / `verify_log_integrity`, not this function.
    """
    errors: list[str] = []

    if not isinstance(log, dict):
        return False, ["log must be a JSON object (dict)"]

    # ---- top-level required fields -----------------------------------------
    if log.get("schema_version") != SCHEMA_VERSION:
        if not _is_str(log.get("schema_version")):
            errors.append("schema_version: required string (e.g. '1.0')")
        # a different but well-typed version is forward-compat, not an error.
    if not _is_str(log.get("agent_id")):
        errors.append("agent_id: required string (W3C DID)")
    elif not _DID_RE.match(log["agent_id"]):
        errors.append("agent_id: must be a W3C DID of the form did:<method>:<id>")
    if not _is_str(log.get("model_id")):
        errors.append("model_id: required string")

    # ---- task ---------------------------------------------------------------
    task = log.get("task")
    if not _is_obj(task):
        errors.append("task: required object")
    else:
        if not _is_str(task.get("task_id")):
            errors.append("task.task_id: required string")
        if task.get("task_type") not in TASK_TYPES:
            errors.append(f"task.task_type: required enum {sorted(TASK_TYPES)}")
        if not _is_str(task.get("dataset")):
            errors.append("task.dataset: required string")
        if not _is_str(task.get("prompt")):
            errors.append("task.prompt: required string")

    # ---- tool_operations (validate first; reasoning steps reference op_ids) -
    tool_ops = log.get("tool_operations")
    op_ids: set[str] = set()
    if not isinstance(tool_ops, list):
        errors.append("tool_operations: required array (may be empty [])")
    else:
        for i, op in enumerate(tool_ops):
            p = f"tool_operations[{i}]"
            if not _is_obj(op):
                errors.append(f"{p}: must be an object")
                continue
            oid = op.get("op_id")
            if not _is_str(oid):
                errors.append(f"{p}.op_id: required string")
            else:
                if oid in op_ids:
                    errors.append(f"{p}.op_id: duplicate op_id '{oid}'")
                op_ids.add(oid)
            if not _is_str(op.get("tool_name")):
                errors.append(f"{p}.tool_name: required string")
            if not _is_obj(op.get("inputs")):
                errors.append(f"{p}.inputs: required object")
            if not _is_str_num_obj(op.get("output")):
                errors.append(f"{p}.output: required string | number | object")
            if not isinstance(op.get("deterministic"), bool):
                errors.append(f"{p}.deterministic: required boolean (binding -- only true ops "
                              f"are re-executed by the predicate)")
            if "evidence_hash" in op and not (op["evidence_hash"] is None or _is_str(op["evidence_hash"])):
                errors.append(f"{p}.evidence_hash: must be string | null when present")

    # ---- reasoning_steps (ORDERED) -----------------------------------------
    steps = log.get("reasoning_steps")
    n_steps = 0
    if not isinstance(steps, list):
        errors.append("reasoning_steps: required array")
    elif len(steps) == 0:
        errors.append("reasoning_steps: must contain at least one step")
    else:
        n_steps = len(steps)
        for i, st in enumerate(steps):
            p = f"reasoning_steps[{i}]"
            if not _is_obj(st):
                errors.append(f"{p}: must be an object")
                continue
            # 0-based, strictly increasing == contiguous index equal to position.
            if not _is_int(st.get("step_index")):
                errors.append(f"{p}.step_index: required integer")
            elif st["step_index"] != i:
                errors.append(f"{p}.step_index: must equal its position {i} "
                              f"(0-based, strictly increasing); got {st['step_index']}")
            if st.get("step_type") not in STEP_TYPES:
                errors.append(f"{p}.step_type: required enum {sorted(STEP_TYPES)}")
            if not _is_str(st.get("content")):
                errors.append(f"{p}.content: required string")
            # depends_on: list of EARLIER indices (DAG; no forward refs).
            dep = st.get("depends_on")
            if not isinstance(dep, list):
                errors.append(f"{p}.depends_on: required array of earlier step indices")
            else:
                for d in dep:
                    if not _is_int(d):
                        errors.append(f"{p}.depends_on: entries must be integers; got {d!r}")
                    elif not (0 <= d < i):
                        errors.append(f"{p}.depends_on: {d} is not an EARLIER step index "
                                      f"(must be 0..{i - 1}) -- forward/self reference rejected")
            # produces: OPTIONAL; if present, string|number|null.
            if "produces" in st and not (st["produces"] is None or _is_str_or_num(st["produces"])):
                errors.append(f"{p}.produces: must be string | number | null when present")
            # tool_op_ref: REQUIRED key; null or a resolving op_id.
            if "tool_op_ref" not in st:
                errors.append(f"{p}.tool_op_ref: required key (string op_id or null)")
            else:
                ref = st["tool_op_ref"]
                if ref is not None:
                    if not _is_str(ref):
                        errors.append(f"{p}.tool_op_ref: must be a string op_id or null")
                    elif ref not in op_ids:
                        errors.append(f"{p}.tool_op_ref: '{ref}' does not resolve to any "
                                      f"tool_operations.op_id")
                # semantic tightening consistent with section 2: a tool_call invokes a tool.
                if st.get("step_type") == "tool_call" and ref is None:
                    errors.append(f"{p}: step_type 'tool_call' must reference a tool_op via "
                                  f"tool_op_ref")

    # ---- final_output -------------------------------------------------------
    fo = log.get("final_output")
    if not _is_obj(fo):
        errors.append("final_output: required object {value, derived_from_step}")
    else:
        if "value" not in fo or not _is_str_num_obj(fo.get("value")):
            errors.append("final_output.value: required string | number | object")
        dfs = fo.get("derived_from_step")
        if not _is_int(dfs):
            errors.append("final_output.derived_from_step: required integer")
        elif n_steps and not (0 <= dfs < n_steps):
            errors.append(f"final_output.derived_from_step: {dfs} does not resolve to an "
                          f"existing step index (0..{n_steps - 1})")

    # ---- metadata -----------------------------------------------------------
    md = log.get("metadata")
    if not _is_obj(md):
        errors.append("metadata: required object")
    else:
        if not _is_str(md.get("timestamp")):
            errors.append("metadata.timestamp: required string (RFC 3339)")
        if not _is_int(md.get("sim_tick")):
            errors.append("metadata.sim_tick: required integer")
        if "seed" not in md or not (md["seed"] is None or _is_int(md["seed"])):
            errors.append("metadata.seed: required integer | null")
        if "temperature" not in md or not (md["temperature"] is None or _is_num(md["temperature"])):
            errors.append("metadata.temperature: required number | null")
        if "runtime_ms" in md and not _is_num(md["runtime_ms"]):
            errors.append("metadata.runtime_ms: must be a number when present")

    # ---- anchors (identity is the ledger's; format-checked here) -----------
    lid, h = log.get("log_id"), log.get("hash")
    if require_anchors:
        for name, val in (("log_id", lid), ("hash", h)):
            if not _is_str(val) or not _HEX_RE.match(val):
                errors.append(f"{name}: required lowercase-hex string when anchors are required")
        if _is_str(lid) and _is_str(h) and lid != h:
            errors.append("log_id must equal hash")
    else:
        for name, val in (("log_id", lid), ("hash", h)):
            if val is not None and not _is_str(val):
                errors.append(f"{name}: must be a string if present")
        if _is_str(lid) and _is_str(h) and lid != h:
            errors.append("log_id must equal hash")

    # ---- section 0.7: no ground-truth leakage ------------------------------
    _scan_forbidden(log, "$", errors)

    return (len(errors) == 0), errors


def assert_valid_log(log: Any, *, require_anchors: bool = False) -> None:
    ok, errors = validate_log(log, require_anchors=require_anchors)
    if not ok:
        raise SchemaError("invalid action log:\n  - " + "\n  - ".join(errors))


# ===========================================================================
# SECTION 2.  MODEL CLIENT (LangChain -> Ollama, guarded) + canned stand-in
# ===========================================================================

def make_ollama_client(model_id: str = DEFAULT_MODEL,
                       temperature: float = DEFAULT_TEMPERATURE,
                       seed: Optional[int] = None,
                       *, base_url: Optional[str] = None,
                       num_ctx: Optional[int] = None,
                       json_mode: bool = True):
    """Build the live LangChain -> Ollama client: `model_client(prompt) -> raw_text`.

    Guarded import: requires `pip install langchain langchain-ollama` and a running Ollama
    daemon with the model pulled (`ollama pull llama3.1:8b`). Raises a clear RuntimeError
    otherwise, so this module still imports and the offline demo/tests run without it.

    `json_mode=True` sets Ollama's JSON mode (`format="json"`), which constrains the model to
    emit syntactically valid JSON -- our first line of defense for structured-log emission.
    NOTE: newer Ollama also accepts a full JSON *schema* in `format=` for structured outputs;
    whether we use plain json-mode + a parser or a pinned JSON-schema is to be finalized during
    implementation (it depends on the pinned Ollama version). `seed` + `temperature=0` makes
    generation best-effort reproducible, but the framing does NOT assume determinism.
    """
    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage
    except Exception as e:  # noqa: BLE001 -- surface a precise, actionable message
        raise RuntimeError(
            "live Ollama path unavailable: install `langchain` and `langchain-ollama` and run "
            "an Ollama daemon with the model pulled (`ollama pull llama3.1:8b`). For tests/demo "
            "without a model, inject a ScriptedModelClient instead."
        ) from e

    kwargs: dict[str, Any] = {"model": model_id, "temperature": temperature}
    if seed is not None:
        kwargs["seed"] = seed
    if base_url is not None:
        kwargs["base_url"] = base_url
    if num_ctx is not None:
        kwargs["num_ctx"] = num_ctx
    if json_mode:
        kwargs["format"] = "json"
    llm = ChatOllama(**kwargs)

    def _call(prompt: str) -> str:
        resp = llm.invoke([HumanMessage(content=prompt)])
        return resp.content if hasattr(resp, "content") else str(resp)

    return _call


class ScriptedModelClient:
    """Deterministic stand-in for the LangChain -> Ollama client, for unit tests and the
    offline demo (no live model). Returns canned raw strings in order; the LAST is repeated
    if called again. Pass several responses to exercise the retry path (e.g. a malformed
    reply followed by a good one)."""

    def __init__(self, *responses: str) -> None:
        if not responses:
            raise ValueError("ScriptedModelClient needs at least one canned response")
        self._responses = list(responses)
        self._i = 0

    def __call__(self, prompt: str) -> str:  # noqa: D401 -- callable client
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return r


# ===========================================================================
# SECTION 3.  STRUCTURED EMISSION -- prompt, JSON extraction, retries
# ---------------------------------------------------------------------------
# HONEST STATUS. An 8B instruct model is NOT reliably a clean JSON emitter. We handle this
# in three layers, none of which fabricates a passing log:
#   (1) CONSTRAIN: Ollama JSON mode (format="json") forces valid JSON; the prompt pins the
#       exact reasoning-payload shape and the DAG/determinism rules.
#   (2) PARSE: `extract_json_object` strips code fences and pulls the first balanced JSON
#       object, so stray prose around the JSON is tolerated.
#   (3) RETRY then FAIL LOUD: `produce_log` re-prompts (with the validator's error as a repair
#       hint) up to `max_retries`; if the model still cannot produce a SCHEMA-VALID log, it
#       raises StructuredEmissionError. A malformed model output therefore never reaches the
#       ledger, and the failure is surfaced (and countable by the harness), not hidden.
# The model only authors the *reasoning payload* (reasoning_steps / tool_operations /
# final_output). Identity, the task echo, and metadata (sim_tick/seed/temperature/timestamp)
# are HARNESS-OWNED in `assemble_log`, so the model cannot forge them or smuggle section-0.7
# labels. Exact retry budget and any narrow type-coercion tolerance are to be finalized during
# implementation against the pinned model.
# ===========================================================================

class StructuredEmissionError(RuntimeError):
    """The model failed to emit a parseable, schema-valid log within the retry budget."""


_REASONING_CONTRACT = """You are a problem-solving agent. Solve the task and return ONLY a single
JSON object (no prose, no markdown, no code fences) describing your reasoning PROCESS.

The JSON object MUST have exactly these top-level keys:
  "reasoning_steps": ordered array. Each element:
      "step_index"  : integer, 0-based, strictly increasing (0,1,2,...).
      "step_type"   : one of "inference" | "tool_call" | "evidence_use" | "decision".
      "content"     : string, the natural-language reasoning for this step.
      "depends_on"  : array of integers, indices of EARLIER steps this step builds on
                      (no forward references; step 0 uses []).
      "produces"    : optional machine-checkable value this step yields (string or number),
                      or null.
      "tool_op_ref" : the op_id of a tool_operations entry if this step calls a tool, else null.
                      A "tool_call" step MUST set a non-null tool_op_ref.
  "tool_operations": array (may be []). Each element:
      "op_id"        : string, referenced by tool_op_ref.
      "tool_name"    : string, e.g. "calculator".
      "inputs"       : object, the tool arguments.
      "output"       : the tool's returned value (string, number, or object).
      "deterministic": boolean. true if re-running inputs reproduces output exactly.
      "evidence_hash": optional string hash of retrieved evidence, or null.
  "final_output": object:
      "value"            : the answer (string or number for arithmetic).
      "derived_from_step": integer, the step_index the answer is read from.

Rules: every tool_op_ref and final_output.derived_from_step must reference something that exists;
depends_on may reference only earlier steps. Do NOT include any honesty, behavior, effort, or
role labels. Output the JSON object and nothing else.
"""


def build_prompt(task: dict) -> str:
    """Compose the agent prompt: the reasoning-payload contract + the task."""
    return (
        f"{_REASONING_CONTRACT}\n"
        f"Task type: {task['task_type']}\n"
        f"Dataset: {task['dataset']}\n"
        f"Task id: {task['task_id']}\n"
        f"Question:\n{task['prompt']}\n"
    )


def _repair_hint(err: Exception) -> str:
    return ("Your previous response was rejected: " + str(err).replace("\n", " ")[:600] +
            " Respond again with ONLY one valid JSON object matching the schema. No prose.")


def extract_json_object(text: str) -> dict:
    """Extract the first balanced JSON object from `text` and parse it.

    Tolerates code fences and surrounding prose (common with instruct models even under
    JSON mode). Raises StructuredEmissionError if no parseable object is found.
    """
    if not isinstance(text, str):
        raise StructuredEmissionError(f"model returned non-text response of type {type(text)}")
    s = text.strip()
    # strip a leading ```json / ``` fence and a trailing fence if present
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    start = s.find("{")
    if start == -1:
        raise StructuredEmissionError("no JSON object found in model response")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = s[start:i + 1]
                try:
                    obj = json.loads(candidate)
                except json.JSONDecodeError as e:
                    raise StructuredEmissionError(f"JSON parse failed: {e}") from e
                if not isinstance(obj, dict):
                    raise StructuredEmissionError("top-level JSON value is not an object")
                return obj
    raise StructuredEmissionError("unbalanced JSON braces in model response")


# ===========================================================================
# SECTION 4.  LOG ASSEMBLY + FINALIZE/COMMIT  (pure; unit-testable w/o an LLM)
# ===========================================================================

def assemble_log(*, agent_id: str, model_id: str, task: dict, reasoning_payload: dict,
                 seed: Optional[int], temperature: Optional[float], sim_tick: int,
                 runtime_ms: Optional[float] = None, timestamp: Optional[str] = None) -> dict:
    """Assemble a section-2 action log WITHOUT the `hash`/`log_id` anchors.

    The model authors only `reasoning_payload` (reasoning_steps / tool_operations /
    final_output). Identity, the task echo, and metadata are harness-owned here -- so the
    model cannot forge sim_tick/seed/agent_id or inject section-0.7 labels. The returned dict
    is intentionally anchorless: the caller computes the hash via the ledger and sets the
    anchors (see `finalize_and_commit`). Key order is irrelevant -- JCS canonicalization sorts
    keys before hashing.
    """
    if not isinstance(reasoning_payload, dict):
        raise StructuredEmissionError("reasoning payload is not a JSON object")
    md: dict[str, Any] = {
        "timestamp": timestamp or _now(),
        "sim_tick": sim_tick,
        "seed": seed,
        "temperature": temperature,
    }
    if runtime_ms is not None:
        md["runtime_ms"] = runtime_ms
    return {
        "schema_version": SCHEMA_VERSION,
        "agent_id": agent_id,
        "model_id": model_id,
        "task": {
            "task_id": task["task_id"],
            "task_type": task["task_type"],
            "dataset": task["dataset"],
            "prompt": task["prompt"],
        },
        "reasoning_steps": reasoning_payload.get("reasoning_steps"),
        "tool_operations": reasoning_payload.get("tool_operations"),
        "final_output": reasoning_payload.get("final_output"),
        "metadata": md,
    }


def finalize_and_commit(log: dict, ledger: SimulatedLedger) -> str:
    """Anchor and commit an *anchorless* log via the ledger's log-commit path ONLY.

    Steps (exactly as specified): validate the schema -> `ledger.recompute_hash(log)` ->
    set `log_id == hash` to that value -> `ledger.commit_log(log)` (which itself enforces
    hash == log_id == content-hash). Returns the `log_id` (== content hash). A schema-invalid
    log is refused here so it never reaches the store. This path is phase-independent: it does
    NOT touch the verdict commit/reveal lifecycle.
    """
    ok, errors = validate_log(log, require_anchors=False)
    if not ok:
        raise SchemaError("refusing to commit an invalid log:\n  - " + "\n  - ".join(errors))
    if "hash" in log or "log_id" in log:
        raise SchemaError("finalize_and_commit expects an anchorless log "
                          "(no 'hash'/'log_id'); they are set here from the content hash)")
    h = ledger.recompute_hash(log)   # the ledger is the single source of hash identity
    log["log_id"] = h
    log["hash"] = h
    return ledger.commit_log(log)    # enforces hash == log_id == content-hash; returns log_id


# ===========================================================================
# SECTION 5.  AGENT (Mesa Agent subclass) + behavior config
# ===========================================================================

# did:key base58btc alphabet (no 0 O I l) -- used only to shape a *placeholder* DID.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def make_did(index: int, *, salt: str = "thesis-demo") -> str:
    """Deterministic, syntactically-shaped W3C `did:key` PLACEHOLDER for a prototype agent.

    Identity is presupposed (no Sybil resistance claimed, section 0.6), so a placeholder is
    sufficient here. This is NOT a real multicodec-wrapped Ed25519 key; minting genuine
    did:key values (Ed25519 keypair -> 0xed01 multicodec -> multibase base58btc) is to be
    finalized during implementation and does not affect the consensus logic.
    """
    n = int.from_bytes(hashlib.sha256(f"{salt}:{index}".encode("utf-8")).digest(), "big")
    s = ""
    while len(s) < 21:
        n, r = divmod(n, 58)
        s += _B58[r]
    return "did:key:z6Mk" + s


@dataclass
class AgentConfig:
    """Per-agent configuration. `behavior_class` is a HARNESS label and lives here / in the
    experiment label store ONLY -- it is never written into a committed log (section 0.7)."""
    agent_id: str                         # W3C DID (presupposed)
    model_id: str = DEFAULT_MODEL
    seed: Optional[int] = None
    temperature: float = DEFAULT_TEMPERATURE
    behavior_class: str = "honest"        # honest | colluder | free_rider | byzantine_validator (E)


class Agent(mesa.Agent):
    """A Mesa agent that reasons via LangChain -> Ollama and emits + commits a section-2 log.

    Adversary variants (Component E) are behavior subclasses overriding `reason`/`produce_log`;
    the honest path here is the baseline. Only the log-commit path is exercised -- verdict
    commit/reveal (Component D) is out of scope for this layer.
    """

    def __init__(self, model: mesa.Model, config: AgentConfig, model_client,
                 ledger: Optional[SimulatedLedger] = None, *, max_retries: int = 2) -> None:
        if _MESA_MAJOR >= 3:
            super().__init__(model)                       # Mesa >= 3: unique_id auto-assigned
        else:                                             # Mesa 1.x/2.x: (unique_id, model)
            super().__init__(config.agent_id, model)
        self.config = config
        self.agent_id = config.agent_id
        self.model_id = config.model_id
        self.seed = config.seed
        self.temperature = config.temperature
        self.behavior_class = config.behavior_class       # harness-only; never logged
        self.model_client = model_client
        self.ledger = ledger
        self.max_retries = max_retries
        self.results: list[dict] = []                     # [{task_id, log_id, final_output}]

    # ---- emit (LLM-touching, with constrained parse + retry) ---------------
    def produce_log(self, task: dict, sim_tick: int) -> dict:
        """Reason about `task` and return a schema-valid, ANCHORLESS section-2 log.

        Re-prompts up to `max_retries` on parse/schema failure (feeding the error back as a
        repair hint). Raises StructuredEmissionError if no schema-valid log is produced -- the
        only LLM-touching method; everything downstream is pure.
        """
        prompt = build_prompt(task)
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            p = prompt if attempt == 0 else f"{prompt}\n{_repair_hint(last_err)}"
            t0 = time.perf_counter()
            raw = self.model_client(p)
            runtime_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            try:
                payload = extract_json_object(raw)
                log = assemble_log(
                    agent_id=self.agent_id, model_id=self.model_id, task=task,
                    reasoning_payload=payload, seed=self.seed, temperature=self.temperature,
                    sim_tick=sim_tick, runtime_ms=runtime_ms,
                )
                ok, errors = validate_log(log, require_anchors=False)
                if not ok:
                    raise SchemaError("; ".join(errors))
                return log
            except (StructuredEmissionError, SchemaError, ValueError, KeyError, TypeError) as e:
                last_err = e
        raise StructuredEmissionError(
            f"agent {self.agent_id}: no schema-valid log for task '{task.get('task_id')}' "
            f"after {self.max_retries + 1} attempt(s); last error: {last_err}"
        )

    def solve(self, task: dict, sim_tick: int) -> tuple[Any, dict]:
        """Return (final_output, anchorless_log) WITHOUT committing."""
        log = self.produce_log(task, sim_tick)
        return log["final_output"], log

    def solve_and_commit(self, task: dict, ledger: Optional[SimulatedLedger] = None,
                         sim_tick: Optional[int] = None) -> tuple[Any, str, dict]:
        """Reason, emit a section-2 log, and commit it to the ledger (log-commit path).

        Returns (final_output, log_id, committed_log).
        """
        ledger = ledger or self.ledger
        if ledger is None:
            raise ValueError("solve_and_commit needs a ledger (pass one or set self.ledger)")
        if sim_tick is None:
            sim_tick = int(getattr(self.model, "tick", 0))
        log = self.produce_log(task, sim_tick)
        log_id = finalize_and_commit(log, ledger)
        self.results.append({"task_id": task["task_id"], "log_id": log_id,
                             "final_output": log["final_output"]})
        return log["final_output"], log_id, log

    # ---- Mesa scheduler hook ----------------------------------------------
    def step(self) -> None:
        """Mesa step: solve the model's current task once and commit. No-op if unset."""
        task = getattr(self.model, "current_task", None)
        if task is None or self.ledger is None:
            return
        self.solve_and_commit(task, self.ledger, int(getattr(self.model, "tick", 0)))


# ===========================================================================
# SECTION 6.  RUNNABLE DEMO -- 3 Mesa agents, ONE task, commit to the ledger
# ===========================================================================

class ConsensusModel(mesa.Model):
    """Minimal Mesa model hosting the agent population for the demo. The scheduler RNG
    (`self.rng`, seeded) only orders agent activation -- it is environment, never read by an
    agent as a coordination input (section 0.2)."""

    def __init__(self, configs: list[AgentConfig], clients: list, ledger: SimulatedLedger,
                 task: dict, seed: Optional[int] = None) -> None:
        super().__init__(rng=seed)            # Mesa 3.5: use rng= (seed= is deprecated)
        self.ledger = ledger
        self.current_task = task
        self.tick = 0
        for cfg, client in zip(configs, clients):
            Agent(self, cfg, client, ledger)

    def step(self) -> None:
        self.tick += 1
        self.agents.shuffle_do("step")        # shuffled activation via the seeded scheduler RNG


# Canned GSM8K-style reasoning payloads (the "model" output) for the OFFLINE demo -- three
# honest, individually-valid logs that all reproduce 37 via slightly different processes.
# In a live run these come from Ollama; swapping `make_ollama_client(...)` in for the
# ScriptedModelClient below is the only change.
_DEMO_PAYLOAD_A = json.dumps({
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Total marbles = red + blue.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Compute 12 + 25 with the calculator.",
         "depends_on": [0], "produces": "37", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "The total number of marbles is 37.",
         "depends_on": [1], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12+25"},
         "output": "37", "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": "37", "derived_from_step": 2},
})

_DEMO_PAYLOAD_B = json.dumps({
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference",
         "content": "Identify the two quantities: 12 red and 25 blue marbles.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "inference",
         "content": "They are disjoint groups, so the total is their sum.",
         "depends_on": [0], "produces": None, "tool_op_ref": None},
        {"step_index": 2, "step_type": "tool_call", "content": "Add 12 and 25.",
         "depends_on": [1], "produces": "37", "tool_op_ref": "op-0"},
        {"step_index": 3, "step_type": "decision", "content": "Total marbles = 37.",
         "depends_on": [2], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "12 + 25"},
         "output": "37", "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": "37", "derived_from_step": 3},
})

_DEMO_PAYLOAD_C = json.dumps({
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference",
         "content": "The question asks for the combined count of red and blue marbles.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Use the calculator to sum 12 and 25.",
         "depends_on": [0], "produces": "37", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "evidence_use",
         "content": "The calculator returned 37, which is the total.",
         "depends_on": [1], "produces": "37", "tool_op_ref": None},
        {"step_index": 3, "step_type": "decision", "content": "Answer: 37 marbles.",
         "depends_on": [2], "produces": "37", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "sum(12, 25)"},
         "output": 37, "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": "37", "derived_from_step": 3},
})

_DEMO_TASK = {
    "task_id": "gsm8k-00042",
    "task_type": "discrete",
    "dataset": "GSM8K",
    "prompt": "A box has 12 red and 25 blue marbles. How many marbles total?",
}


def run_demo() -> SimulatedLedger:
    """Three Mesa agents solve ONE task, each emits a schema-valid log, each is committed to
    a fresh SimulatedLedger. Prints each log, its log_id, and the resulting ledger entries.
    Runs fully offline via ScriptedModelClient (no Ollama in this environment)."""
    ledger = SimulatedLedger()
    canned = [_DEMO_PAYLOAD_A, _DEMO_PAYLOAD_B, _DEMO_PAYLOAD_C]
    configs = [AgentConfig(agent_id=make_did(i), model_id=DEFAULT_MODEL, seed=101 + i,
                           temperature=DEFAULT_TEMPERATURE, behavior_class="honest")
               for i in range(3)]
    clients = [ScriptedModelClient(p) for p in canned]

    print("=" * 78)
    print("Agent Layer (Component B) demo -- 3 Mesa agents, one task, log-commit path")
    print("OFFLINE: model_client = ScriptedModelClient (no live Ollama in this environment).")
    print("Swap make_ollama_client(model_id='llama3.1:8b', temperature=0.7, seed=...) for the")
    print("live path; nothing else changes.")
    print("=" * 78)
    print(f"\nTask: {_DEMO_TASK['task_id']} [{_DEMO_TASK['task_type']}/{_DEMO_TASK['dataset']}]"
          f"  ->  {_DEMO_TASK['prompt']}")

    model = ConsensusModel(configs, clients, ledger, _DEMO_TASK, seed=7)
    model.step()  # tick -> 1; every agent solves the task once and commits its log

    # agents activate in scheduler-shuffled order; report deterministically by DID
    for agent in sorted(model.agents, key=lambda a: a.agent_id):
        res = agent.results[-1]
        log = ledger.get_log(res["log_id"])
        print("\n" + "-" * 78)
        print(f"agent_id (DID): {agent.agent_id}")
        print(f"returned final_output: {res['final_output']}")
        print(f"returned log_id (= content hash): {res['log_id']}")
        print(f"verify_log_integrity({res['log_id'][:12]}...): "
              f"{ledger.verify_log_integrity(res['log_id'])}")
        print("committed log:")
        print(json.dumps(log, indent=2, ensure_ascii=False))

    print("\n" + "=" * 78)
    print("Resulting ledger entries (tx_type == 'log_commit'):")
    for tx in ledger.get_history({"tx_type": "log_commit"}):
        pl = tx["payload"]
        print(f"  idx={tx['index']}  tick={tx['sim_tick']}  agent={pl['agent_id']}  "
              f"task={pl['task_id']}  log_id={pl['log_id'][:16]}...")
    distinct = {a.results[-1]["log_id"] for a in model.agents}
    print(f"\ndistinct logs committed: {len(distinct)} / {len(model.agents)} agents")
    print(f"whole-ledger verify(): {ledger.verify()}")
    print("note: each log is content-addressed INDIVIDUALLY -- agents never agree on a single")
    print("log hash (that would be output/artifact equality, which the framing rejects).")
    print("=" * 78)
    return ledger


if __name__ == "__main__":
    run_demo()
