#!/usr/bin/env python3
"""
validate_live.py -- LIVE-MODEL validation harness for the Agent Layer (Component B).

The live-model proof the offline A.2 suite (ScriptedModelClient) could not give: it exercises
the EXISTING, AUDITED `produce_log` against a REAL Ollama-served Llama 3.1 8B and MEASURES how
reliably an 8B model emits a schema-valid section-2 action log. It does not try to make the
model pass; it reports the real rate and the real failure modes.

IMPORTANT -- agents.py / sim_ledger.py are NOT modified or re-implemented here. This file only
IMPORTS from them. The measured path is exactly the audited one:
    Agent(...).produce_log(task, sim_tick)   # constrain -> parse -> validate_log -> (retry)
A transparent `RecordingClient` wraps the live model client so the harness can count attempts
and categorize the final failure WITHOUT changing produce_log (produce_log calls the client
once per attempt). Every schema-valid log is committed via the real `finalize_and_commit`, and
`verify_log_integrity` (per log) + whole-ledger `verify()` are checked at the end.

What is measured, per item:
  * was a schema-valid section-2 log produced within max_retries?
  * how many attempts did it take?
  * on failure: the category -- JSON parse failure, or which validate_log rule failed
    (DAG/depends_on, tool_op_ref resolution, derived_from_step, step_type/tool_call,
    missing/typed field, enum, determinism flag, ...).

GSM8K source: `datasets` (load_dataset("gsm8k","main","test"), sampled). If `datasets` is
unavailable (or its hub is unreachable), it FALLS BACK to a small set of FRESHLY-AUTHORED
arithmetic word problems (clearly labelled smoke-test stand-ins -- no GSM8K text is embedded in
this file) and the summary states which source was used.

--json-schema toggles Ollama's JSON-SCHEMA structured-output format against plain json-mode, so
you can compare emission rates between the two. This is the to-be-finalized decision the data
should inform. Because make_ollama_client (audited) exposes only json-mode, the JSON-schema path
uses a small LOCAL client builder here that passes a JSON Schema to ChatOllama's `format=`;
agents.py is left untouched.

----------------------------------------------------------------------------------------------
SETUP (run on a machine with Ollama + a GPU/enough RAM for an 8B model):

    # 1. Python deps (in your venv)
    pip install langchain langchain-ollama datasets

    # 2. Ollama daemon + model
    ollama serve                 # one terminal (or ensure the service is running)
    ollama pull llama3.1:8b      # ~4.7 GB

    # 3. Run the live validation (plain json-mode)
    python validate_live.py --n 20 --retries 2 --model llama3.1:8b --temperature 0.7 --seed 0

    # 4. Compare emission under Ollama JSON-SCHEMA vs plain json-mode
    python validate_live.py --n 20 --json-schema

Offline harness self-test (NO model -- verifies the measurement/commit/verify plumbing only):
    python validate_live.py --selftest --n 12
----------------------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
import time
from collections import Counter
from typing import Any, Optional

import mesa

# ---- import ONLY (no modification) from the audited modules -----------------
from agents import (
    Agent, AgentConfig, make_did, make_ollama_client,
    assemble_log, validate_log, finalize_and_commit, extract_json_object,
    StructuredEmissionError, SchemaError,
)
from sim_ledger import SimulatedLedger


# ===========================================================================
# JSON SCHEMA for the reasoning payload (used only with --json-schema).
# Constrains the SHAPE/TYPES the model must emit. It CANNOT express the cross-field
# constraints (depends_on references earlier indices; tool_op_ref / derived_from_step
# resolve) -- those remain validate_log's job. Exact schema-feature acceptance depends on the
# pinned Ollama / langchain-ollama version (to be finalized during implementation).
# ===========================================================================
REASONING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reasoning_steps", "tool_operations", "final_output"],
    "properties": {
        "reasoning_steps": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["step_index", "step_type", "content", "depends_on", "tool_op_ref"],
                "properties": {
                    "step_index": {"type": "integer"},
                    "step_type": {"type": "string",
                                  "enum": ["inference", "tool_call", "evidence_use", "decision"]},
                    "content": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "integer"}},
                    "produces": {"type": ["string", "number", "null"]},
                    "tool_op_ref": {"type": ["string", "null"]},
                },
            },
        },
        "tool_operations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["op_id", "tool_name", "inputs", "output", "deterministic"],
                "properties": {
                    "op_id": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "inputs": {"type": "object"},
                    "output": {"type": ["string", "number", "object"]},
                    "deterministic": {"type": "boolean"},
                    "evidence_hash": {"type": ["string", "null"]},
                },
            },
        },
        "final_output": {
            "type": "object",
            "required": ["value", "derived_from_step"],
            "properties": {
                "value": {"type": ["string", "number", "object"]},
                "derived_from_step": {"type": "integer"},
            },
        },
    },
}


def make_ollama_schema_client(model_id: str, temperature: float, seed: Optional[int],
                              schema: dict, *, base_url: Optional[str] = None,
                              num_ctx: Optional[int] = None):
    """LOCAL sibling of agents.make_ollama_client that passes a JSON *schema* to Ollama's
    `format=` (structured outputs). Kept here, not in agents.py, so the audited module is
    unmodified. Returns `model_client(prompt) -> raw_text`. Guarded import with a clear error.
    """
    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "live JSON-schema path needs `langchain` + `langchain-ollama` (recent enough to "
            "accept a JSON-schema dict in `format=`) and a running Ollama with the model pulled."
        ) from e
    kwargs: dict[str, Any] = {"model": model_id, "temperature": temperature, "format": schema}
    if seed is not None:
        kwargs["seed"] = seed
    if base_url is not None:
        kwargs["base_url"] = base_url
    if num_ctx is not None:
        kwargs["num_ctx"] = num_ctx
    llm = ChatOllama(**kwargs)

    def _call(prompt: str) -> str:
        resp = llm.invoke([HumanMessage(content=prompt)])
        return resp.content if hasattr(resp, "content") else str(resp)

    return _call


# ===========================================================================
# GSM8K loading (datasets) with a freshly-authored fallback (NO GSM8K text here).
# ===========================================================================

# Freshly-authored arithmetic word problems -- SMOKE-TEST STAND-INS ONLY (not GSM8K items).
# Each has an unambiguous integer answer used only for the optional secondary correctness note.
_AUTHORED_FALLBACK = [
    ("A baker made 9 trays of muffins with 6 muffins on each tray, then sold 14 muffins. "
     "How many muffins are left?", 40),
    ("A library had 320 books, lent out 47, and then received a donation of 18 books. "
     "How many books are in the library now?", 291),
    ("A cyclist rides 12 km each morning and 8 km each evening for 5 days. "
     "How many kilometres does the cyclist ride in total?", 100),
    ("A theatre has 18 rows with 24 seats per row. If 95 seats are already booked, "
     "how many seats are still available?", 337),
    ("Maria saves $7 per week for 11 weeks, then spends $23. How many dollars does she have left?",
     54),
    ("A farm collected 156 eggs and packed them into cartons of 12. "
     "How many full cartons are there, and how many eggs are left over? Give the number left over.",
     0),
    ("A school orders 8 boxes of pencils with 45 pencils each, then hands out 110 pencils. "
     "How many pencils remain?", 250),
    ("A water tank holds 500 litres. It loses 35 litres per day for 9 days. "
     "How many litres remain in the tank?", 185),
    ("A runner completes 4 laps of a 350-metre track, then walks another 600 metres. "
     "How many metres did the runner cover in total?", 2000),
    ("A shop sells notebooks for $3 each. If a customer buys 7 notebooks and pays with a $50 "
     "note, how many dollars of change do they receive?", 29),
    ("There are 28 students in a class. Each student folds 15 paper cranes. "
     "If 47 cranes are damaged, how many undamaged cranes remain?", 373),
    ("A train carries 240 passengers. At the first stop 58 get off and 31 get on. "
     "How many passengers are on the train after the first stop?", 213),
]


def _parse_gsm8k_gold(answer_field: str) -> Optional[str]:
    """GSM8K answers end with '#### <number>'. Return the normalized number string, or None."""
    if "####" in answer_field:
        tail = answer_field.split("####")[-1].strip()
        return tail.replace(",", "").replace("$", "").strip()
    return None


def load_problems(n: int, seed: int) -> tuple[list[dict], str]:
    """Return (problems, source_label). Tries real GSM8K via `datasets`; falls back to the
    freshly-authored stand-ins if `datasets` is missing or its hub is unreachable."""
    try:
        from datasets import load_dataset
        ds = load_dataset("gsm8k", "main", split="test")
        rng = random.Random(seed)
        idxs = rng.sample(range(len(ds)), min(n, len(ds)))
        problems = []
        for i in idxs:
            row = ds[int(i)]
            problems.append({
                "task_id": f"gsm8k-test-{int(i):05d}", "task_type": "discrete",
                "dataset": "GSM8K", "prompt": row["question"],
                "_gold": _parse_gsm8k_gold(row.get("answer", "")),
            })
        return problems, f"GSM8K  (datasets: gsm8k/main/test, {len(problems)} sampled, seed={seed})"
    except Exception as e:  # noqa: BLE001 -- datasets missing OR hub unreachable
        problems = []
        for k in range(n):
            prompt, gold = _AUTHORED_FALLBACK[k % len(_AUTHORED_FALLBACK)]
            problems.append({
                "task_id": f"smoke-{k:03d}", "task_type": "discrete",
                "dataset": "AUTHORED_SMOKE_TEST", "prompt": prompt, "_gold": str(gold),
            })
        reason = type(e).__name__
        return problems, (f"FRESHLY-AUTHORED smoke-test stand-ins (NOT GSM8K) -- `datasets` "
                          f"unavailable: {reason}. Install `datasets` for the real GSM8K run.")


# ===========================================================================
# Measurement instrumentation (no modification to produce_log).
# ===========================================================================

class RecordingClient:
    """Transparent proxy around a `model_client(prompt)->str`. Records every (prompt, raw) call
    so the harness can count attempts and categorize the final failure. produce_log calls the
    wrapped client exactly once per attempt, so len(calls) == attempts."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    def __call__(self, prompt: str) -> str:
        raw = self.inner(prompt)
        self.calls.append({"prompt": prompt, "raw": raw})
        return raw

    def reset(self) -> None:
        self.calls = []


