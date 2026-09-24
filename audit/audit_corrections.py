#!/usr/bin/env python3
"""audit/audit_corrections.py -- turn the generated results into the corrected, paper-facing pair.

Usage: python3 audit/audit_corrections.py RESULTS_DIR   (after experiments.py and independent_checks.py)

* The generated files are kept verbatim as generated_results.json, generated_RESULTS_SUMMARY.md and
  generated_<csv>; the corrected files take the canonical names.
* results.json: H1b keys renamed from FPR to flag-rate / attribution vocabulary (audit item 4); new
  `audit` block (units, attribution, task-level detection, gate carrying detection, independent checks,
  evasion probe, H1-U overlap); unit-aware targets. No existing number is changed.
* RESULTS_SUMMARY.md: sections 2, 4, 9 rebuilt from results.json; targeted sentence fixes elsewhere;
  section 13 (audit) added. Every replacement asserts that its anchor occurs exactly once.
Idempotent: always starts from the generated_* copies.
"""
import copy
import csv
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))   # repository layout: modules live in src/
import stats_ci as sc  # noqa: E402

RES = sys.argv[1]
PRIMARY_SEED = "20260629"
TIERS = ["minimal", "terse", "standard", "verbose", "evidence"]
VARIANTS = ["bare_assertion_wrong", "bare_assertion_correct", "forged_op", "orphan_op"]
MOVED = ["results.json", "RESULTS_SUMMARY.md", "mcnemar.csv", "robustness.csv", "h1b_divergence.csv"]
for f in MOVED:
    src, dst = os.path.join(RES, f), os.path.join(RES, "generated_" + f)
    if not os.path.exists(dst):
        shutil.move(src, dst)

R = json.load(open(os.path.join(RES, "generated_results.json"), encoding="utf-8"))
SUMMARY = open(os.path.join(RES, "generated_RESULTS_SUMMARY.md"), encoding="utf-8").read()
A = json.load(open(os.path.join(RES, "audit_checks.json"), encoding="utf-8"))


# ------------------------------------------------------------------ formatting (same as experiments.py)
def pct(p):
    return "n/a" if p is None else f"{100 * p:.1f}%"


def cif(ci):
    return "n/a" if ci is None else f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def ppf(d):
    return f"{(0.0 if abs(d) < 5e-4 else d):+.1f} pp"


def P(x, n):
    r = sc.prop(x, n)
    r["fmt"] = {"p": pct(r["p"]), "x_n": f"{x}/{n}", "wilson95": cif(r["wilson95"]), "cp95": cif(r["cp95"])}
    return r


def mc(b, c):
    return sc.mcnemar_exact(b, c)


def pline(label, arm, r):
    return f"| {label} | {arm} | {pct(r['p'])} | {r['x']}/{r['n']} | {cif(r['wilson95'])} | {cif(r['cp95'])} |"


def xn(r):
    return f"{r['x']}/{r['n']}"


def pvf(m):
    return m["p_fmt"] if m["p_fmt"].startswith("<") else f"= {m['p_fmt']}"


