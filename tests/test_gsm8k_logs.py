#!/usr/bin/env python3
"""test_gsm8k_logs.py -- tests for gsm8k_logs.py (+ stats_ci / experiments helpers).

  1. conversion correctness on three HAND-CHECKED problems (GSM8K test indices 0, 1, 2, embedded
     verbatim below so the test needs no network): annotations, dependency edges, merged expression;
  2. every honest tier is ACCEPTED by the audited predicate (the 3 problems, and all 100 selected
     problems when data/gsm8k_test.jsonl is present);
  3. every free-rider variant is REJECTED on its intended gate (and forged-op keeps the correct answer);
  4. drop-reason categorisation on synthetic records;
  5. sec 0.7: no label value in any generated log; model-agnostic: decision invariant to model_id;
  6. round-robin variant counts; baseline dissent rule; Wilson / Clopper-Pearson sanity.

Run:  pytest -q test_gsm8k_logs.py
"""
from __future__ import annotations

import json
import os

import pytest

import agents
import metrics
import validation
import walking_skeleton as ws
import gsm8k_logs as g
import stats_ci as sc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # repository root
SRC = os.path.join(ROOT, "src")
DATA = os.path.join(ROOT, "data", "gsm8k_test.jsonl")

# GSM8K test.jsonl lines 0-2 (MIT licence, (c) 2021 OpenAI), verbatim.
RAW = [
    {"question": "Janet\u2019s ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market daily for $2 per fresh duck egg. How much in dollars does she make every day at the farmers' market?",
     "answer": "Janet sells 16 - 3 - 4 = <<16-3-4=9>>9 duck eggs a day.\nShe makes 9 * 2 = $<<9*2=18>>18 every day at the farmer\u2019s market.\n#### 18"},
    {"question": "A robe takes 2 bolts of blue fiber and half that much white fiber.  How many bolts in total does it take?",
     "answer": "It takes 2/2=<<2/2=1>>1 bolt of white fiber\nSo the total amount of fabric is 2+1=<<2+1=3>>3 bolts of fabric\n#### 3"},
    {"question": "Josh decides to try flipping a house.  He buys a house for $80,000 and then puts in $50,000 in repairs.  This increased the value of the house by 150%.  How much profit did he make?",
     "answer": "The cost of the house and repairs came out to 80,000+50,000=$<<80000+50000=130000>>130,000\nHe increased the value of the house by 80,000*1.5=<<80000*1.5=120000>>120,000\nSo the new value of the house is 120,000+80,000=$<<120000+80000=200000>>200,000\nSo he made a profit of 200,000-130,000=$<<200000-130000=70000>>70,000\n#### 70000"},
]

# Hand-checked expectations
EXPECTED = {
    0: {"ann": [("16-3-4", "9"), ("9*2", "18")], "deps": [[], [0]], "answer": "18",
        "merged": "(16 - 3 - 4) * 2"},
    1: {"ann": [("2/2", "1"), ("2+1", "3")], "deps": [[], [0]], "answer": "3",
        "merged": "2 + 2 / 2"},
    # 80000*1.5 uses no earlier result; 120000+80000 uses step 1; 200000-130000 uses steps 2 and 0.
    2: {"ann": [("80000+50000", "130000"), ("80000*1.5", "120000"), ("120000+80000", "200000"),
                ("200000-130000", "70000")],
        "deps": [[], [], [1], [0, 2]], "answer": "70000",
        "merged": "80000 * 1.5 + 80000 - (80000 + 50000)"},
}


def _problem(i):
    return g.check_tiers(g.check_problem(g.parse_row(i, RAW[i])))


@pytest.fixture(scope="module")
def selected():
    if not os.path.exists(DATA):
        pytest.skip("data/gsm8k_test.jsonl not present")
    return g.convert(g.load_rows(DATA), 100)["selected"]