_RULE_BUCKETS = [
    ("dag_depends_on", ("depends_on", "forward", "earlier step")),
    ("tool_op_ref_resolution", ("tool_op_ref",)),
    ("derived_from_step", ("derived_from_step",)),
    ("step_type_tool_call", ("tool_call must reference", "step_type 'tool_call'")),
    ("step_index_order", ("step_index",)),
    ("step_type_enum", ("step_type",)),
    ("task_type_enum", ("task_type", "task.task_type")),
    ("determinism_flag", ("deterministic",)),
    ("label_leak", ("forbidden ground-truth",)),
    ("final_output_value", ("final_output.value",)),
    ("missing_or_typed_field", ("required",)),
]


def _bucket_line(line: str) -> str:
    low = line.lower()
    for name, needles in _RULE_BUCKETS:
        if any(nd in low for nd in needles):
            return name
    return "other_schema"


def categorize(raw: str, task: dict, cfg: AgentConfig) -> tuple[str, list[str]]:
    """Re-derive the failure category for one raw model response using ONLY public functions
    (extract_json_object / assemble_log / validate_log). Returns (primary_bucket, all_errors)."""
    try:
        payload = extract_json_object(raw)
    except StructuredEmissionError:
        return "json_parse", []
    try:
        log = assemble_log(agent_id=cfg.agent_id, model_id=cfg.model_id, task=task,
                           reasoning_payload=payload, seed=cfg.seed, temperature=cfg.temperature,
                           sim_tick=0, runtime_ms=0.0)
    except StructuredEmissionError:
        return "payload_not_object", []
    ok, errors = validate_log(log, require_anchors=False)
    if ok:
        return "none_unexpected", []
    return _bucket_line(errors[0]), errors