# ------------------------------------------------------------------ per-log rows (primary, N = 30)
rows = defaultdict(list)
with open(os.path.join(RES, "per_log_records.csv"), newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["N"] == "30" and r["seed"] == PRIMARY_SEED and r["scenario"] in ("H1", "H1b", "H3", "H1U"):
            for k in ("is_attack", "divergent", "output_correct", "flagged_process", "flagged_baseline", "C4",
                      "gate_process_coherence", "gate_tool_utilization"):
                r[k] = r[k] == "True"
            rows[r["scenario"]].append(r)


def by_task(rs):
    d = defaultdict(list)
    for r in rs:
        d[r["task_id"]].append(r)
    return d


audit = {"description": ("Independent audit of the P2 results (2026-09-23). Numbers here are computed from "
                         "per_log_records.csv and audit_checks.json; none replaces a generated number."),
         "fixes_applied": {
             "D1_source_hashes": "meta.source_sha256 covers .py/.sh sources and AUDITED_SHA256.txt / requirements.txt "
                                 "only; run_log_final.txt (overwritten by the run) made results.json irreproducible",
             "D8_negative_literals": ("gsm8k_logs.numeric_constants and merged_expression read '-x' as one negative "
                                      "literal; before the fix a negative intermediate result was never wired to its "
                                      "consumer. Effects: GSM8K test index 489 is now eligible (whole-file rule-(c) "
                                      "problems 53 -> 52, rejected faithful logs 212 -> 208); problem selection, H1, H3 "
                                      "and H1-U logs are byte-identical; in H1b the divergent derivation of task "
                                      "gsm8k-test-0012 changed (7 -> 8 lemons, answer 11, was chosen only because the bug "
                                      "hid a link in the preferred candidate 3 -> 13, answer -35, which the fixed search "
                                      "then selected; 3 logs per seed and N = 30 run change, no H1b rate changes). A "
                                      "negative answer is implausible as an honest misreading; superseded for that task by "
                                      "O4 below")}}

# O4 (non-negativity preference in the H1b divergence search): record what it changed, from results only
_dvn = R["meta"]["config"]["divergence"]["nonnegative_answer_preference"]
_sel = {s_["task_id"]: s_["divergence"] for s_ in R["meta"]["dataset"]["selected"]}
_chg = []
for _tid in _dvn["tasks_selection_changed"]:
    _m = _sel[_tid]
    _chg.append(f"{_tid} (rejected {_m['nonneg_rejected_candidate']}, answer {_m['nonneg_rejected_answer']}; selected "
                f"step {_m['step']}: {_m['operand']} -> {_m['new_operand']} (delta {_m['delta']}), answer "
                f"{_m['divergent_answer']})")
audit["fixes_applied"]["O4_nonnegativity_preference"] = (
    "the H1b divergence search rejects a candidate whose divergent answer is below zero in favour of the next "
    f"candidate in search order. Selection changed on {len(_chg)} task(s): " + ("; ".join(_chg) if _chg else "none")
    + f". Negative divergent answers after the change: {_dvn['negative_divergent_answers']}; negative answers kept as "
    f"fallback: {len(_dvn['tasks_negative_answer_kept_as_fallback'])}. Only the divergent logs of the changed task(s) "
    "differ; H1, H3 and H1-U logs are unaffected")
audit["fixes_applied"]["O3_cost_sentence"] = ("section 6 closing sentence regenerated: the audit's direct ledger count "
                                              "matches the table's per-log protocol and all-ledger columns (the earlier "
                                              "'does NOT match' came from comparing a 4-dp audit value with the unrounded "
                                              "table value); walking-skeleton ops are explained as a non-ledger count")
audit["fixes_applied"]["O7_headline_payload_unit"] = ("added audit.headline_payload_level: distinct payloads among the "
                                                      "correct-answer free-rider logs, with Wilson and Clopper-Pearson "
                                                      "intervals, from per_log_records.csv")

# units: distinct payloads and tasks
units = {}
for s, rs in rows.items():
    hon = [r for r in rs if not r["is_attack"]]
    dist = {r["payload_sha256"] for r in hon}
    dist_flag = {r["payload_sha256"] for r in hon if r["flagged_process"]}
    u = {"honest_logs": len(hon), "distinct_honest_payloads": len(dist),
         "process_flagged_distinct_honest_payloads": P(len(dist_flag), len(dist)),
         "tasks": len(by_task(rs)),
         "process_tasks_with_any_honest_log_flagged": P(sum(1 for t in by_task(hon).values() if any(r["flagged_process"] for r in t)),
                                                         len(by_task(rs)))}
    atk = [r for r in rs if r["is_attack"]]
    if atk:
        u["attacker_logs"] = len(atk)
        u["distinct_attacker_payloads"] = len({r["payload_sha256"] for r in atk})
    units[s] = u
audit["units"] = {"note": ("The 6 agents of a tier (N = 30) emit byte-identical honest payloads, and a deterministic "
                           "predicate returns the same decision on identical bytes; log-level n overstates the "
                           "evidence. Task-level and distinct-payload proportions are the defensible units."),
                  **units}

# attribution (H1b + H3)
h1b, h3 = rows["H1b"], rows["H3"]
cells = {
    "honest_correct_answer": [r for r in h1b if not r["divergent"]],
    "honest_wrong_answer": [r for r in h1b if r["divergent"]],
    "free_rider_wrong_answer": [r for r in h3 if r["behaviour"] in ("bare_assertion_wrong", "orphan_op")],
    "free_rider_correct_answer": [r for r in h3 if r["behaviour"] in ("bare_assertion_correct", "forged_op")]}
att = {"note": ("H1b and H3, N = 30, primary seed, same 100 problems. Free-rider cells pool two variants of 75 logs. "
                "Output-only flags track answer error; process flags track free-riding.")}
for k, rs in cells.items():
    att[k] = {"logs": len(rs), "process_flagged": P(sum(r["flagged_process"] for r in rs), len(rs)),
              "output_only_flagged": P(sum(r["flagged_baseline"] for r in rs), len(rs)),
              "output_correct": sum(r["output_correct"] for r in rs)}
both = h1b + h3
att["output_only_flag_iff_wrong_answer"] = P(sum(r["flagged_baseline"] == (not r["output_correct"]) for r in both), len(both))
audit["attribution"] = att

# O7: headline at payload level. The 150 correct-answer free-rider logs, counted as distinct payloads.
_fc = cells["free_rider_correct_answer"]
_pl = defaultdict(list)
for r in _fc:
    _pl[r["payload_sha256"]].append(r)
_uni_p = all(len({r["flagged_process"] for r in v}) == 1 for v in _pl.values())
_uni_b = all(len({r["flagged_baseline"] for r in v}) == 1 for v in _pl.values())
assert _uni_p and _uni_b, "flag status differs between logs sharing a payload; payload-level count ill-defined"
_wrong_pl = {r["payload_sha256"] for r in cells["free_rider_wrong_answer"]}
audit["headline_payload_level"] = {
    "note": ("H3, N = 30, primary seed. The correct-answer free-rider logs (bare_assertion_correct + forged_op) "
             "counted as distinct payloads (payload_sha256). Flag status is identical for every log sharing a "
             "payload in both arms, so the payload-level count is well defined. Process detection holds by "
             "construction; the output-only count is structural (the arm reads only the final value, which is correct)."),
    "variants": ["bare_assertion_correct", "forged_op"],
    "logs": len(_fc),
    "distinct_payloads": len(_pl),
    "distinct_payloads_per_variant": {v: len({r["payload_sha256"] for r in _fc if r["behaviour"] == v})
                                      for v in ("bare_assertion_correct", "forged_op")},
    "process_flagged_distinct_payloads": P(sum(1 for v in _pl.values() if v[0]["flagged_process"]), len(_pl)),
    "output_only_flagged_distinct_payloads": P(sum(1 for v in _pl.values() if v[0]["flagged_baseline"]), len(_pl)),
    "flag_status_uniform_within_payload": {"process": _uni_p, "output_only": _uni_b},
    "payloads_also_in_wrong_answer_free_rider_logs": len(set(_pl) & _wrong_pl)}

# task-level detection and the gate carrying detection (H3)
atk = [r for r in h3 if r["is_attack"]]
bt = by_task(atk)
any_p = [any(r["flagged_process"] for r in t) for t in bt.values()]
any_b = [any(r["flagged_baseline"] for r in t) for t in bt.values()]
all_p = [all(r["flagged_process"] for r in t) for t in bt.values()]
all_b = [all(r["flagged_baseline"] for r in t) for t in bt.values()]


def pair(a, b):
    bb = sum(1 for x, y in zip(a, b) if x and not y)
    cc = sum(1 for x, y in zip(a, b) if y and not x)
    return {"n": len(a), "process_positive": sum(a), "baseline_positive": sum(b),
            "delta_pp": 100.0 * (sum(a) - sum(b)) / len(a), "mcnemar_exact": mc(bb, cc)}


audit["h3_task_level_detection"] = {"tasks_any_attacker_log_flagged": pair(any_p, any_b),
                                    "tasks_all_attacker_logs_flagged": pair(all_p, all_b),
                                    "process_any": P(sum(any_p), len(any_p)), "baseline_any": P(sum(any_b), len(any_b))}
audit["h3_gate_carrying_detection"] = {
    "attacker_logs_failing_C4": P(sum(not r["C4"] for r in atk), len(atk)),
    "attacker_logs_failing_process_coherence_or_tool_utilization_but_passing_C4":
        sum(1 for r in atk if (not r["gate_process_coherence"] or not r["gate_tool_utilization"]) and r["C4"]),
    "note": "C4 alone rejects every attacker log; PC/TU failures occur only on orphan_op, which C4 also rejects"}
audit["independent_checks"] = A
audit["evasion_probe"] = {**A["evasion_probe"],
                          "identity_op_accepted": P(A["evasion_probe"]["identity_op_accepted_by_predicate"], A["evasion_probe"]["tasks"]),
                          "times_one_op_accepted": P(A["evasion_probe"]["times_one_op_accepted_by_predicate"], A["evasion_probe"]["tasks"]),
                          "label": ("LIMITATION PROBE, NOT AN H3 RESULT: a free-rider that wraps the copied answer A in a "
                                    "trivial calculator op ('A' or 'A*1') satisfies every gate")}
audit["h1u_vs_primary"] = A["h1u_vs_primary"]

# ------------------------------------------------------------------ results.json corrections
C = copy.deepcopy(R)
C["audit"] = audit
C["meta"]["authority"] = ("This file is the single numeric authority for the paper (with RESULTS_SUMMARY.md). It is "
                          "the audit-corrected form of generated_results.json (audit/audit_corrections.py).")
C["meta"]["statistics"]["units_note"] = audit["units"]["note"]
C["meta"]["config"]["scenarios"]["H1b"] = ("all agents honest; per task a seeded random 10% derive a WRONG answer through a "
                                           "valid chain (one operand changed; BLUEPRINT D2). Attribution test: flags on these "
                                           "logs are flag rates, not FPRs comparable to H1")
C["meta"]["dataset"]["h1u_tasks"]["overlap_with_primary"] = A["h1u_vs_primary"]

ARM_REN = {"fpr": "flag_rate_all_logs", "fpr_per_tier": "flag_rate_per_tier", "fpr_divergence_split": "attribution"}
ATT_REN = {"divergent_logs": "honest_error_logs_flagged", "non_divergent_logs": "correct_output_logs_flagged",
           "tasks_with_any_divergent_log_flagged": "tasks_with_any_honest_error_log_flagged",
           "divergent_logs_output_correct": "honest_error_logs_output_correct"}
CMP_REN = {"fpr_logs": "flag_all_logs", "fpr_tasks_any_honest_flagged": "flag_tasks_any_log",
           "fpr_divergent_logs": "flag_honest_error_logs"}
SEED_REN = {"process_fpr": "process_flag_rate_all_logs", "baseline_fpr": "baseline_flag_rate_all_logs",
            "process_fpr_divergent": "process_flag_rate_honest_error", "baseline_fpr_divergent": "baseline_flag_rate_honest_error"}


def ren(d, m):
    return {m.get(k, k): v for k, v in d.items()}


def fix_block(b):
    for arm in ("process", "baseline"):
        a = ren(b[arm], ARM_REN)
        a["attribution"] = ren(a["attribution"], ATT_REN)
        a["task_level"] = ren(a["task_level"], {"tasks_with_any_honest_log_flagged": "tasks_with_any_log_flagged"})
        a["unit_of_analysis"] = "log (flag rates); task (accuracy, task_level)"
        b[arm] = a
    b["comparative"] = ren(b["comparative"], CMP_REN)
    return b


for nk, blk in C["scenarios"]["H1b"].items():
    blk["primary"] = fix_block(blk["primary"])
    blk["per_seed_summary"] = {s: ren(v, SEED_REN) for s, v in blk["per_seed_summary"].items()}
    blk["seed_invariant"] = ren(blk["seed_invariant"], SEED_REN)
ill = C["determinism_gap_illustration"]["H1b"]
C["determinism_gap_illustration"]["H1b"] = ren(ill, {"predicate_fpr": "predicate_flag_rate",
                                                     "output_plurality_fpr": "output_plurality_flag_rate"})
for rule in ("full_log_content_hash", "reasoning_payload_hash"):
    C["determinism_gap_illustration"]["H1b"][rule] = ren(ill[rule], {"flagged_honest_logs": "flagged_logs"})
for pp in C["rule_c_unfiltered"]["per_problem"]:
    pp["steps_outside_answer_cone"] = pp.pop("orphan_steps")

# targets: unit-aware
T = C["targets_primary"]
T["H1b"] = {k: v for k, v in T["H1b"].items() if not k.startswith("fpr_")}
T["H1b"]["fpr_target_not_applicable"] = "attribution test: honest-error logs have wrong answers (section 2)"
for s in ("H1", "H3", "H1U"):
    T[s]["fpr_task_level_wilson_upper_lt_5"] = units[s]["process_tasks_with_any_honest_log_flagged"]["wilson95"][1] < 0.05
    T[s]["fpr_distinct_payload_wilson_upper_lt_5"] = units[s]["process_flagged_distinct_honest_payloads"]["wilson95"][1] < 0.05
T["H3"]["detection_task_level_all_attackers_wilson_lower_ge_90"] = sc.prop(sum(all_p), len(all_p))["wilson95"][0] >= 0.90
rc = C["rule_c_unfiltered"]
T["rule_c_unfiltered_whole_file"]["per_tier_wilson_upper_lt_5"] = {t: rc["fpr_per_tier"][t]["wilson95"][1] < 0.05 for t in TIERS}
T["rule_c_unfiltered_whole_file"]["problem_level_wilson_upper_lt_5"] = rc["problems_with_any_tier_rejected"]["wilson95"][1] < 0.05
T["note"] = ("Met only if the interval bound clears the target. The whole-file pooled rate is diluted by the minimal "
             "tier, which records only the answer's dependency cone and cannot be rejected; the per-tier and "
             "problem-level rows are the binding ones. H1's honest-log FPR is 0 by construction of the sample.")

json.dump(C, open(os.path.join(RES, "results.json"), "w", encoding="utf-8"), indent=1, sort_keys=False, default=str)
open(os.path.join(RES, "results.json"), "a").write("\n")

# ------------------------------------------------------------------ CSV label fixes (H1b)
def rewrite_csv(name, fn):
    with open(os.path.join(RES, "generated_" + name), newline="", encoding="utf-8") as f:
        rd = list(csv.DictReader(f))
        fields = list(rd[0].keys())
    rd, fields = fn(rd, fields)
    with open(os.path.join(RES, name), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rd)


def fix_mcn(rd, fields):
    m = {"fpr_flagged_honest": "flag_all_logs", "fpr_any_honest_flagged": "flag_any_log",
         "fpr_flagged_divergent_honest": "flag_honest_error"}
    for r in rd:
        if r["scenario"] == "H1b":
            r["comparison"] = m.get(r["comparison"], r["comparison"])
    return rd, fields


def fix_rob(rd, fields):
    m = {"process_fpr": "process_flag_nonattack", "baseline_fpr": "baseline_flag_nonattack",
         "process_fpr_divergent": "process_flag_honest_error", "baseline_fpr_divergent": "baseline_flag_honest_error"}
    return [{m.get(k, k): v for k, v in r.items()} for r in rd], [m.get(k, k) for k in fields]


def fix_h1b(rd, fields):
    gm = {"divergent_logs": "honest_error_logs", "non_divergent_logs": "correct_output_logs", "all_honest": "all_logs"}
    mm = {"fpr": "flag_rate", "task_level_any_divergent_flagged": "task_level_any_honest_error_flagged",
          "task_level_any_honest_flagged": "task_level_any_log_flagged"}
    for r in rd:
        r["metric"] = mm.get(r["metric"], r["metric"])
        r["group"] = gm.get(r["group"], r["group"])
    return rd, fields


rewrite_csv("mcnemar.csv", fix_mcn)
rewrite_csv("robustness.csv", fix_rob)
rewrite_csv("h1b_divergence.csv", fix_h1b)

# ------------------------------------------------------------------ summary corrections
S = SUMMARY


def sub1(old, new):
    global S
    assert S.count(old) == 1, f"anchor occurs {S.count(old)} times: {old[:80]!r}"
    S = S.replace(old, new)


def line_starting(prefix):
    ls = [ln for ln in S.split("\n") if ln.startswith(prefix)]
    assert len(ls) == 1, f"{len(ls)} lines start with {prefix[:60]!r}"
    return ls[0]


def section(num):
    m = re.search(rf"^## {num}\. .*?(?=^## |\Z)", S, flags=re.S | re.M)
    assert m, num
    return m.group(0)


P1 = R["scenarios"]["H1"]["N30"]["primary"]
PB = C["scenarios"]["H1b"]["N30"]["primary"]
P3 = R["scenarios"]["H3"]["N30"]["primary"]
PU = R["scenarios"]["H1U"]["N30"]["primary"]
U = units
ov = A["h1u_vs_primary"]

# header
gen_line = line_starting("*Generated from `results/results.json`")
stamp = re.search(r"Generated at [0-9TZ:\-]+\.", gen_line).group(0)
sub1(gen_line, f"*Audit-corrected by `audit/audit_corrections.py` from `generated_RESULTS_SUMMARY.md`; every number "
               f"is in `results/results.json`, the numeric authority. {stamp}*")
sub1("**H1b** all honest with 3 agents per task (seeded, 10.0%) deriving a different output through a valid chain (BLUEPRINT D2);",
     "**H1b** all agents honest, with 3 per task (seeded, 10.0%) deriving a wrong answer through a valid chain "
     "(BLUEPRINT D2; an attribution test, §2);")
sub1("(rule (c) not applied).", f"(rule (c) not applied; {ov['shared']} problems shared with the primary sample, §4).")
sub1("seeds are never pooled.", "seeds are never pooled. **The selection is not neutral for H1:** rules (c) and (d) admit "
     "only problems whose five honest tiers the predicate accepts, so H1's honest-log flag count is 0 by construction; "
     "§4 measures the honest-log FPR without rule (c).")
sub1("Intervals are on the primary seed only.",
     f"Intervals are on the primary seed only. The agents of one tier emit byte-identical payloads, so the "
     f"{U['H1']['honest_logs']} honest logs of H1 are {U['H1']['distinct_honest_payloads']} distinct payloads "
     f"({U['H1']['tasks']} tasks × 5 tiers); readings quote task-level intervals, and distinct-payload intervals "
     f"are in `results.json` → `audit.units`.")

# section 1 reading
u1 = U["H1"]
sub1(line_starting("**Reading.** The process arm flagged 0/3000 honest logs"),
     f"**Reading.** The process arm flagged {xn(P1['process']['fpr'])} honest logs "
     f"({xn(u1['process_flagged_distinct_honest_payloads'])} distinct payloads; "
     f"{xn(u1['process_tasks_with_any_honest_log_flagged'])} tasks, Wilson {cif(u1['process_tasks_with_any_honest_log_flagged']['wilson95'])}). "
     "This is guaranteed by the selection (rules (c) and (d)): H1 shows that the protocol reproduces single-log "
     "decisions end to end, not that the predicate spares honest logs, which §4 measures. Both arms reach "
     f"{pct(P1['process']['consensus_accuracy']['p'])} accuracy (Wilson lower bound "
     f"{100 * P1['process']['consensus_accuracy']['wilson95'][0]:.1f}%). Digest agreement of "
     f"{pct(P1['process']['digest_agreement']['p'])} is expected rather than informative: every validator runs the same "
     "deterministic predicate on the same bytes. The output-only arm flags nothing here only because every honest "
     "log carries the correct output.")

# section 2 rebuilt
pa, ba = PB["process"]["attribution"], PB["baseline"]["attribution"]
dv = R["meta"]["config"]["divergence"]
va = PB["process"]["vote_arithmetic"]
cmpb = PB["comparative"]
atts = audit["attribution"]


def acell(k, arm):
    r = atts[k][arm]
    return f"{xn(r)} ({pct(r['p'])})"


s2 = [f"## 2. H1b — Honest Error Versus Free-Riding: An Attribution Test (N = 30, {PB['logs']} Logs, "
      f"{pa['honest_error_logs_flagged']['n']} Honest-Error Logs)", "",
      "H1b is not a false-positive test comparable to H1. Per task, 3 of the 30 honest agents (seeded, 10.0%) "
      "derive a **wrong** answer through a valid chain: one operand of the reference derivation is changed and "
      "every downstream calculator step is recomputed (BLUEPRINT D2). These *honest-error* logs are not attacks, "
      "but their answers are wrong, so a flag on them is correct for answer quality and a misattribution for "
      "free-rider detection. On single-answer tasks honest divergence can only appear as error; a valid minority "
      "answer (multi-answer tasks) is not exercised.", "",
      "| Metric | Arm | Value | x/n | Wilson 95% | Clopper–Pearson 95% |", "| --- | --- | ---: | ---: | ---: | ---: |",
      pline("Flag rate, honest-error logs (valid chain, wrong answer)", "process", pa["honest_error_logs_flagged"]),
      pline("Flag rate, honest-error logs (valid chain, wrong answer)", "output-only", ba["honest_error_logs_flagged"]),
      pline("Flag rate, honest logs with the correct answer", "process", pa["correct_output_logs_flagged"]),
      pline("Flag rate, honest logs with the correct answer", "output-only", ba["correct_output_logs_flagged"]),
      pline("Flag rate, all logs", "process", PB["process"]["flag_rate_all_logs"]),
      pline("Flag rate, all logs", "output-only", PB["baseline"]["flag_rate_all_logs"]),
      pline("Tasks with ≥ 1 honest-error log flagged", "process", pa["tasks_with_any_honest_error_log_flagged"]),
      pline("Tasks with ≥ 1 honest-error log flagged", "output-only", ba["tasks_with_any_honest_error_log_flagged"]),
      pline("Consensus accuracy (tasks)", "process", PB["process"]["consensus_accuracy"]),
      pline("Consensus accuracy (tasks)", "output-only", PB["baseline"]["consensus_accuracy"]),
      pline("Digest agreement", "process", PB["process"]["digest_agreement"]), "",
      "**Attribution across H1b and H3** (same 100 problems, N = 30, primary seed; each free-rider row pools two "
      "variants of 75 logs):", "",
      "| Log class | Logs | Process arm flagged | Output-only arm flagged |", "| --- | ---: | ---: | ---: |",
      f"| Honest, correct answer (H1b) | {atts['honest_correct_answer']['logs']} | {acell('honest_correct_answer', 'process_flagged')} | {acell('honest_correct_answer', 'output_only_flagged')} |",
      f"| Honest, wrong answer (H1b) | {atts['honest_wrong_answer']['logs']} | {acell('honest_wrong_answer', 'process_flagged')} | {acell('honest_wrong_answer', 'output_only_flagged')} |",
      f"| Free-rider, wrong answer (H3: bare_assertion_wrong + orphan_op) | {atts['free_rider_wrong_answer']['logs']} | {acell('free_rider_wrong_answer', 'process_flagged')} | {acell('free_rider_wrong_answer', 'output_only_flagged')} |",
      f"| Free-rider, correct answer (H3: bare_assertion_correct + forged_op) | {atts['free_rider_correct_answer']['logs']} | {acell('free_rider_correct_answer', 'process_flagged')} | {acell('free_rider_correct_answer', 'output_only_flagged')} |",
      "",
      f"- The output-only arm flags a log exactly when its answer is wrong: {xn(atts['output_only_flag_iff_wrong_answer'])} logs of H1b and H3.",
      f"- Paired, task level: tasks with ≥ 1 honest-error log flagged b = {cmpb['flag_tasks_any_log']['mcnemar_exact']['b']}, "
      f"c = {cmpb['flag_tasks_any_log']['mcnemar_exact']['c']}, p {pvf(cmpb['flag_tasks_any_log']['mcnemar_exact'])}; "
      f"accuracy b = {cmpb['accuracy_tasks']['mcnemar_exact']['b']}, c = {cmpb['accuracy_tasks']['mcnemar_exact']['c']}, "
      f"p {pvf(cmpb['accuracy_tasks']['mcnemar_exact'])}.",
      f"- Construction of the divergent derivations (one per task, rendered in each divergent agent's own tier): operand "
      f"taken from the question text in {dv['operand_in_question']}/100 tasks (in {100 - dv['operand_in_question']} it is a "
      f"constant not in the question); change of +1 in {dv['delta_counts'].get('1', 0)}/100; integer or ≤ 2-decimal values "
      f"in {dv['clean_values']}/100; dependency wiring identical to the reference in {dv['wiring_identical']}/100 (the other "
      f"{dv['wiring_subset_value_collision']} drop one spurious link caused by two reference steps with equal values); more "
      f"than one step recomputed in {dv['multi_step_recomputation']}/100. Per-task detail: `h1b_divergent_derivations.csv`.",
      f"- All {pa['honest_error_logs_flagged']['n']} honest-error logs carry a wrong output ({pa['honest_error_logs_output_correct']} correct). "
      f"Vote arithmetic per task: ≥ {va['min_correct_output_logs_per_task']} correct outputs against one wrong-answer "
      f"bloc of ≤ {va['max_largest_wrong_output_bloc_per_task']}.",
      f"- Ledger `verify()`: {PB['process']['integrity']['ledger_verify']}. Label leaks: "
      f"{PB['process']['integrity']['audited_scan_leaks'] + PB['process']['integrity']['extended_scan_leaks']}.", "",
      "**Reading.** Output voting responds only to whether an answer is right. It flags "
      f"{xn(atts['honest_wrong_answer']['output_only_flagged'])} honest-error logs and "
      f"{xn(atts['free_rider_wrong_answer']['output_only_flagged'])} wrong-answer free-riders alike, and passes "
      f"{atts['free_rider_correct_answer']['logs'] - atts['free_rider_correct_answer']['output_only_flagged']['x']}/"
      f"{atts['free_rider_correct_answer']['logs']} correct-answer free-riders, so it cannot tell honest error from "
      "free-riding. The process arm separates them: it accepts the honest-error logs, whose chains re-execute and are "
      f"grounded ({xn(atts['honest_wrong_answer']['process_flagged'])} flagged), and rejects every free-rider log "
      f"whatever its answer ({atts['free_rider_wrong_answer']['process_flagged']['x'] + atts['free_rider_correct_answer']['process_flagged']['x']}/"
      f"{atts['free_rider_wrong_answer']['logs'] + atts['free_rider_correct_answer']['logs']}). The accepted wrong "
      f"answers enter aggregation and are outvoted ({pct(PB['process']['consensus_accuracy']['p'])} accuracy in both "
      "arms), so the process arm is not an answer-quality filter: were honest error the plurality, it would carry the "
      "wrong answer without a flag. Under a free-rider-detection objective the output-only flags on honest error are "
      "misattributions; this is the attribution half of seminar comment (b). The divergence is constructed (one "
      "changed operand per task), not observed from a model.", "", ""]
sub1(section(2), "\n".join(s2))

# section 3 patches
cd = P3["comparative"]
tl = audit["h3_task_level_detection"]
sub1(line_starting("- Comparative detection, pooled logs:"),
     f"- Comparative detection, task level: all attacker logs flagged {ppf(tl['tasks_all_attacker_logs_flagged']['delta_pp'])} "
     f"(b = {tl['tasks_all_attacker_logs_flagged']['mcnemar_exact']['b']}, c = {tl['tasks_all_attacker_logs_flagged']['mcnemar_exact']['c']}, "
     f"p {pvf(tl['tasks_all_attacker_logs_flagged']['mcnemar_exact'])}); at least one attacker log flagged "
     f"{ppf(tl['tasks_any_attacker_log_flagged']['delta_pp'])} (process {xn(tl['process_any'])}, output-only "
     f"{xn(tl['baseline_any'])}; b = {tl['tasks_any_attacker_log_flagged']['mcnemar_exact']['b']}, "
     f"c = {tl['tasks_any_attacker_log_flagged']['mcnemar_exact']['c']}, p {pvf(tl['tasks_any_attacker_log_flagged']['mcnemar_exact'])}: "
     f"every task has at least one wrong-answer free-rider). Pooled logs: {ppf(cd['detection_pooled']['delta_pp'])} "
     f"({cd['detection_pooled']['mcnemar_exact']['b']} discordant, all process-only; log-level p not cited).")
fr = P3["process"]["validators"]
sub1(line_starting("- Validators: free-rider agents cast"),
     f"- Validators: free-rider agents cast {fr['verdicts_cast_by_free_rider_agents']} of {fr['verdicts_total']} verdicts, on "
     f"{fr['logs_with_a_free_rider_validator']} logs ({fr['attacker_logs_with_a_free_rider_validator']} of them attacker logs). "
     "All validators run the same honest predicate by construction (Byzantine validators are H4, future work), so "
     f"agreement with a separate call of that predicate ({xn(fr['logs_whose_free_rider_verdicts_equal_reference_predicate'])} "
     f"logs; every verdict on every log, {xn(fr['logs_whose_verdicts_all_equal_reference_predicate'])}) is expected. An "
     f"independent re-implementation of the gates agrees with all {A['h3_validator_audit']['revealed_verdicts']} revealed "
     f"verdicts ({A['h3_validator_audit']['disagreeing_with_independent_predicate']} disagreements; §13).")
gc = audit["h3_gate_carrying_detection"]
u3 = U["H3"]
hp = audit["headline_payload_level"]
vline = line_starting("- Vote arithmetic per task: ≥ ")
sub1(vline, vline + "\n" +
     f"- Gate carrying detection: C4 (answer grounded in a re-executed operation) fails on {xn(gc['attacker_logs_failing_C4'])} "
     "attacker logs; process coherence and tool utilization fail only on the orphan_op logs, which C4 also rejects "
     f"({gc['attacker_logs_failing_process_coherence_or_tool_utilization_but_passing_C4']} logs caught by them alone).\n"
     f"- Units: the {u3['attacker_logs']} attacker logs (one per variant per task) are {u3['distinct_attacker_payloads']} "
     "distinct payloads, because the predicate never reads the question and an assertion of the same value on two tasks "
     f"is the same payload; the {u3['honest_logs']} honest logs are {u3['distinct_honest_payloads']} distinct payloads. Honest "
     f"FPR {xn(u3['process_flagged_distinct_honest_payloads'])} distinct payloads (Wilson "
     f"{cif(u3['process_flagged_distinct_honest_payloads']['wilson95'])}), {xn(u3['process_tasks_with_any_honest_log_flagged'])} "
     f"tasks (Wilson {cif(u3['process_tasks_with_any_honest_log_flagged']['wilson95'])}). The "
     f"{hp['logs']} correct-answer free-rider logs (bare_assertion_correct + forged_op) are {hp['distinct_payloads']} "
     f"distinct payloads: the process arm flags {xn(hp['process_flagged_distinct_payloads'])} (Wilson "
     f"{cif(hp['process_flagged_distinct_payloads']['wilson95'])}), the output-only arm "
     f"{xn(hp['output_only_flagged_distinct_payloads'])} (Wilson {cif(hp['output_only_flagged_distinct_payloads']['wilson95'])}).")
ev = audit["evasion_probe"]
reps = P3["process"]["reputation"]
tal = P3["process"]["task_level"]["tasks_all_attacker_logs_flagged"]
sub1(line_starting("**Reading.** The process arm flagged 300/300 free-rider logs"),
     f"**Reading.** The process arm flagged {xn(P3['process']['detection_tpr'])} free-rider logs (every attacker log flagged "
     f"in {xn(tal)} tasks, Wilson lower bound {100 * tal['wilson95'][0]:.1f}%) and {xn(P3['process']['fpr'])} honest logs, "
     f"and kept {xn(P3['process']['exclusion'])} flagged logs out of aggregation. Every rejected log failed its variant's "
     f"intended gate, and all {gc['attacker_logs_failing_C4']['n']} also fail C4. Free-rider reputation fell to the floor "
     f"({reps['floor']:g}) while honest reputation rose, but only as a ledger write: the run is a single batch and "
     f"aggregation weights every author at r₀ = {reps['r0']}, so reputation never influenced a consensus value here. "
     "Output matching misses bare_assertion_correct and forged_op entirely "
     f"({xn(P3['baseline']['detection_per_variant']['bare_assertion_correct']['logs'])} and "
     f"{xn(P3['baseline']['detection_per_variant']['forged_op']['logs'])}, all with the correct answer): the false-negative "
     f"leg of seminar comment (b). Consensus accuracy is {pct(P3['process']['consensus_accuracy']['p'])} in both arms "
     f"({ppf(cd['accuracy_delta_pp'])}): at least {P3['process']['vote_arithmetic']['min_correct_output_logs_per_task']} of 30 "
     "logs per task carry the correct output and no wrong output is shared by more than "
     f"{P3['process']['vote_arithmetic']['max_largest_wrong_output_bloc_per_task']} log, so at this fraction the protocol "
     "adds detection, exclusion and a reputation penalty, not accuracy. Detection is 100% by construction: each "
     "variant was built to violate a gate, the predicate is deterministic and the validators are honest. It is not an "
     "estimate of robustness to adaptive free-riders: an audit probe that wraps the copied answer in a trivial "
     f"calculator operation is accepted on {xn(ev['identity_op_accepted'])} tasks (§13).")

# section 4 rebuilt
rcw = C["rule_c_unfiltered"]
h1u_p, h1u_b = PU["process"], PU["baseline"]
rs_ = h1u_p["fpr_rule_c_split"]
uu = U["H1U"]
tg = T["rule_c_unfiltered_whole_file"]
full_tiers = [t for t in TIERS if t != "minimal"]
tier_fail = [t for t in full_tiers if not tg["per_tier_wilson_upper_lt_5"][t]]
s4 = ["## 4. Honest-Log FPR Without the Added Rule (c)", "",
      "The primary sample is selected with rule (c) and the rule-(d) safety net, which admit only problems whose five "
      "honest tiers the predicate accepts. This section is the non-circular measurement of how the predicate treats "
      "faithful honest logs: rule (c) is **not** applied.", "",
      f"**Table 4a. Whole test file: every problem passing rules (a)+(b) ({rcw['problems']} problems), one faithful log per "
      f"problem and tier ({rcw['logs']} logs).**", "",
      "| Metric | Unit | Value | x/n | Wilson 95% | Clopper–Pearson 95% |", "| --- | --- | ---: | ---: | ---: | ---: |",
      pline("Honest-log FPR, all tiers", "log", rcw["fpr_logs"])]
s4 += [pline(f"Honest-log FPR, tier *{t}*", "log", rcw["fpr_per_tier"][t]) for t in TIERS]
s4 += [pline("Problems with ≥ 1 tier rejected", "problem", rcw["problems_with_any_tier_rejected"]), "",
       f"- Targets (< 5% on the Wilson upper bound): pooled over tiers {'met' if tg['fpr_wilson_upper_lt_5'] else 'not met'} "
       f"({cif(rcw['fpr_logs']['wilson95'])}); per tier recording the full annotated derivation "
       + ("; ".join(f"*{t}* {'met' if tg['per_tier_wilson_upper_lt_5'][t] else 'not met'} ({cif(rcw['fpr_per_tier'][t]['wilson95'])})" for t in full_tiers))
       + f"; problem level {'met' if tg['problem_level_wilson_upper_lt_5'] else 'not met'} "
       f"({cif(rcw['problems_with_any_tier_rejected']['wilson95'])}). The pooled rate is diluted by the *minimal* tier, which "
       "records only the answer's dependency cone and so cannot be rejected; the per-tier rows are the binding ones.", "",
       f"**Table 4b. End-to-end protocol run H1-U (N = 30, primary seed).** Sample: the first 100 problems passing (a)+(b) "
       f"(indices 0–{ov['h1u_last_index']}), i.e. the primary selection rule without rule (c). It shares {ov['shared']} "
       f"problems with the primary sample: it adds the rule-(c) problems {', '.join(map(str, ov['h1u_only']))} and drops "
       f"{', '.join(map(str, ov['primary_only']))}.", "",
       "| Metric | Arm | Value | x/n | Wilson 95% | Clopper–Pearson 95% |", "| --- | --- | ---: | ---: | ---: | ---: |",
       pline("FPR, all honest logs", "process", h1u_p["fpr"]),
       pline("FPR, all honest logs", "output-only", h1u_b["fpr"]),
       pline("FPR, distinct honest payloads", "process", uu["process_flagged_distinct_honest_payloads"]),
       pline("Tasks with ≥ 1 honest log flagged", "process", h1u_p["task_level"]["tasks_with_any_honest_log_flagged"]),
       pline("FPR, logs on rule-(c) problems", "process", rs_["logs_on_rule_c_problems"]),
       pline("FPR, logs on other problems", "process", rs_["logs_on_other_problems"])]
s4 += [pline(f"FPR on rule-(c) problems, tier *{t}*", "process", rs_["per_tier_on_rule_c_problems"][t]) for t in TIERS]
cu = PU["comparative"]
s4 += [pline("Rule-(c) problems with ≥ 1 log flagged", "process", rs_["rule_c_problems_with_any_log_flagged"]),
       pline("Consensus accuracy (tasks)", "process", h1u_p["consensus_accuracy"]),
       pline("Consensus accuracy (tasks)", "output-only", h1u_b["consensus_accuracy"]), "",
       f"- Paired comparison (H1-U), task level: b = {cu['fpr_tasks_any_honest_flagged']['mcnemar_exact']['b']}, "
       f"c = {cu['fpr_tasks_any_honest_flagged']['mcnemar_exact']['c']}, p {pvf(cu['fpr_tasks_any_honest_flagged']['mcnemar_exact'])}.",
       f"- Targets (< 5%): not met at any unit — log {cif(h1u_p['fpr']['wilson95'])}, distinct payload "
       f"{cif(uu['process_flagged_distinct_honest_payloads']['wilson95'])}, task {cif(h1u_p['task_level']['tasks_with_any_honest_log_flagged']['wilson95'])}. "
       "The log-level interval is too narrow: the agents of one tier emit byte-identical logs."
       if not (T["H1U"]["fpr_wilson_upper_lt_5"] or T["H1U"]["fpr_distinct_payload_wilson_upper_lt_5"] or T["H1U"]["fpr_task_level_wilson_upper_lt_5"])
       else "- Targets: see §9.",
       f"- Seed invariance of H1-U over the 5 harness seeds: {R['scenarios']['H1U']['N30']['all_metrics_seed_invariant']}.", "",
       f"**What fails.** Over the {rcw['rejected_logs']} rejected faithful logs: "
       + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in rcw["gate_failures_over_rejected_logs"].items())
       + ". In every rejected log the answer chain re-executes and is grounded; what fails is that at least one recorded "
       "calculator result has no recorded consumer. In the references this happens when the solution continues its "
       "arithmetic in prose or in an equation without an annotation (index 265: the house price 350000 + 17500 + 42000 is "
       "summed in prose, so both fee computations lack a recorded consumer), restates a prose result as an identity "
       f"annotation (index 52: 7.5 / .5 appears only as `<<15=15>>`; {rcw['rule_c_problems_with_identity_annotation']} of the "
       f"{rcw['problems_failing_rule_c']['x']} problems contain one), or computes both options of a comparison and keeps one "
       "(index 15). Index 489 was a converter defect, not an incomplete record: a negative intermediate result was never "
       "wired to its consumer. It is fixed in this run (audit D8) and the problem is now eligible. Full examples: "
       "`rule_c_examples.md`. Every rejection falls on a rule-(c) problem (checked in code, and by an independent "
       "re-implementation of the predicate, §13).", "",
       "| GSM8K index | Annotations | Steps outside answer cone | Identity ann. | Tiers rejected | Tier accepted | Gates failed (every rejected tier) |",
       "| ---: | ---: | --- | --- | --- | --- | --- |"]