# 1 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("i", [0, 1, 2])
def test_conversion_hand_checked(i):
    p = _problem(i)
    exp = EXPECTED[i]
    assert p.eligible, p.reasons
    assert [(a.expr, a.value) for a in p.annotations] == exp["ann"]
    assert p.deps == exp["deps"]
    assert p.answer == exp["answer"]
    assert p.merged_expr == exp["merged"]
    assert validation._values_equal(validation._safe_arith(p.merged_expr), p.answer,
                                    task_type="discrete", tol=0.0)
    # standard tier: one tool_call + op per annotation, terminal decision on the last op
    task = g.make_task(p, 0)
    log = g.build_log(g.honest_payload("standard", task), task)
    steps, ops = log["reasoning_steps"], log["tool_operations"]
    K = len(exp["ann"])
    assert len(ops) == K and len(steps) == K + 1
    assert [s["depends_on"] for s in steps[:K]] == exp["deps"]
    assert steps[-1]["step_type"] == "decision" and steps[-1]["depends_on"] == [K - 1]
    assert log["final_output"] == {"value": exp["answer"], "derived_from_step": K}
    assert all(o["deterministic"] is True and o["tool_name"] == "calculator" for o in ops)
    assert g.gate_breakdown(log)["decision"] == "accept"


def test_task_private_keys_never_enter_log():
    """Renamed in the audit: reference expressions/sentences DO enter logs by design (the scripted agent
    reproduces the reference derivation); what must never enter is a harness-private task KEY."""
    p = _problem(2)
    task = g.make_task(p, 0)
    log = g.build_log(g.honest_payload("standard", task), task)
    assert set(log["task"]) == {"task_id", "task_type", "dataset", "prompt"}
    # no harness-private KEY anywhere in the log (the substring "reference" legitimately
    # occurs in the recorded model_id "scripted-gsm8k-reference", so test keys, not text)
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert not {"reference", "position", "gsm8k_index", "merged_expr", "deps"} & set(keys(log))
    assert '"merged_expr"' not in json.dumps(log)


# 2 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("i", [0, 1, 2])
@pytest.mark.parametrize("tier", g.EFFORT_TIERS)
def test_every_honest_tier_accepts_hand_checked(i, tier):
    p = _problem(i)
    task = g.make_task(p, 0)
    log = g.build_log(g.honest_payload(tier, task), task)
    ok, errs = agents.validate_log(log)
    assert ok, errs
    d = g.gate_breakdown(log)
    assert d["decision"] == "accept", (tier, d)


def test_every_honest_tier_accepts_all_selected(selected):
    assert len(selected) == 100
    for p in selected:
        for tier, d in g.tier_decisions(p).items():
            assert d["decision"] == "accept", (p.index, tier, d)


def test_tier_sizes_differ(selected):
    p = selected[2]
    task = g.make_task(p, 0)
    K = len(p.annotations)
    sizes = {t: len(g.build_log(g.honest_payload(t, task), task)["reasoning_steps"]) for t in g.EFFORT_TIERS}
    assert sizes == {"minimal": 1, "terse": K, "standard": K + 1, "verbose": 2 * K + 2, "evidence": K + 2}


# 3 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("i", [0, 1, 2])
def test_free_rider_variants_fail_intended_gate(i):
    p = _problem(i)
    task = g.make_task(p, 0)
    ans = p.answer
    bare = g.gate_breakdown(g.build_log(g.free_rider_bare_assertion_wrong("standard", task), task))
    assert bare["decision"] == "reject" and bare["C4"] is False
    assert bare["process_coherence"] and bare["tool_utilization"]          # ONLY causal sufficiency

    bc_log = g.build_log(g.free_rider_bare_assertion_correct("standard", task), task)
    bc = g.gate_breakdown(bc_log)
    assert bc["decision"] == "reject" and bc["C4"] is False                 # BLUEPRINT D3
    assert bc["process_coherence"] and bc["tool_utilization"]
    assert validation._values_equal(bc_log["final_output"]["value"], ans, task_type="discrete", tol=0)
    assert bc_log["tool_operations"] == []

    forged_log = g.build_log(g.free_rider_forged_op("standard", task), task)
    forged = g.gate_breakdown(forged_log)
    assert forged["decision"] == "reject" and forged["C1"] is False
    assert forged["process_coherence"] and forged["tool_utilization"]
    assert validation._values_equal(forged_log["final_output"]["value"], ans, task_type="discrete", tol=0)

    orphan_log = g.build_log(g.free_rider_orphan_op("standard", task), task)
    orphan = g.gate_breakdown(orphan_log)
    assert orphan["decision"] == "reject" and orphan["tool_utilization"] is False
    assert orphan["C1"] is True                                            # the decoy op is REAL
    assert not validation._values_equal(orphan_log["final_output"]["value"], ans,
                                        task_type="discrete", tol=0)