# ===========================================================================
# Self-test mock client (offline plumbing check ONLY -- NOT a model, NOT a measurement).
# ===========================================================================

_SELFTEST_VALID = json.dumps({
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "Read the quantities.",
         "depends_on": [], "produces": None, "tool_op_ref": None},
        {"step_index": 1, "step_type": "tool_call", "content": "Add them.",
         "depends_on": [0], "produces": "42", "tool_op_ref": "op-0"},
        {"step_index": 2, "step_type": "decision", "content": "The total is 42.",
         "depends_on": [1], "produces": "42", "tool_op_ref": None},
    ],
    "tool_operations": [
        {"op_id": "op-0", "tool_name": "calculator", "inputs": {"expr": "a+b"},
         "output": "42", "deterministic": True, "evidence_hash": None},
    ],
    "final_output": {"value": "42", "derived_from_step": 2},
})
_SELFTEST_BAD_DAG = json.dumps({
    "reasoning_steps": [
        {"step_index": 0, "step_type": "inference", "content": "x", "depends_on": [2],
         "produces": None, "tool_op_ref": None},  # forward dependency -> dag_depends_on
        {"step_index": 1, "step_type": "decision", "content": "y", "depends_on": [0],
         "produces": "1", "tool_op_ref": None},
    ],
    "tool_operations": [], "final_output": {"value": "1", "derived_from_step": 1},
})