s4 += [f"| {pp['gsm8k_index']} | {pp['annotations']} | {', '.join(map(str, pp['steps_outside_answer_cone']))} | "
       f"{'yes' if pp['identity_annotation'] else 'no'} | {len(pp['tiers_rejected'])} | {', '.join(pp['tiers_accepted'])} | "
       f"{' + '.join(pp['failed_gates'])} |" for pp in rcw["per_problem"]]
s4 += ["", "Not evaluated: " + rcw["not_evaluated"] + ".", "",
       f"**Reading.** Without rule (c) the predicate rejects {xn(rcw['fpr_logs'])} faithful honest logs over the whole file "
       f"(pooled {pct(rcw['fpr_logs']['p'])}, Wilson {cif(rcw['fpr_logs']['wilson95'])}); for each tier that records the full "
       f"annotated derivation the rate is {pct(rcw['fpr_per_tier']['terse']['p'])} ({cif(rcw['fpr_per_tier']['terse']['wilson95'])})"
       + (", whose upper bound exceeds the 5% target" if tier_fail else "") + ". These logs are faithful to the reference's "
       "calculator annotations, which are an incomplete record of the derivation; an agent that recorded its prose steps "
       "would pass. The cost falls on logs that record more: the *minimal* tier is never rejected. Inside the protocol, "
       f"H1-U flags {xn(h1u_p['fpr'])} honest logs ({pct(h1u_p['fpr']['p'])}), all on its "
       f"{rs_['rule_c_problems_with_any_log_flagged']['n']} rule-(c) problems; it differs from the whole-file rate because its "
       f"sample contains rule-(c) problems at {pct(rs_['rule_c_problems_with_any_log_flagged']['n'] / 100)} against "
       f"{pct(rcw['problems_failing_rule_c']['p'])} in the whole file, whose figure is the population estimate. Both come from "
       "reference solutions, not from a model, so neither is a live-LLM rate.", "", ""]