def test_free_rider_variants_fail_intended_gate_all_selected(selected):
    for p in selected:
        task = g.make_task(p, 0)
        for v, gen in g.VARIANT_GENERATORS.items():
            d = g.gate_breakdown(g.build_log(gen("standard", task), task))
            key = {"C4": "C4", "C1": "C1", "tool_utilization": "tool_utilization"}[g.INTENDED_GATE[v]]
            assert d["decision"] == "reject" and d[key] is False, (p.index, v, d)


def test_forged_expression_fallback():
    assert g.forged_expression("12+25", "37") == ("12+24", False)
    expr, fb = g.forged_expression("5*0", "0")            # 5*(-1) is refused (negative literal)
    assert fb is True and expr == "(5*0)-1"


# 4 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("answer_text,reason", [
    ("She has 2 apples.\n#### 2", g.R_NO_ANN),
    ("Half is 6*0.1=<<6*0.1=0.60>>0.60\n#### 0.6", g.R_MISMATCH),          # IEEE-754 artefact
    ("It is 3/4=<<3/4=3/4>>3/4\n#### 1", g.R_MISMATCH),                    # non-numeric recorded value
    ("Sum 10+2=<<10+2=12>>12, then 12+6=18.\n#### 18", g.R_ANSWER_NE_LAST),
    ("A 5*2=<<5*2=10>>10. B 3*3=<<3*3=9>>9. So <<9=9>>9\n#### 9", g.R_INCOMPLETE),
    ("Bad <<5**=5>>5\n#### 5", g.R_NOT_EXECUTABLE),
])
def test_drop_reasons(answer_text, reason):
    p = g.check_tiers(g.check_problem(g.parse_row(9999, {"question": "q", "answer": answer_text})))
    assert not p.eligible and p.primary_reason == reason, (p.reasons, p.details)


def test_thousands_separator_answer():
    p = g.check_tiers(g.check_problem(g.parse_row(0, {"question": "q",
          "answer": "Total 1000+1125=<<1000+1125=2125>>2,125\n#### 2,125"})))
    assert p.eligible and p.answer == "2125"


# 5 -------------------------------------------------------------------------------------------
def test_no_label_value_in_any_generated_log():
    labels = set(g.EFFORT_TIERS) | set(g.VARIANTS) | {"honest", "wrong_block", "free_rider",
                                                      "gsm8k/honest", "gsm8k/wrong_block"}
    for i in range(3):
        task = g.make_task(_problem(i), 0)
        gens = [lambda e, t, tier=tier: g.honest_payload(tier, t) for tier in g.EFFORT_TIERS]
        gens += list(g.VARIANT_GENERATORS.values()) + [g.free_rider_wrong_block]
        for gen in gens:
            log = g.build_log(gen("standard", task), task)
            assert agents.validate_log(log)[0]
            assert not labels.intersection(ws._string_values(log))


def test_model_agnostic_decision_invariant_to_model_id():
    """Seminar comment (a): model_id is provenance only; the predicate never branches on it."""
    task = g.make_task(_problem(2), 0)
    for gen in [lambda e, t: g.honest_payload("standard", t), g.free_rider_forged_op]:
        decisions = {g.gate_breakdown(g.build_log(gen("standard", task), task, model_id=m))["decision"]
                     for m in ("llama3.1:8b", "qwen2.5:7b", "any-other-model", g.SCRIPTED_MODEL_ID)}
        assert len(decisions) == 1