class _SelfTestClient:
    """DETERMINISTIC MOCK for the harness self-test ONLY. Produces a fixed mix of outcomes
    (first-try success, retry-recovered, exhausted-on-parse, exhausted-on-DAG) so the
    measurement/categorization/commit/verify pipeline is exercised offline. THE NUMBERS IT
    PRODUCES ARE SYNTHETIC AND ARE NOT A MEASUREMENT OF ANY LLM."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def __call__(self, prompt: str) -> str:
        bucket = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), 16) % 5
        n = self._seen.get(prompt, 0)
        self._seen[prompt] = n + 1
        if bucket == 0:
            return _SELFTEST_VALID                      # first-try success
        if bucket == 1:
            return _SELFTEST_VALID if n >= 1 else "sorry, the answer is 42"  # retry (parse)->ok
        if bucket == 2:
            return _SELFTEST_VALID if n >= 1 else _SELFTEST_BAD_DAG          # retry (dag)->ok
        if bucket == 3:
            return _SELFTEST_BAD_DAG                     # always DAG -> exhaust
        return "this is not json at all"                 # always parse fail -> exhaust


# ===========================================================================
# Numbers helpers
# ===========================================================================

def _norm_num(s: Any) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _pct(x: int, d: int) -> str:
    return f"{(100.0 * x / d):.1f}%" if d else "n/a"


# ===========================================================================
# The harness
# ===========================================================================

def build_client(model: str, temperature: float, seed: Optional[int], use_schema: bool):
    if use_schema:
        return make_ollama_schema_client(model, temperature, seed, REASONING_JSON_SCHEMA)
    return make_ollama_client(model_id=model, temperature=temperature, seed=seed, json_mode=True)


_SETUP_HELP = (
    "Setup for the live run:\n"
    "  pip install langchain langchain-ollama datasets\n"
    "  ollama serve                 # ensure the daemon is running\n"
    "  ollama pull llama3.1:8b\n"
    "  python validate_live.py --n 20 --model llama3.1:8b --temperature 0.7 --seed 0\n"
    "Offline plumbing check (no model):  python validate_live.py --selftest"
)


def preflight(client) -> tuple[bool, Optional[Exception]]:
    """One trivial call to confirm the model endpoint is reachable before measuring."""
    try:
        client('Reply with exactly this JSON and nothing else: {"ok": true}')
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, e


def run(args: argparse.Namespace) -> int:
    live = not args.selftest
    fmt_mode = "json-schema" if args.json_schema else "json-mode"

    # ---- client + source ---------------------------------------------------
    if live:
        try:
            client = build_client(args.model, args.temperature, args.seed, args.json_schema)
        except RuntimeError as e:
            print("LIVE MODEL UNAVAILABLE (client could not be built):\n  ", e)
            print("\n" + _SETUP_HELP)
            return 1
        ok, err = preflight(client)
        if not ok:
            print("LIVE MODEL UNREACHABLE (preflight call failed):\n  "
                  f"{type(err).__name__}: {str(err)[:200]}")
            print("\n" + _SETUP_HELP)
            return 1
        problems, source = load_problems(args.n, args.seed)
        model_label = args.model
    else:
        client = _SelfTestClient()
        problems = []
        for k in range(args.n):
            prompt, gold = _AUTHORED_FALLBACK[k % len(_AUTHORED_FALLBACK)]
            problems.append({"task_id": f"selftest-{k:03d}", "task_type": "discrete",
                             "dataset": "AUTHORED_SMOKE_TEST", "prompt": prompt, "_gold": None})
        source = "AUTHORED smoke-test prompts (self-test; no dataset)"
        model_label = "MOCK (_SelfTestClient)"

    recorder = RecordingClient(client)
    ledger = SimulatedLedger()
    model = mesa.Model(rng=args.seed)  # bare host for the Agent population

    # ---- per-item run ------------------------------------------------------
    rows: list[dict] = []
    primary_fail = Counter()           # one category per FAILED item
    all_attempt_fail = Counter()       # category per FAILED ATTEMPT (incl. pre-success retries)
    committed_logs: list[dict] = []
    correct_among_valid = 0
    gold_available = 0

    print("=" * 92)
    print("LIVE-MODEL VALIDATION -- Agent Layer (Component B): produce_log vs "
          + ("Ollama " + args.model if live else "MOCK self-test"))
    if not live:
        print("!! HARNESS SELF-TEST -- MOCK CLIENT -- NOT LLAMA 3.1 8B. The rates below are "
              "SYNTHETIC and !!")
        print("!! are NOT a measurement of the live model. Use them only to verify the harness "
              "plumbing. !!")
    print("=" * 92)
    print(f"source           : {source}")
    print(f"model_id         : {model_label}")
    print(f"format mode      : {fmt_mode}" + ("  (Ollama format=<JSON schema>)" if args.json_schema
                                              else "  (Ollama format=\"json\")"))
    print(f"temperature      : {args.temperature}")
    print(f"seed             : {args.seed}")
    print(f"max_retries      : {args.retries}   (attempts per item <= {args.retries + 1})")
    print(f"N                : {len(problems)}")
    print("-" * 92)

    t_start = time.perf_counter()
    for i, task in enumerate(problems):
        cfg = AgentConfig(agent_id=make_did(i), model_id=(args.model if live else "mock"),
                          seed=args.seed, temperature=args.temperature, behavior_class="honest")
        agent = Agent(model, cfg, recorder, ledger, max_retries=args.retries)
        recorder.reset()
        status, attempts, category, log_id = "ok", 0, None, None
        try:
            log = agent.produce_log(task, sim_tick=i)
            attempts = len(recorder.calls)
            # categorize the FAILED retries that preceded success (if any)
            for c in recorder.calls[:-1]:
                all_attempt_fail[categorize(c["raw"], task, cfg)[0]] += 1
            log_id = finalize_and_commit(log, ledger)
            committed_logs.append(log)
            if task.get("_gold") is not None:
                gold_available += 1
                v = log["final_output"].get("value") if isinstance(log["final_output"], dict) else None
                if _norm_num(v) is not None and _norm_num(v) == _norm_num(task["_gold"]):
                    correct_among_valid += 1
        except StructuredEmissionError:
            status = "fail"
            attempts = len(recorder.calls) or (args.retries + 1)
            for c in recorder.calls:
                all_attempt_fail[categorize(c["raw"], task, cfg)[0]] += 1
            category = categorize(recorder.calls[-1]["raw"], task, cfg)[0] if recorder.calls else "no_output"
            primary_fail[category] += 1
        except Exception as e:  # noqa: BLE001 -- infra (e.g. transient connection): excluded
            status = "infra"
            attempts = len(recorder.calls)
            category = f"{type(e).__name__}"
        rows.append({"task_id": task["task_id"], "status": status, "attempts": attempts,
                     "category": category, "log_id": log_id})
        mark = {"ok": "ok ", "fail": "FAIL", "infra": "INFRA"}[status]
        extra = "" if status == "ok" else f"  <{category}>"
        print(f"  [{i + 1:>3}/{len(problems)}] {task['task_id']:<18} {mark}  "
              f"attempts={attempts}{extra}")
    elapsed = time.perf_counter() - t_start

    # ---- aggregate ---------------------------------------------------------
    n_eval = sum(1 for r in rows if r["status"] in ("ok", "fail"))   # exclude infra
    n_infra = sum(1 for r in rows if r["status"] == "infra")
    first_try = sum(1 for r in rows if r["status"] == "ok" and r["attempts"] == 1)
    final_ok = sum(1 for r in rows if r["status"] == "ok")
    exhausted = sum(1 for r in rows if r["status"] == "fail")
    attempts_all = [r["attempts"] for r in rows if r["status"] in ("ok", "fail")]
    mean_attempts = statistics.mean(attempts_all) if attempts_all else 0.0
    retried_ok = sum(1 for r in rows if r["status"] == "ok" and r["attempts"] > 1)

    print("-" * 92)
    print("RESULTS" + ("  (SYNTHETIC self-test numbers -- not a model measurement)" if not live else ""))
    print(f"  evaluated items (excl. infra)     : {n_eval}" + (f"   [infra excluded: {n_infra}]" if n_infra else ""))
    print(f"  first-attempt schema-valid rate   : {first_try}/{n_eval}  ({_pct(first_try, n_eval)})")
    print(f"  schema-valid after retries (final): {final_ok}/{n_eval}  ({_pct(final_ok, n_eval)})")
    print(f"     of which needed a retry        : {retried_ok}")
    print(f"  exhausted retries (StructuredEmissionError): {exhausted}/{n_eval}  ({_pct(exhausted, n_eval)})")
    print(f"  mean attempts / item              : {mean_attempts:.2f}")
    print(f"  wall time                         : {elapsed:.1f}s  "
          f"({(elapsed / n_eval):.2f}s/item)" if n_eval else "")
    if gold_available:
        print(f"  (secondary, not the headline) final_output.value == gold among schema-valid: "
              f"{correct_among_valid}/{final_ok}  -- B validates EMISSION, not arithmetic; "
              f"answer-correctness is Component C's concern")

    print("\n  failure-mode breakdown (one primary category per FAILED item):")
    if primary_fail:
        for cat, c in primary_fail.most_common():
            print(f"     {cat:<26} {c:>4}  ({_pct(c, exhausted)} of failures)")
    else:
        print("     (none -- every evaluated item produced a schema-valid log)")
    if all_attempt_fail:
        print("\n  rule-violations observed across ALL failed attempts (incl. pre-success retries):")
        for cat, c in all_attempt_fail.most_common():
            print(f"     {cat:<26} {c:>4}")

    # ---- ledger verification ----------------------------------------------
    print("\n" + "-" * 92)
    print("LEDGER (real commit + verification of the schema-valid logs):")
    per_log_ok = all(ledger.verify_log_integrity(r["log_id"])
                     for r in rows if r["status"] == "ok" and r["log_id"])
    commit_txs = ledger.get_history({"tx_type": "log_commit"})
    print(f"  logs committed                    : {len(committed_logs)}")
    print(f"  log_commit tx on chain            : {len(commit_txs)}")
    print(f"  verify_log_integrity all True     : {per_log_ok if committed_logs else 'n/a (none committed)'}")
    print(f"  whole-ledger verify()             : {ledger.verify()}")

    # ---- one full example committed log -----------------------------------
    print("\n" + "-" * 92)
    if committed_logs:
        print("EXAMPLE committed log (first schema-valid log"
              + (" -- mock-sourced content in self-test):" if not live else " from the live model):"))
        print(json.dumps(committed_logs[0], indent=2, ensure_ascii=False))
    else:
        print("EXAMPLE committed log: NONE -- the model produced no schema-valid log at this "
              "setting. See the failure-mode breakdown and interpretation.")

    # ---- honest interpretation (keyed to the measured numbers) ------------
    print("\n" + "=" * 92)
    print("INTERPRETATION" + ("  (self-test plumbing only)" if not live else ""))
    rate = (final_ok / n_eval) if n_eval else 0.0
    dom = primary_fail.most_common(1)[0][0] if primary_fail else None
    if not live:
        print("  Self-test: confirms the harness measures attempts, categorizes failures across")
        print("  JSON-parse and validate_log rules, commits schema-valid logs, and that")
        print("  verify_log_integrity + verify() hold. It says NOTHING about the 8B model -- run")
        print("  the live command for real emission numbers.")
    else:
        if rate >= 0.95:
            print(f"  Emission is reliable at this setting ({_pct(final_ok, n_eval)} final). The plain")
            print("  contract suffices; retries absorb rare blips. Keep json-mode unless latency matters.")
        elif rate >= 0.80:
            print(f"  Usable but not clean ({_pct(final_ok, n_eval)} final; {retried_ok} needed a retry).")
            print("  Retries are doing real work (latency/cost). Try --json-schema and compare.")
        elif rate >= 0.50:
            print(f"  Marginal ({_pct(final_ok, n_eval)} final). The model frequently violates the")
            print("  contract. Strongly consider JSON-schema mode AND/OR coarser reasoning granularity")
            print("  (fewer, larger steps shrink the depends_on/tool_op_ref surface). Re-run both modes.")
        else:
            print(f"  LOW ({_pct(final_ok, n_eval)} final). Do NOT paper over this. The 8B cannot")
            print("  reliably emit this section-2 contract here. Options, in order:")
            print("   (1) --json-schema (constrains SHAPE; see caveat below);")
            print("   (2) change the OUTPUT CONTRACT the model must satisfy -- e.g. have the model")
            print("       emit a flat step list and DERIVE depends_on/tool_op_ref harness-side,")
            print("       shrinking what the model is responsible for;")
            print("   (3) coarser reasoning granularity; (4) a larger actor model.")
            print("  The committed-log path and validate_log are correct -- the bottleneck is the")
            print("  model's structured-emission reliability, which is exactly what this measures.")
        if dom in ("dag_depends_on", "tool_op_ref_resolution", "derived_from_step",
                   "step_type_tool_call"):
            print(f"  Caveat: the dominant failure '{dom}' is a CROSS-FIELD constraint that JSON-schema")
            print("  mode CANNOT enforce, so expect --json-schema to help less here than for")
            print("  missing/typed-field failures. This is the signal for option (2) above.")
    print("=" * 92)

    # acceptance: a real run should commit >=1 log with verify_log_integrity + verify() True.
    return 0 if (ledger.verify() and (committed_logs or not live)) else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Live-model validation harness for the Agent layer (Component B).",
        epilog=_SETUP_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=20, help="number of items to run (default 20)")
    p.add_argument("--retries", type=int, default=2,
                   help="max retries (attempts per item = retries+1; default 2)")
    p.add_argument("--model", type=str, default="llama3.1:8b", help="Ollama model tag")
    p.add_argument("--temperature", type=float, default=0.7, help="sampling temperature")
    p.add_argument("--seed", type=int, default=0, help="sampling + sampling-of-items seed")
    p.add_argument("--json-schema", dest="json_schema", action="store_true",
                   help="use Ollama JSON-schema structured output instead of plain json-mode "
                        "(compare emission rates)")
    p.add_argument("--selftest", action="store_true",
                   help="OFFLINE harness self-test with a deterministic MOCK client (no model); "
                        "verifies measurement/commit/verify plumbing only")
    return p


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