sub1(section(4), "\n".join(s4))

# section 5 patches
for old, new in [("| H1b | honest log flagged | log |", "| H1b | flagged, all logs (incl. honest error) | log |"),
                 ("| H1b | any honest log flagged | task |", "| H1b | any log flagged (= any honest-error log) | task |"),
                 ("| H1b | divergent log flagged | log |", "| H1b | honest-error log flagged | log |")]:
    sub1(old, new)
a_any = tl["tasks_any_attacker_log_flagged"]
row_all = line_starting("| H3 | all attacker logs flagged | task |")
sub1(row_all, row_all + f"\n| H3 | any attacker log flagged | task | {a_any['n']} | {a_any['process_positive']} | "
     f"{a_any['baseline_positive']} | {a_any['mcnemar_exact']['b']} | {a_any['mcnemar_exact']['c']} | {pvf(a_any['mcnemar_exact'])} |")
sub1(line_starting("p is exact two-sided;"),
     "p is exact two-sided; '< 0.001' is the canonical form (exact values in results.json and `mcnemar.csv`). With b + c = 0 "
     "the test is uninformative (p = 1 by convention). Cite only task-level rows: logs within a task are not independent "
     "and the honest logs of one tier are byte-identical. Every discordance here is deterministic by construction, so a "
     "p-value measures the size of a designed effect, not evidence that it generalises.")