# 6 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("F,T,expected", [(2, 100, [50, 50, 50, 50]), (3, 100, [75, 75, 75, 75]),
                                          (1, 100, [25, 25, 25, 25])])
def test_round_robin_counts(F, T, expected):
    counts = {v: 0 for v in g.VARIANTS}
    for t in range(T):
        for s in range(F):
            counts[g.variant_for(t, s, F)] += 1
    assert [counts[v] for v in g.VARIANTS] == expected


def test_baseline_dissent_rule():
    outs = ["18", "18", 18, "17", "19"]
    plural = metrics.baseline_consensus({"t": outs}, {"t": "discrete"})["t"]["value"]
    flagged = [not metrics._values_equal(o, plural, task_type="discrete", tol=0.0) for o in outs]
    assert plural == 18 and flagged == [False, False, False, True, True]


def test_intervals_closed_forms():
    lo, hi = sc.clopper_pearson(9, 9)
    assert abs(lo - 0.025 ** (1 / 9)) < 1e-9 and hi == 1.0
    lo, hi = sc.clopper_pearson(0, 2700)
    assert lo == 0.0 and abs(hi - (1 - 0.025 ** (1 / 2700))) < 1e-9
    lo, hi = sc.wilson(9, 9)
    assert abs(lo - 0.7009) < 1e-4 and hi == 1.0
    lo, hi = sc.wilson(100, 100)
    assert abs(lo - 0.9630) < 1e-4


def test_intervals_match_scipy_if_available():
    stats = pytest.importorskip("scipy.stats")
    for x, n in [(0, 10), (3, 10), (67, 67), (5, 2000), (1799, 1800)]:
        lo = 0.0 if x == 0 else stats.beta.ppf(0.025, x, n - x + 1)
        hi = 1.0 if x == n else stats.beta.ppf(0.975, x + 1, n - x)
        clo, chi = sc.clopper_pearson(x, n)
        assert abs(clo - lo) < 1e-7 and abs(chi - hi) < 1e-7
        w = stats.binomtest(x, n).proportion_ci(method="wilson")
        wlo, whi = sc.wilson(x, n)
        assert abs(wlo - w.low) < 1e-9 and abs(whi - w.high) < 1e-9


# 7 -- decisions of 23 Sep: bare-correct variant, H1b divergence, McNemar, code-level confirmations
def test_four_variants_distinct_per_task_at_three_free_riders():
    for t in range(100):
        vs = [g.variant_for(t, s, 3) for s in range(3)]
        assert len(set(vs)) == 3


def test_divergent_reference_all_selected(selected):
    """BLUEPRINT D2: every selected task gets a validly derived DIFFERENT output; all tiers accept it."""
    for i, p in enumerate(selected):
        task = g.attach_divergence(g.make_task(p, i))
        d = task["divergent_reference"]
        assert d is not None, p.index
        assert not validation._values_equal(d["annotations"][-1]["value"], p.answer, task_type="discrete", tol=0)
        assert all(set(nd) <= set(od) for nd, od in zip(d["deps"], task["reference"]["deps"]))
        dt = g.divergent_task(task)
        for tier in g.EFFORT_TIERS:
            log = g.build_log(g.honest_payload(tier, dt), dt)
            assert g.gate_breakdown(log)["decision"] == "accept", (p.index, tier)
            assert validation._values_equal(log["final_output"]["value"], d["annotations"][-1]["value"],
                                            task_type="discrete", tol=0)
            assert "divergent_reference" not in json.dumps(log) and "meta" not in log["task"]


def test_divergent_hand_checked_task0():
    """Task 0: (16-3-4)*2 = 18; operand 2 (in the question) misread as 3 -> 27, recomputed."""
    task = g.attach_divergence(g.make_task(_problem(0), 0))
    d = task["divergent_reference"]
    assert d["meta"]["operand"] == "2" and d["meta"]["new_operand"] == "3"
    assert d["annotations"][-1]["value"] == "27" and d["merged_expr"] == "(16 - 3 - 4) * 3"