# section 6 patches
ls_ = P3["process"]["log_size"]
min_ops_honest = min(ls_["per_tier"][t]["n_tool_ops"]["min"] for t in TIERS)
sub1("Detection does not track thinness: every honest *minimal* log has at most 1 step, fewer than any free-rider log (at least 2), and all minimal logs are accepted.",
     f"Detection does not track step count: every honest *minimal* log has {ls_['per_tier']['minimal']['n_steps']['max']} step, "
     f"fewer than any free-rider log (at least {min(ls_['per_variant'][v]['n_steps']['min'] for v in VARIANTS)}), and all "
     "minimal logs are accepted. It does coincide with tool-operation count for the two bare-assertion variants, the only "
     f"logs with no tool operation (every honest log has at least {min_ops_honest}).")
lc = A["h3_ledger_count"]
cost3 = P3["process"]["cost"]["per_log"]
# audit O3: compare at the table's 2-dp rendering (the audit value is stored at 4 dp); fail loudly on a real mismatch
cc3 = P3["process"]["cost"]
match = (f"{lc['protocol_tx_per_log']:.2f}" == f"{cost3['protocol_ledger_tx']:.2f}"
         and f"{lc['all_tx_per_log']:.2f}" == f"{cost3['all_ledger_tx']:.2f}"
         and lc["protocol_tx"] == round(cost3["protocol_ledger_tx"] * lc["logs"])
         and lc["total_tx"] == cc3["ledger_tx_total"])
assert match, "audit ledger count does not match the cost table"
ws_ops, ver_off, rep_w = cc3["walking_skeleton_overhead_ops"], cc3["counters"]["verdicts_computed"], cc3["reputation_updates"]
assert ws_ops == lc["protocol_tx"] + ver_off - rep_w, "walking-skeleton decomposition does not hold"
ol = line_starting("Output-only arm: 0 validation operations")
sub1(ol, ol + f" Counted directly from the H3 (N = 30) chain by the audit: {lc['protocol_tx']} protocol transactions and "
     f"{lc['total_tx']} in all over {lc['logs']} logs ({lc['protocol_tx_per_log']:.2f} and {lc['all_tx_per_log']:.2f} per log), "
     "which matches the table's protocol and all-ledger columns; the walking-skeleton count "
     f"({ws_ops}) is not a ledger count, since it includes the {ver_off} off-ledger verdict computations and excludes "
     f"the {rep_w} reputation writes.")

# section 7 patches
sub1("| Proc TPR | Proc FPR | Proc acc | Out TPR | Out FPR | Out acc | Out FPR divergent |",
     "| Proc TPR | Proc flag (non-attack) | Proc acc | Out TPR | Out flag (non-attack) | Out acc | Out flag (honest error) |")
seed_line = line_starting("Seed invariance (every summary metric identical")
sub1(seed_line, "Non-attack flag rates are FPRs for H1 and H3; for H1b they include the 10% honest-error logs and are not "
     "FPRs (§2).\n\n" + seed_line)