def test_divergent_nonnegativity_preference_task12(selected):
    """Audit O4: task 12's unconstrained pick (3 -> 13, answer -35) is rejected for the next candidate."""
    assert selected[12].index == 12
    task = g.attach_divergence(g.make_task(selected[12], 12))
    m = task["divergent_reference"]["meta"]
    assert m["nonneg_changed_selection"] and m["nonneg_rejected_answer"] == "-35"
    assert m["operand"] == "7" and m["new_operand"] == "8" and m["divergent_answer"] == "11"
    assert not m["nonneg_fallback_negative_kept"]


def test_divergent_answers_nonnegative_all_selected(selected):
    """Audit O4: no selected task keeps a negative divergent answer."""
    for i, p in enumerate(selected):
        d = g.attach_divergence(g.make_task(p, i))["divergent_reference"]
        assert float(d["annotations"][-1]["value"]) >= 0, p.index
        assert not d["meta"]["nonneg_fallback_negative_kept"], p.index

def test_mcnemar_exact_known_values():
    assert sc.mcnemar_exact(0, 0)["p_value"] == 1.0
    assert sc.mcnemar_exact(5, 0)["p_value"] == pytest.approx(0.0625)
    assert sc.mcnemar_exact(3, 1)["p_value"] == pytest.approx(0.625)
    assert sc.mcnemar_exact(10, 10)["p_value"] == 1.0
    assert sc.mcnemar_exact(75, 0)["p_fmt"] == "< 0.001"
    stats = pytest.importorskip("scipy.stats")
    for b, c in [(7, 2), (0, 12), (30, 21)]:
        ref = stats.binomtest(min(b, c), b + c, 0.5).pvalue
        assert sc.mcnemar_exact(b, c)["p_value"] == pytest.approx(ref, rel=1e-12)


def _identifier_sites(path, name):
    """Every AST site where `name` occurs as an identifier, attribute, keyword or string constant,
    with the enclosing top-level function (None = module level)."""
    import ast
    tree = ast.parse(open(os.path.join(SRC, path), encoding="utf-8").read())
    sites = []
    for top in tree.body:
        owner = top.name if isinstance(top, (ast.FunctionDef, ast.ClassDef)) else None
        for n in ast.walk(top):
            hit = ((isinstance(n, ast.Name) and n.id == name) or (isinstance(n, ast.Attribute) and n.attr == name)
                   or (isinstance(n, ast.keyword) and n.arg == name)
                   or (isinstance(n, ast.Constant) and n.value == name))
            if hit:
                sites.append(owner)
    return sites


def test_validation_and_consensus_never_branch_on_model_id():
    """[VERIFY] (a): validation.py never references model_id; consensus.py only inside build_h1 (the
    legacy demo population builder, which constructs AgentConfig objects), never in the round."""
    assert _identifier_sites("validation.py", "model_id") == []
    assert set(_identifier_sites("consensus.py", "model_id")) <= {"build_h1"}


def test_h1b_assignment_counts_and_seed_independence():
    import experiments as E
    tasks = [{"task_id": f"t{i}"} for i in range(100)]
    for n in (10, 20, 30):
        a = E.h1b_assignment(n, tasks)
        assert all(len(v) == E.n_divergent(n) and all(0 <= x < n for x in v) for v in a.values())
        assert a == E.h1b_assignment(n, tasks)                   # deterministic
    assert len({frozenset(v) for v in E.h1b_assignment(30, tasks).values()}) > 50    # varies across tasks


def test_negative_literal_is_wired():
    """Audit fix D8: GSM8K test index 489 ('-48+21+(-3)=-30', '-30/3=-10') is a complete derivation;
    the negative earlier result must be wired to its consumer, and every honest tier accepted."""
    anns = [g.Annotation("-48+21+(-3)", "-30", ""), g.Annotation("-30/3", "-10", "")]
    assert g.wire_dependencies(anns) == [[], [0]]
    assert g.numeric_constants("-30/3") == [-30, 3]
    assert validation._safe_arith(g.merged_expression(anns, [[], [0]])) == -10