# section 8 patches
s8 = section(8)
s8n = s8.replace("**H1b** (3000 honest logs):", "**H1b** (3000 non-attack logs, 300 of them honest error):")
parts = s8n.split("| Agreement rule | Honest logs flagged |")
assert len(parts) == 3
s8n = parts[0] + "| Agreement rule | Honest logs flagged |" + parts[1] + "| Agreement rule | Non-attack logs flagged |" + parts[2]
assert s8n.count("order. with equal-sized") == 1
s8n = s8n.replace("order. with equal-sized", "order. With equal-sized")
sub1(s8, s8n)

# section 9 rebuilt
def tline(k, d):
    return f"- {k}: " + "; ".join(f"{kk} = {vv}" for kk, vv in d.items())


s9 = ["## 9. Targets (Met Only if the Interval Bound Clears the Target)", ""]
s9 += [tline(k, v) for k, v in T.items() if k != "note"]
s9 += ["", T["note"], "", ""]
sub1(section(9), "\n".join(s9))

# section 10 patches
ml = line_starting("- **Model-agnostic predicate.**")
sub1(ml, ml + " This shows that the predicate and the consensus decision never read `model_id`; it does not show that "
     "logs written by other models meet the schema, which needs the live run (future work).")
vl = line_starting("- **Validators.**")
sub1(vl, vl + " All validators are honest by construction; Byzantine validators (H4) are future work.")

# section 12 patch
sub1(line_starting("- `verify_summary.py` checks every number"),
     "- `verify_summary.py` checks `generated_RESULTS_SUMMARY.md` against `generated_results.json`; `audit/verify_corrected.py` "
     "checks every number in this file against this `results.json` (`corrected_verification.txt`). `run_experiments.sh "
     "--verify-repro` reruns everything, including the audit steps, and compares both results.json files (ignoring "
     "`meta.volatile`) and every other output byte for byte. Source hashes cover source files and pinned inputs only (audit D1).")

# section 13
ic, iw, va3, lk, ti = A["independent_predicate_primary"], A["independent_predicate_whole_file"], A["h3_validator_audit"], A["leakage"], A["tiers"]
st = A["strict_reference_test"]["tokens"]
cf = A["conversion_faithfulness_all_selected"]
same_pairs = sorted({x["gsm8k_index"] for x in ti["same_computation_tier_pairs"]})
s13 = ["## 13. Audit (Independent Checks)", "",
       "- **Independent predicate.** A re-implementation of the three gates written from their definitions (it imports "
       f"neither `validation.py` nor `metrics.py`) agrees with the recorded gates and decision on {ic['logs_checked']} "
       f"primary logs ({ic['gate_or_decision_mismatches']} mismatches; H1, H1b, H3, H1-U at N = 30), with all "
       f"{va3['revealed_verdicts']} revealed H3 verdicts ({va3['free_rider_verdicts']} cast by free-rider agents; "
       f"{va3['disagreeing_with_independent_predicate']} disagreements; {va3['self_validations']} self-validations) and on "
       f"{iw['logs']} whole-file faithful logs ({iw['decision_mismatch_vs_predicate']} mismatches; {iw['rejected']} rejected, "
       f"all on rule-(c) problems: {iw['all_rejections_on_rule_c_problems']}).",
       f"- **Leakage.** The regenerated logs have the same log-id sets as `per_log_records.csv` "
       f"({', '.join(k for k, v in A['log_id_sets_equal_to_csv'].items() if v)}). Over {lk['logs_scanned']} logs: "
       f"{lk['exact_label_values']} label values, {lk['label_substrings_outside_prompt']} label substrings outside the prompt, "
       f"{lk['tasks_with_extra_keys']} tasks with keys beyond task_id, task_type, dataset, prompt. By design, reference "
       "expressions (every tier) and reference sentences (standard, verbose) do enter logs: the scripted agent reproduces "
       "the reference derivation.",
       f"- **Relaxed test.** In the strict substring form, 'reference' occurs in {st['reference']['logs_containing']} of "
       f"{A['strict_reference_test']['logs_checked']} logs, at: {', '.join(st['reference']['paths'])}. "
       + "; ".join(f"'{t}' in {st[t]['logs_containing']}" + (f" (at {', '.join(st[t]['paths'])})" if st[t]["paths"] else "")
                   for t in ("position", "gsm8k_index", "merged_expr", "deps"))
       + ". The strict form fails only on provenance and dataset text, not on a harness-private key, so the relaxation "
       "hid no defect; the test is renamed `test_task_private_keys_never_enter_log`.",
       f"- **Tiers.** {ti['tasks_with_5_distinct_payloads']}/{ti['tasks']} tasks have 5 distinct payloads and "
       f"{ti['tasks_with_5_distinct_step_counts']}/{ti['tasks']} have 5 distinct step counts; within a tier all agents' "
       f"payloads are byte-identical ({ti['payload_byte_identical_within_tier']}). In tasks at GSM8K indices "
       f"{', '.join(map(str, same_pairs))} (one annotation each) *minimal* and *terse* encode the same single computation and "
       "differ only in text and number formatting.",
       f"- **Conversion.** Over the 100 selected problems, calculator ops equal the reference annotations in order "
       f"({cf['ops_match_annotations']}/100) and the final value equals the `####` answer ({cf['final_equals_gsm8k_answer']}/100). "
       f"A random sample of 10 (seed {A['sample_seed']}; indices {', '.join(map(str, A['conversion_sample_indices']))}) is shown "
       "in full in `conversion_sample.md`.",
       f"- **Evasion probe (limitation, not an H3 result).** A free-rider that copies the answer A and records one calculator "
       f"operation 'A' is accepted on {xn(ev['identity_op_accepted'])} tasks (independent predicate: "
       f"{ev['identity_op_accepted_independent']}/{ev['tasks']}); with 'A*1' on {xn(ev['times_one_op_accepted'])}. The "
       "predicate checks that the answer is produced by a re-executable operation, not that the operation does work on the "
       f"question's quantities. In {ev['answer_appears_in_question']}/{ev['tasks']} selected tasks the answer itself appears as a "
       "number in the question.",
       "- **Fixes applied in this run.** D1: " + audit["fixes_applied"]["D1_source_hashes"] + ". D8: "
       + audit["fixes_applied"]["D8_negative_literals"] + ". O3: " + audit["fixes_applied"]["O3_cost_sentence"]
       + ". O4: " + audit["fixes_applied"]["O4_nonnegativity_preference"] + ". O7: "
       + audit["fixes_applied"]["O7_headline_payload_unit"] + ".", ""]
S = S.rstrip("\n") + "\n\n" + "\n".join(s13)
open(os.path.join(RES, "RESULTS_SUMMARY.md"), "w", encoding="utf-8").write(S)
print("corrected results.json, RESULTS_SUMMARY.md, mcnemar.csv, robustness.csv, h1b_divergence.csv written")
