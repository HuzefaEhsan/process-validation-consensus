#!/usr/bin/env python3
"""report.py -- render RESULTS_SUMMARY.md and gsm8k_conversion_report.md from the results dict.

Every number printed here is read from results.json (via the in-memory dict) and formatted by ONE
set of helpers (canonical format, fact sheet sec 5): percentages to one decimal, percentage points
to one decimal with sign, reputations to four decimals, counts as integers, intervals as [lo, hi]
in % to one decimal. Nothing is typed by hand.
"""
from __future__ import annotations

import gsm8k_logs as g


def pct(p):
    return "n/a" if p is None else f"{100 * p:.1f}%"


def cif(ci):
    return "n/a" if ci is None else f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def ppf(d):
    return "n/a" if d is None else f"{(0.0 if abs(d) < 5e-4 else d):+.1f} pp"


def repf(r):
    return "n/a" if r is None else f"{r:.4f}"


def row(*cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def prow(label, arm, rec):
    if rec is None:
        return row(label, arm, "n/a", "n/a", "n/a", "n/a")
    return row(label, arm, pct(rec["p"]), f"{rec['x']}/{rec['n']}", cif(rec["wilson95"]), cif(rec["cp95"]))


HDR = [row("Metric", "Arm", "Value", "x/n", "Wilson 95%", "Clopper–Pearson 95%"),
       row("---", "---", "---:", "---:", "---:", "---:")]


def pv(m):
    """McNemar p-value, canonical."""
    return f"p {m['p_fmt']}" if m["p_fmt"].startswith("<") else f"p = {m['p_fmt']}"


def mcn(d):
    m = d["mcnemar_exact"]
    return f"b = {m['b']}, c = {m['c']}, {pv(m)}"


def xn(rec):
    return f"{rec['x']}/{rec['n']}"


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def write_summary(res: dict, path: str) -> None:
    m, cfg = res["meta"], res["meta"]["config"]
    PN, PS = cfg["primary_N"], cfg["primary_seed"]
    NS = [PN] + cfg["robustness_N"]
    S = res["scenarios"]
    h1, h1b, h3, h1u = (S[k][f"N{PN}"]["primary"] for k in ("H1", "H1b", "H3", "H1U"))
    ds = m["dataset"]
    dv = cfg["divergence"]
    VAR = cfg["variants"]
    L = []
    A = L.append
    A("# RESULTS_SUMMARY — P2 offline H1/H1b/H3 evaluation on real GSM8K problems")
    A("")
    A(f"*Generated from `results/results.json` (the numeric authority; this file repeats its numbers in "
      f"canonical format). Generated at {m['volatile']['generated_at']}.*")
    A("")
    A("**What was run.** "
      f"{cfg['n_tasks']} GSM8K test problems ({ds['name']}, `{ds['source_repo']}` @ `{ds['upstream_commit'][:12]}`, "
      f"file SHA-256 `{ds['file_sha256'][:16]}…`), selected as the first {cfg['n_tasks']} eligible problems by index "
      f"(indices 0–{ds['conversion']['selection_prefix']['last_index_scanned']}). "
      "Agents are **scripted**: each log is converted mechanically from the reference solution's calculator "
      f"annotations; **no LLM is in the loop** and every log records `model_id = \"{cfg['model_id_in_logs']}\"`. "
      f"Primary configuration: N = {PN} agents, k = {cfg['k']}, reject supermajority 2/3, r₀ = {cfg['r0']}, "
      f"α = {cfg['alpha']:.2f}, β = {cfg['beta']:.2f}, harness seed {PS}; robustness at N = "
      f"{', '.join(str(n) for n in cfg['robustness_N'])}. Scenarios: **H1** all honest; **H1b** all honest with "
      f"{plural(dv['divergent_agents_by_N'][str(PN)], 'agent')} per task (seeded, {pct(dv['fraction'])}) deriving a "
      f"different output through a valid chain (BLUEPRINT D2); **H3** {cfg['free_riders_by_N'][str(PN)]} free-riders "
      f"({pct(cfg['free_riders_by_N'][str(PN)] / PN)}) cycling round-robin through {len(VAR)} variants; **H1-U** as H1 "
      f"on the first {cfg['n_tasks']} problems passing only rules (a)+(b) (rule (c) not applied). "
      "All validators are honest, including free-rider agents drawn into peer sets. The harness seed changes only "
      "peer-set draws (and commitment salts); seeds are never pooled.")
    A("")
    A("**Unit of analysis and tests.** TPR and FPR are per log; accuracy is per task. Logs within a task are not "
      "independent (same problem; honest agents of one tier emit byte-identical payloads), so log-level intervals "
      "and p-values are optimistic; task-level figures are given alongside. Both arms judge the same logs, so arm "
      "comparisons use McNemar's exact two-sided test on discordant pairs (b = flagged/correct by the process arm "
      "only, c = by the output-only arm only); it replaces the independent-sample Newcombe interval of the "
      "blueprint. Intervals are on the primary seed only.")
    A("")

    # ------------------------------------------------------------------ H1
    pr, ba, cm = h1["process"], h1["baseline"], h1["comparative"]
    A(f"## 1. H1 — All-Honest Baseline (N = {PN}, {cfg['n_tasks']} tasks, {h1['logs']} logs)")
    A("")
    L.extend(HDR)
    L.append(prow("Consensus accuracy (tasks)", "process", pr["consensus_accuracy"]))
    L.append(prow("Consensus accuracy (tasks)", "output-only", ba["consensus_accuracy"]))
    L.append(prow("FPR (honest logs flagged)", "process", pr["fpr"]))
    L.append(prow("FPR (honest logs flagged)", "output-only", ba["fpr"]))
    for t in g.EFFORT_TIERS:
        L.append(prow(f"FPR, tier *{t}*", "process", pr["fpr_per_tier"][t]))
    L.append(prow("Tasks with ≥ 1 honest log flagged", "process", pr["task_level"]["tasks_with_any_honest_log_flagged"]))
    L.append(prow("Digest agreement (all k digests identical)", "process", pr["digest_agreement"]))
    A("")
    rep = pr["reputation"]
    A(f"- H1-FPR confirmatory interval (97.5%, BLUEPRINT D6, unsigned): Wilson {cif(pr['fpr']['wilson97_5'])}, "
      f"CP {cif(pr['fpr']['cp97_5'])}.")
    nodisc = cm["fpr_logs"]["mcnemar_exact"]["discordant"] == 0 and cm["accuracy_tasks"]["mcnemar_exact"]["discordant"] == 0
    A(f"- Paired comparison: FPR {mcn(cm['fpr_logs'])}; accuracy {mcn(cm['accuracy_tasks'])}"
      + (" (no discordant pairs)." if nodisc else "."))
    A(f"- Honest reputation after 3 updates: {', '.join(repf(x) for x in rep['honest_after_3_updates'])}; "
      f"final after {rep['updates_per_agent']} updates: mean {repf(rep['honest_final']['mean'])} "
      f"(min {repf(rep['honest_final']['min'])}, max {repf(rep['honest_final']['max'])}).")
    A(f"- Ledger `verify()`: {pr['integrity']['ledger_verify']}. Label leaks: "
      f"{pr['integrity']['audited_scan_leaks'] + pr['integrity']['extended_scan_leaks']} "
      f"(audited scan over {pr['integrity']['audited_scan_logs']} logs + extended scan incl. variant labels).")
    A("- Log size per tier (steps / tool ops / canonical bytes, mean [min–max]):")
    for t in g.EFFORT_TIERS:
        sz = pr["log_size"]["per_tier"][t]
        A(f"  - *{t}*: {sz['n_steps']['mean']:.1f} [{sz['n_steps']['min']}–{sz['n_steps']['max']}] / "
          f"{sz['n_tool_ops']['mean']:.1f} [{sz['n_tool_ops']['min']}–{sz['n_tool_ops']['max']}] / "
          f"{sz['canonical_bytes']['mean']:.0f} [{sz['canonical_bytes']['min']}–{sz['canonical_bytes']['max']}]")
    A("")
    A("**Reading.** " +
      f"The process arm flagged {xn(pr['fpr'])} honest logs across the five effort tiers (FPR {pct(pr['fpr']['p'])}, "
      f"Wilson upper bound {pct(pr['fpr']['wilson95'][1])}), including the *minimal* tier, whose logs carry a single "
      f"merged calculator step. "
      + (f"Both arms reach {pct(pr['consensus_accuracy']['p'])} accuracy (Wilson lower bound "
         f"{pct(pr['consensus_accuracy']['wilson95'][0])}). " if pr['consensus_accuracy']['p'] == ba['consensus_accuracy']['p']
         else f"Accuracy is {pct(pr['consensus_accuracy']['p'])} (process) and {pct(ba['consensus_accuracy']['p'])} (output-only). ")
      + f"Every log's k = {cfg['k']} recompute digests were "
      f"byte-identical ({pct(pr['digest_agreement']['p'])}). "
      + (f"The output-only arm flags {xn(ba['fpr'])} here only because every honest log carries the same correct "
         "output; honest output divergence is exercised in H1b. " if ba['fpr']['x'] == 0 else
         f"The output-only arm flags {xn(ba['fpr'])} honest logs. ") +
      "The selection itself depends on the added rule (c); §4 reports the FPR without it.")
    A("")

    # ------------------------------------------------------------------ H1b
    pr, ba, cm = h1b["process"], h1b["baseline"], h1b["comparative"]
    dsp, dsb = pr["fpr_divergence_split"], ba["fpr_divergence_split"]
    A(f"## 2. H1b — Honest Output Divergence (BLUEPRINT D2; N = {PN}, {h1b['logs']} logs, "
      f"{dsp['divergent_logs']['n']} divergent)")
    A("")
    L.extend(HDR)
    L.append(prow("FPR, all honest logs", "process", pr["fpr"]))
    L.append(prow("FPR, all honest logs", "output-only", ba["fpr"]))
    L.append(prow("FPR, divergent logs", "process", dsp["divergent_logs"]))
    L.append(prow("FPR, divergent logs", "output-only", dsb["divergent_logs"]))
    L.append(prow("FPR, non-divergent logs", "process", dsp["non_divergent_logs"]))
    L.append(prow("FPR, non-divergent logs", "output-only", dsb["non_divergent_logs"]))
    L.append(prow("Tasks with ≥ 1 divergent log flagged", "process", dsp["tasks_with_any_divergent_log_flagged"]))
    L.append(prow("Tasks with ≥ 1 divergent log flagged", "output-only", dsb["tasks_with_any_divergent_log_flagged"]))
    L.append(prow("Consensus accuracy (tasks)", "process", pr["consensus_accuracy"]))
    L.append(prow("Consensus accuracy (tasks)", "output-only", ba["consensus_accuracy"]))
    L.append(prow("Digest agreement", "process", pr["digest_agreement"]))
    A("")
    A(f"- Paired comparison, divergent logs: {mcn(cm['fpr_divergent_logs'])}; all honest logs: {mcn(cm['fpr_logs'])}; "
      f"tasks with any honest log flagged: {mcn(cm['fpr_tasks_any_honest_flagged'])}.")
    A(f"- Divergent derivations (one per task, rendered in each divergent agent's own tier): operand taken from "
      f"the question text in {dv['operand_in_question']}/{cfg['n_tasks']} tasks; integer or ≤ 2-decimal values in "
      f"{dv['clean_values']}/{cfg['n_tasks']}; dependency wiring identical to the reference in "
      f"{dv['wiring_identical']}/{cfg['n_tasks']} (the other {dv['wiring_subset_value_collision']} drop one spurious "
      f"link caused by two reference steps with equal values); more than one step recomputed in "
      f"{dv['multi_step_recomputation']}/{cfg['n_tasks']}. Per-task detail: `h1b_divergent_derivations.csv`.")
    A(f"- All {dsp['divergent_logs']['n']} divergent logs carry a wrong output "
      f"({dsp['divergent_logs_output_correct']} correct). Vote arithmetic per task: ≥ "
      f"{pr['vote_arithmetic']['min_correct_output_logs_per_task']} correct outputs vs a divergent bloc of ≤ "
      f"{pr['vote_arithmetic']['max_largest_wrong_output_bloc_per_task']}.")
    A(f"- Ledger `verify()`: {pr['integrity']['ledger_verify']}. Label leaks: "
      f"{pr['integrity']['audited_scan_leaks'] + pr['integrity']['extended_scan_leaks']}.")
    A("")
    A("**Reading.** " +
      f"Output matching flagged {xn(dsb['divergent_logs'])} validly derived divergent logs "
      f"({pct(dsb['divergent_logs']['p'])}) and the process predicate flagged {xn(dsp['divergent_logs'])}"
      + (": every divergent log re-executes and is grounded, so the predicate has nothing to reject. This is the "
         "empirical false-positive leg of output matching (seminar comment (b)). " if dsp['divergent_logs']['x'] == 0
         else ". ") +
      "Two limits matter for the paper. First, the "
      "flagged logs carry wrong answers, so the output-only arm's flags are false positives only under the "
      "free-rider-detection objective; under an answer-quality objective they are correct, and the process arm "
      "admits these wrong outputs into aggregation, where the honest plurality outvotes them "
      + (f"({pct(pr['consensus_accuracy']['p'])} accuracy in both arms). " if pr['consensus_accuracy']['p'] == ba['consensus_accuracy']['p']
         else f"(accuracy {pct(pr['consensus_accuracy']['p'])} process vs {pct(ba['consensus_accuracy']['p'])} output-only). ")
      + "Second, the divergence is constructed (one "
      "changed operand, one derivation per task), not observed from a model.")
    A("")

    # ------------------------------------------------------------------ H3
    pr, ba, cm = h3["process"], h3["baseline"], h3["comparative"]
    A(f"## 3. H3 — Free-Riding at {pct(h3['free_rider_share'])} (N = {PN}, {h3['n_free_riders']} free-riders, "
      f"{h3['attacker_logs']} of {h3['logs']} logs)")
    A("")
    L.extend(HDR)
    L.append(prow("Detection TPR (attacker logs flagged)", "process", pr["detection_tpr"]))
    L.append(prow("Detection TPR (attacker logs flagged)", "output-only", ba["detection_tpr"]))
    L.append(prow("FPR (honest logs flagged)", "process", pr["fpr"]))
    L.append(prow("FPR (honest logs flagged)", "output-only", ba["fpr"]))
    L.append(prow("Consensus accuracy (tasks)", "process", pr["consensus_accuracy"]))
    L.append(prow("Consensus accuracy (tasks)", "output-only", ba["consensus_accuracy"]))
    L.append(prow("Flagged logs excluded from aggregate", "process", pr["exclusion"]))
    L.append(prow("Tasks with all attacker logs flagged", "process", pr["task_level"]["tasks_all_attacker_logs_flagged"]))
    L.append(prow("Tasks with all attacker logs flagged", "output-only", ba["task_level"]["tasks_all_attacker_logs_flagged"]))
    L.append(prow("Digest agreement", "process", pr["digest_agreement"]))
    A("")
    A(f"- Comparative accuracy (process − output-only): {ppf(cm['accuracy_delta_pp'])} ({mcn(cm['accuracy_tasks'])}).")
    A(f"- Comparative detection, pooled logs: {ppf(cm['detection_pooled']['delta_pp'])} ({mcn(cm['detection_pooled'])}); "
      f"tasks with all attacker logs flagged: {ppf(cm['detection_tasks_all_attackers_flagged']['delta_pp'])} "
      f"({mcn(cm['detection_tasks_all_attackers_flagged'])}).")
    A(f"- H3-TPR confirmatory interval (97.5%, D6 unsigned): Wilson {cif(pr['detection_tpr']['wilson97_5'])}, "
      f"CP {cif(pr['detection_tpr']['cp97_5'])}.")
    A("")
    A("**Per variant** (process arm = supermajority reject; output-only arm = dissent from the task plurality; "
      "McNemar on the variant's logs):")
    A("")
    A(row("Variant", "Logs", "Correct output", "Process TPR", "Process Wilson 95%", "Output-only TPR",
          "Output-only Wilson 95%", "Δ detection", "McNemar (logs)", "Intended gate failed"))
    A(row("---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---", "---:"))
    for v in VAR:
        dp = pr["detection_per_variant"][v]
        db = ba["detection_per_variant"][v]
        ga = pr["gate_attribution_per_variant"][v]
        cv = cm["detection_per_variant"][v]["logs"]
        A(row(v, dp["logs"]["n"], dp["output_correct_logs"], pct(dp["logs"]["p"]), cif(dp["logs"]["wilson95"]),
              pct(db["logs"]["p"]), cif(db["logs"]["wilson95"]), ppf(cv["delta_pp"]), mcn(cv),
              f"{ga['intended_gate']}: {xn(ga['intended_gate_failed'])}"))
    A("")
    A("Gate attribution (rejected logs failing each gate / sub-check):")
    A("")
    A(row("Variant", "Rejected", "Process coherence", "Tool utilization", "Causal sufficiency", "C1", "C2", "C3", "C4"))
    A(row("---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"))
    for v in VAR:
        ga = pr["gate_attribution_per_variant"][v]
        c = ga["failed_gate_counts"]
        A(row(v, ga["rejected_logs"], c["gate_process_coherence"], c["gate_tool_utilization"],
              c["gate_causal_sufficiency"], c["C1"], c["C2"], c["C3"], c["C4"]))
    A("")
    rep = pr["reputation"]
    va = pr["vote_arithmetic"]
    vd = pr["validators"]
    A(f"- Reputation after 3 updates: honest {', '.join(repf(x) for x in rep['honest_after_3_updates'])}; "
      f"free-rider {', '.join(repf(x) for x in rep['free_rider_after_3_updates'])}.")
    A(f"- Final reputation after {rep['updates_per_agent']} updates: honest mean {repf(rep['honest_final']['mean'])} "
      f"(min {repf(rep['honest_final']['min'])}); free-riders "
      f"{', '.join(repf(x) for x in rep['free_rider_final'].values())} (floor {rep['floor']:g}); "
      f"separation (min honest − max free-rider) {repf(rep['separation_min_honest_minus_max_free_rider'])}.")
    A(f"- Validators: free-rider agents cast {vd['verdicts_cast_by_free_rider_agents']} of {vd['verdicts_total']} "
      f"verdicts, on {vd['logs_with_a_free_rider_validator']} logs ({vd['attacker_logs_with_a_free_rider_validator']} "
      f"of them attacker logs); on {xn(vd['logs_whose_free_rider_verdicts_equal_reference_predicate'])} of those logs "
      "their verdicts equal an independent run of the predicate, and every verdict on every log does "
      f"({xn(vd['logs_whose_verdicts_all_equal_reference_predicate'])}).")
    A(f"- Honest-log gate failures: {sum(pr['honest_gate_failures'].values())}. Ledger `verify()`: "
      f"{pr['integrity']['ledger_verify']}. Label leaks: "
      f"{pr['integrity']['audited_scan_leaks'] + pr['integrity']['extended_scan_leaks']}.")
    A(f"- Vote arithmetic per task: ≥ {va['min_correct_output_logs_per_task']} logs carry the correct output; "
      f"no wrong output is shared by more than {plural(va['max_largest_wrong_output_bloc_per_task'], 'log')} "
      f"(≤ {va['max_attacker_logs_per_task']} attacker logs per task).")
    A("")
    all_intended = all(ga["intended_gate_failed"]["x"] == ga["intended_gate_failed"]["n"]
                       for ga in pr["gate_attribution_per_variant"].values())
    fr_at_floor = all(abs(x - rep["floor"]) < 1e-12 for x in rep["free_rider_final"].values())
    missed = [v for v in VAR if ba["detection_per_variant"][v]["logs"]["x"] == 0]
    base_pv = "; ".join(f"{v} {pct(ba['detection_per_variant'][v]['logs']['p'])}" for v in VAR)
    miss_txt = " and ".join(f"{v} ({ba['detection_per_variant'][v]['output_correct_logs']} logs, all with the "
                            f"correct answer)" for v in missed)
    A("**Reading.** " +
      f"The process arm flagged {xn(pr['detection_tpr'])} free-rider logs ({pct(pr['detection_tpr']['p'])}, Wilson "
      f"lower bound {pct(pr['detection_tpr']['wilson95'][0])}) and {xn(pr['fpr'])} honest logs, and kept "
      f"{xn(pr['exclusion'])} flagged logs out of aggregation. Free-rider reputation "
      f"{'fell to the floor' if fr_at_floor else 'fell'} while honest reputation rose. "
      f"{'Every rejected log failed its variant' + chr(39) + 's intended gate. ' if all_intended else 'Not every rejected log failed its intended gate (see table). '}"
      f"Output-only detection per variant: {base_pv}. "
      + (f"Output matching misses {miss_txt} entirely: this is the false-negative leg of output matching "
         "(seminar comment (b)). " if missed else "") +
      f"Consensus accuracy is {pct(pr['consensus_accuracy']['p'])} in both arms "
      f"({ppf(cm['accuracy_delta_pp'])}): with at least {va['min_correct_output_logs_per_task']} correct outputs per "
      f"task and no wrong output shared by more than {plural(va['max_largest_wrong_output_bloc_per_task'], 'log')}, "
      "the honest plurality fixes the output in both arms, so at this fraction the protocol adds detection, exclusion "
      "and reputation penalty, not accuracy. Detection is 100% by construction for gate-violating variants under "
      "honest validators and a deterministic predicate; it is a mechanism demonstration, not an estimate of "
      "robustness to adaptive free-riders.")
    A("")

    # ------------------------------------------------------------------ rule (c)
    rc = res["rule_c_unfiltered"]
    pr, ba, cm = h1u["process"], h1u["baseline"], h1u["comparative"]
    rsp, rsb = pr["fpr_rule_c_split"], ba["fpr_rule_c_split"]
    A("## 4. Honest-Log FPR Without the Added Rule (c)")
    A("")
    A(f"Rule (c) (complete recorded derivation) was added to the two requested eligibility rules. This section "
      f"reports what the predicate does to faithful honest logs when rule (c) is **not** applied.")
    A("")
    A(f"**Table 4a. Whole test file: every problem passing rules (a)+(b) ({rc['problems']} problems), one faithful log "
      f"per problem and tier ({rc['logs']} logs).**")
    A("")
    A(row("Metric", "Unit", "Value", "x/n", "Wilson 95%", "Clopper–Pearson 95%"))
    A(row("---", "---", "---:", "---:", "---:", "---:"))
    A(row("Honest-log FPR, all tiers", "log", pct(rc["fpr_logs"]["p"]), xn(rc["fpr_logs"]),
          cif(rc["fpr_logs"]["wilson95"]), cif(rc["fpr_logs"]["cp95"])))
    for t in g.EFFORT_TIERS:
        r_ = rc["fpr_per_tier"][t]
        A(row(f"Honest-log FPR, tier *{t}*", "log", pct(r_["p"]), xn(r_), cif(r_["wilson95"]), cif(r_["cp95"])))
    r_ = rc["problems_with_any_tier_rejected"]
    A(row("Problems with ≥ 1 tier rejected", "problem", pct(r_["p"]), xn(r_), cif(r_["wilson95"]), cif(r_["cp95"])))
    A("")
    A(f"**Table 4b. End-to-end protocol run H1-U (N = {PN}, primary seed; first {cfg['n_tasks']} problems passing "
      f"(a)+(b), indices 0–{ds['h1u_tasks']['last_index']}, of which {len(ds['h1u_tasks']['rule_c_problems'])} fail "
      f"rule (c): {', '.join(str(i) for i in ds['h1u_tasks']['rule_c_problems'])}).**")
    A("")
    L.extend(HDR)
    L.append(prow("FPR, all honest logs", "process", pr["fpr"]))
    L.append(prow("FPR, all honest logs", "output-only", ba["fpr"]))
    L.append(prow("FPR, logs on rule-(c) problems", "process", rsp["logs_on_rule_c_problems"]))
    L.append(prow("FPR, logs on other problems", "process", rsp["logs_on_other_problems"]))
    for t in g.EFFORT_TIERS:
        L.append(prow(f"FPR on rule-(c) problems, tier *{t}*", "process", rsp["per_tier_on_rule_c_problems"][t]))
    L.append(prow("Rule-(c) problems with ≥ 1 log flagged", "process", rsp["rule_c_problems_with_any_log_flagged"]))
    L.append(prow("Tasks with ≥ 1 honest log flagged", "process", pr["task_level"]["tasks_with_any_honest_log_flagged"]))
    L.append(prow("Consensus accuracy (tasks)", "process", pr["consensus_accuracy"]))
    L.append(prow("Consensus accuracy (tasks)", "output-only", ba["consensus_accuracy"]))
    A("")
    A(f"- Paired comparison (H1-U): FPR {mcn(cm['fpr_logs'])}; tasks {mcn(cm['fpr_tasks_any_honest_flagged'])}.")
    A(f"- Targets on the Wilson upper bound (< 5%): whole file {'met' if rc['fpr_logs']['wilson95'][1] < 0.05 else 'not met'} "
      f"({cif(rc['fpr_logs']['wilson95'])}); H1-U {'met' if pr['fpr']['wilson95'][1] < 0.05 else 'not met'} "
      f"({cif(pr['fpr']['wilson95'])}).")
    A(f"- Seed invariance of H1-U over the 5 harness seeds: {S['H1U'][f'N{PN}']['all_metrics_seed_invariant']}.")
    A("")
    gf = rc["gate_failures_over_rejected_logs"]
    A(f"**Gate breakdown.** Over the {rc['rejected_logs']} rejected faithful logs: "
      + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in gf.items()) + ". Gate patterns per problem: "
      + "; ".join(f"{k}: {v} problems" for k, v in rc["gate_patterns"].items()) + ". "
      f"{rc['rule_c_problems_with_identity_annotation']} of the {rc['problems_failing_rule_c']['x']} problems contain "
      "an identity annotation (e.g. `<<15=15>>`, a value computed in prose). Every rejection falls on a rule-(c) "
      "problem (checked in code). Per problem:")
    A("")
    A(row("GSM8K index", "Annotations", "Orphan steps", "Identity ann.", "Tiers rejected", "Tier accepted",
          "Gates failed (every rejected tier)"))
    A(row("---:", "---:", "---", "---", "---", "---", "---"))
    for pp in rc["per_problem"]:
        gates = " + ".join(pp["failed_gates"]) if pp["same_gate_pattern_in_every_rejected_tier"] else \
            "; ".join(f"{t}: {' + '.join(v)}" for t, v in pp["failed_gates_by_tier"].items())
        A(row(pp["gsm8k_index"], pp["annotations"], ", ".join(str(x) for x in pp["orphan_steps"]),
              "yes" if pp["identity_annotation"] else "no", len(pp["tiers_rejected"]),
              ", ".join(pp["tiers_accepted"]) or "—", gates))
    A("")
    A(f"Not evaluated: {rc['not_evaluated']}.")
    A("")
    failed = [k for k, v in gf.items() if v == rc["rejected_logs"]]
    never = [k for k, v in gf.items() if v == 0]
    minimal_x = rc["fpr_per_tier"]["minimal"]["x"]
    win_rate = rsp["rule_c_problems_with_any_log_flagged"]["n"] / cfg["n_tasks"]
    file_rate = rc["problems_failing_rule_c"]["p"]
    A("**Reading.** " +
      f"Without rule (c), the predicate rejects {xn(rc['fpr_logs'])} faithful honest logs over the whole file "
      f"(FPR {pct(rc['fpr_logs']['p'])}, Wilson {cif(rc['fpr_logs']['wilson95'])}); all rejections sit on the "
      f"{rc['problems_failing_rule_c']['x']} problems ({pct(rc['problems_failing_rule_c']['p'])}) whose reference "
      "records a calculator result that never reaches the answer"
      + (f", and every rejected log fails {' and '.join(k.replace('_', ' ') for k in failed)}" if failed else "")
      + (f" and never {', '.join(k.replace('_', ' ') for k in never if k in ('causal_sufficiency',))}"
         if "causal_sufficiency" in never else "") + ". "
      + ("The *minimal* tier, which records only the answer's dependency cone, is never rejected, so the pressure "
         "falls on logs that record more, not less. " if minimal_x == 0 else
         f"The *minimal* tier is rejected on {xn(rc['fpr_per_tier']['minimal'])} problems. ")
      + f"The end-to-end run reproduces the single-log decision inside the protocol: H1-U flags {xn(pr['fpr'])} "
      f"honest logs ({pct(pr['fpr']['p'])}), "
      + (f"all on its {len(ds['h1u_tasks']['rule_c_problems'])} rule-(c) problems. "
         if rsp["logs_on_other_problems"]["x"] == 0 else
         f"{xn(rsp['logs_on_other_problems'])} of them outside its rule-(c) problems. ")
      + (f"The two rates differ because the first {cfg['n_tasks']} problems passing (a)+(b) contain rule-(c) cases "
         f"at {pct(win_rate)} against {pct(file_rate)} in the whole file; the whole-file figure is the population "
         "estimate. " if abs(win_rate - file_rate) > 1e-12 else "")
      + "Both come from reference solutions, not from a model, so neither is a live-LLM rate.")
    A("")

    # ------------------------------------------------------------------ McNemar table
    A("## 5. Paired Arm Comparisons (McNemar Exact, Primary Configuration)")
    A("")
    A(row("Scenario", "Comparison", "Unit", "n", "Process positive", "Output-only positive", "b", "c", "p"))
    A(row("---", "---", "---", "---:", "---:", "---:", "---:", "---:", "---"))

    def mrow(sc, comp, unit, d):
        mm = d["mcnemar_exact"]
        A(row(sc, comp, unit, d["n"], d["process_positive"], d["baseline_positive"], mm["b"], mm["c"],
              mm["p_fmt"] if mm["p_fmt"].startswith("<") else f"= {mm['p_fmt']}"))

    for sc, blk in (("H1", h1), ("H1b", h1b), ("H3", h3), ("H1-U", h1u)):
        c = blk["comparative"]
        mrow(sc, "correct consensus", "task", c["accuracy_tasks"])
        mrow(sc, "honest log flagged", "log", c["fpr_logs"])
        mrow(sc, "any honest log flagged", "task", c["fpr_tasks_any_honest_flagged"])
        if "fpr_divergent_logs" in c:
            mrow(sc, "divergent log flagged", "log", c["fpr_divergent_logs"])
        if "detection_pooled" in c:
            mrow(sc, "attacker log flagged", "log", c["detection_pooled"])
            mrow(sc, "all attacker logs flagged", "task", c["detection_tasks_all_attackers_flagged"])
            for v in VAR:
                mrow(sc, f"{v} flagged", "log", c["detection_per_variant"][v]["logs"])
                mrow(sc, f"all {v} logs flagged", "task", c["detection_per_variant"][v]["tasks"])
    A("")
    A("p is exact two-sided; '< 0.001' is the canonical form (exact values in results.json and `mcnemar.csv`). "
      "With b + c = 0 the test is uninformative (p = 1 by convention). Log-level p-values are optimistic "
      "(non-independent logs); cite the task-level test where one exists.")
    A("")

    # ------------------------------------------------------------------ cost
    A("## 6. Verification Cost (Counts, Not Latency; Primary Seed)")
    A("")
    A(row("Scenario", "N", "Logs", "Peer-set records", "Verdicts", "Commits", "Reveals", "Seals",
          "Reputation updates", "Protocol ledger tx / log", "All ledger tx / log", "Walking-skeleton overhead ops"))
    A(row(*(["---"] * 2 + ["---:"] * 10)))
    cost_keys = [(sc, N) for sc in ("H1", "H1b", "H3") for N in NS] + [("H1U", PN)]
    for sc, N in cost_keys:
        c = S[sc][f"N{N}"]["primary"]["process"]["cost"]
        k = c["counters"]
        A(row("H1-U" if sc == "H1U" else sc, N, k["logs"], k["peer_set_assignments"], k["verdicts_computed"],
              k["commits"], k["reveals"], k["seals"], c["reputation_updates"],
              f"{c['per_log']['protocol_ledger_tx']:.2f}", f"{c['per_log']['all_ledger_tx']:.2f}",
              c["walking_skeleton_overhead_ops"]))
    A("")
    A("Output-only arm: 0 validation operations (one plurality per task). Per log, the process arm performs "
      f"k = {cfg['k']} predicate evaluations (verdicts), {cfg['k']} commits, {cfg['k']} reveals, 1 seal, 1 peer-set "
      "record and 1 reputation write. \"Walking-skeleton overhead ops\" = "
      f"`{h1['process']['cost']['walking_skeleton_overhead_formula']}` — the formula behind the earlier \"+165 ops\" "
      "figure (15 logs × 11); the \"150\" figure omits the 15 peer-set records.")
    A("")
    A("Steps, tool operations and canonical bytes per log (H3 primary; divergent logs from H1b):")
    A("")
    A(row("Log class", "Steps (mean)", "Tool ops (mean)", "Bytes (mean)"))
    A(row("---", "---:", "---:", "---:"))
    for t in g.EFFORT_TIERS:
        sz = h3["process"]["log_size"]["per_tier"][t]
        A(row(f"honest / {t}", f"{sz['n_steps']['mean']:.1f}", f"{sz['n_tool_ops']['mean']:.1f}",
              f"{sz['canonical_bytes']['mean']:.0f}"))
    sz = h1b["process"]["log_size"]["divergent_honest"]
    A(row("honest divergent (H1b, all tiers)", f"{sz['n_steps']['mean']:.1f}", f"{sz['n_tool_ops']['mean']:.1f}",
          f"{sz['canonical_bytes']['mean']:.0f}"))
    for v in VAR:
        sz = h3["process"]["log_size"]["per_variant"][v]
        A(row(f"free-rider / {v}", f"{sz['n_steps']['mean']:.1f}", f"{sz['n_tool_ops']['mean']:.1f}",
              f"{sz['canonical_bytes']['mean']:.0f}"))
    A("")
    ptx = {S[sc][f"N{N}"]["primary"]["process"]["cost"]["per_log"]["protocol_ledger_tx"] for sc, N in cost_keys}
    min_steps = h3["process"]["log_size"]["per_tier"]["minimal"]["n_steps"]["max"]
    fr_steps = min(h3["process"]["log_size"]["per_variant"][v]["n_steps"]["min"] for v in VAR)
    A("**Reading.** " +
      (f"Protocol ledger transactions per log are the same in every scenario and population "
       f"({', '.join(f'{x:.2f}' for x in sorted(ptx))}); the rest of the chain is one log commit per log plus "
       "per-run genesis and phase records, so cost grows linearly with the number of logs. "
       if len(ptx) == 1 else
       f"Protocol ledger transactions per log vary across scenarios ({', '.join(f'{x:.2f}' for x in sorted(ptx))}). ") +
      (f"Detection does not track thinness: every honest *minimal* log has at most {plural(min_steps, 'step')}, fewer "
       f"than any free-rider log (at least {fr_steps}), and all minimal logs are accepted."
       if min_steps < fr_steps else "See the log-size table for how honest and free-rider log sizes compare."))
    A("")

    # ------------------------------------------------------------------ robustness
    A("## 7. Robustness: Population Size × Harness Seed")
    A("")
    A(row("Scenario", "N", "Seed", "Proc TPR", "Proc FPR", "Proc acc", "Out TPR", "Out FPR", "Out acc",
          "Out FPR divergent", "Out TPR bare-correct", "Out TPR forged-op", "Digest agr.", "verify()", "Leaks",
          "Honest rep", "FR rep (max)"))
    A(row(*(["---"] * 3 + ["---:"] * 14)))
    for sc in ("H1", "H1b", "H3"):
        for N in NS:
            for sd, sm in S[sc][f"N{N}"]["per_seed_summary"].items():
                A(row(sc, N, sd, pct(sm["process_tpr"]), pct(sm["process_fpr"]), pct(sm["process_accuracy"]),
                      pct(sm["baseline_tpr"]), pct(sm["baseline_fpr"]), pct(sm["baseline_accuracy"]),
                      pct(sm.get("baseline_fpr_divergent")), pct(sm.get("baseline_tpr_bare_assertion_correct")),
                      pct(sm.get("baseline_tpr_forged_op")), pct(sm["digest_agreement"]), sm["ledger_verify"],
                      sm["label_leaks"], repf(sm["honest_rep_mean"]), repf(sm["free_rider_rep_max"])))
    inv = {f"{sc} N={N}": S[sc][f"N{N}"]["all_metrics_seed_invariant"] for sc in ("H1", "H1b", "H3") for N in NS}
    A("")
    A("Seed invariance (every summary metric identical across the 5 seeds): "
      + "; ".join(f"{k}: {v}" for k, v in inv.items()) + ".")
    A("")
    fr = cfg["free_riders_by_N"]
    A("**Reading.** " +
      f"Across N = {', '.join(str(n) for n in NS)} ({', '.join(str(fr[str(n)]) for n in NS)} free-riders in H3; "
      f"{', '.join(str(dv['divergent_agents_by_N'][str(n)]) for n in NS)} divergent agents per task in H1b) and five "
      "harness seeds the headline metrics do not move. This is expected rather than reassuring: the predicate is "
      "deterministic, validators are honest, and payloads are identical across seeds, so the seed can only change "
      "which honest validators are drawn — and every honest validator returns the same verdict. Seeds are therefore "
      "not independent replications. Per-variant counts differ with N because the four variants cycle over "
      "F × 100 attacker slots.")
    A("")

    # ------------------------------------------------------------------ illustration
    A("## 8. Exact-Artifact Agreement Illustration (Constructed; H1 and H1b Logs)")
    A("")
    for sc in ("H1", "H1b"):
        il = res["determinism_gap_illustration"][sc]
        A(f"**{sc}** ({il['predicate_fpr']['n']} honest logs):")
        A("")
        A(row("Agreement rule", "Honest logs flagged", "x/n", "Wilson 95%", "Distinct artifacts / task", "Tied pluralities"))
        A(row("---", "---:", "---:", "---:", "---:", "---:"))
        for rule, lbl in (("full_log_content_hash", "Exact artifact: full log content hash"),
                          ("reasoning_payload_hash", "Exact artifact: reasoning payload hash")):
            r_ = il[rule]
            A(row(lbl, pct(r_["flagged_honest_logs"]["p"]), xn(r_["flagged_honest_logs"]),
                  cif(r_["flagged_honest_logs"]["wilson95"]), f"{r_['mean_distinct_artifacts_per_task']:.1f}",
                  r_["tasks_with_tied_plurality"]))
        A(row("Output plurality (output-only arm)", pct(il["output_plurality_fpr"]["p"]), xn(il["output_plurality_fpr"]),
              cif(il["output_plurality_fpr"]["wilson95"]), "—", "—"))
        A(row("Process predicate", pct(il["predicate_fpr"]["p"]), xn(il["predicate_fpr"]),
              cif(il["predicate_fpr"]["wilson95"]), "—", "—"))
        A("")
        arith, other = [], []
        for rule, name in (("full_log_content_hash", "full-log-hash"), ("reasoning_payload_hash", "payload-hash")):
            r_ = il[rule]
            d = r_["mean_distinct_artifacts_per_task"]
            if d and abs(r_["flagged_honest_logs"]["p"] - (1 - 1 / d)) < 1e-12:
                arith.append(f"the {name} rate is exactly 1 − 1/{d:.0f} = {pct(1 - 1 / d)}")
            else:
                other.append(name)
        A(f"*{sc} reading.* " + il["label"] + " "
          + (("These rates are arithmetic, not empirical: " if not other else "")
             + ("with equal-sized artifact blocs per task, " + " and ".join(arith)
                + ", so " + ("they characterise" if not other else "it characterises")
                + " the rule, not any system. " if arith else "")
             + (f"The {' and '.join(other)} rate{'s' if len(other) > 1 else ''} (unequal blocs) also "
                "follow{'' if len(other) > 1 else 's'} from the construction, not from any system. ".replace(
                    "follow{'' if len(other) > 1 else 's'}", "follow" if len(other) > 1 else "follows")
                if other else ""))
          + "An exact-artifact rule flags most honest logs although all are valid derivations; the predicate flags none.")
        A("")

    # ------------------------------------------------------------------ targets
    A("## 9. Targets (Met Only if the Interval Bound Clears the Target)")
    A("")
    for sc, t in res["targets_primary"].items():
        A(f"- {sc}: " + "; ".join(f"{k} = {v}" for k, v in t.items()))
    A("")

    # ------------------------------------------------------------------ code confirmations
    mi = res["model_id_invariance"]
    vd = h3["process"]["validators"]
    A("## 10. Code-Level Confirmations")
    A("")
    A(f"- **Model-agnostic predicate.** Every log of the primary {mi['scenario']} run ({mi['logs']} logs) was "
      f"re-validated with `model_id` replaced by each of {', '.join(repr(x) for x in mi['alternative_model_ids'])}: "
      f"decision, gate results and recompute digest identical in {xn(mi['identical_decision_rules_and_digest'])} "
      "re-validations. Static check: `test_validation_and_consensus_never_branch_on_model_id` (run before every "
      "experiment by `run_experiments.sh`) asserts that `validation.py` contains no `model_id` identifier and that "
      "in `consensus.py` it occurs only inside `build_h1` (legacy demo population builder), outside the verdict "
      "and aggregation path.")
    A(f"- **Validators.** Peer sets: {vd['peer_set_pool']}. Verdicts: {vd['verdict_function']}. Empirically (H3, "
      f"N = {PN}): {vd['verdicts_cast_by_free_rider_agents']} verdicts by free-rider agents, all equal to the "
      "reference predicate.")
    A("")

    # ------------------------------------------------------------------ diagnostic
    A("## 11. Out-of-Scope Diagnostic — NOT FOR THE PAPER")
    A("")
    dg = res["diagnostic_out_of_scope_crossover"]
    A(dg["label"])
    A("")
    A(row("Wrong-answer share", "Free-riders", "Proc detection", "Proc FPR", "Proc acc", "Out acc", "Δ acc"))
    A(row("---", "---:", "---:", "---:", "---:", "---:", "---:"))
    for k, d in dg["fractions"].items():
        A(row(k.replace("pct", "%"), d["wrong_answer_free_riders"], pct(d["process_detection"]["p"]),
              pct(d["process_fpr"]["p"]), pct(d["process_accuracy"]["p"]), pct(d["baseline_accuracy"]["p"]),
              ppf(d["accuracy_delta_pp"])))
    A("")
    A("At 50% the honest and wrong-answer blocs tie in the output-only plurality; `metrics.weighted_majority` "
      "breaks the tie by ascending value, and the wrong value (answer + 10) is always larger, so the tie resolves "
      "to the correct answer by construction. Do not cite any row of this table.")
    A("")

    # ------------------------------------------------------------------ integrity
    A("## 12. Integrity and Reproducibility")
    A("")
    A(f"- Audited modules unmodified (SHA-256 vs `AUDITED_SHA256.txt`): {m['audited_modules_unmodified']}.")
    A(f"- Environment: Python {m['environment']['python']}, rfc8785 {m['environment']['rfc8785']}, "
      f"Mesa {m['environment']['Mesa']}, pytest {m['environment']['pytest']}.")
    A("- Log metadata pinned (timestamp, runtime_ms; seed and temperature null), so every `log_id` is byte-stable; "
      "the log-id set digest per scenario is in results.json. Ledger transaction timestamps are wall-clock and "
      "never enter results.")
    A("- `verify_summary.py` checks every number in this file against results.json (`summary_verification.txt`). "
      "`run_experiments.sh --verify-repro` reruns everything and compares results.json (ignoring `meta.volatile`), "
      "this file (ignoring its generation timestamp) and every other output byte-for-byte.")
    A("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(x for x in L if x is not None) + "\n")


def write_conversion_report(res: dict, conv: dict, path: str) -> None:
    rep = conv["report"]
    ds = res["meta"]["dataset"]
    L = []
    A = L.append
    A("# GSM8K Conversion Report")
    A("")
    A(f"Source: `{ds['source_url']}` (upstream commit `{ds['upstream_commit']}`, SHA-256 `{ds['file_sha256']}`, "
      f"{ds['rows_in_file']} problems, MIT licence). Dataset citation: Cobbe et al. (2021) — **[VERIFY]** "
      "(out-of-matrix).")
    A("")
    A("## Eligibility Rules")
    A("")
    A("Rules (a) and (b) are the two requested rules; (c) was **added** and is flagged; (d) is a safety net.")
    A("")
    A("- (a) every `<<expr=value>>` annotation re-executes under `validation._safe_arith` (the predicate's "
      "whitelisted AST evaluator) to exactly its recorded value (discrete canonical equality, tolerance 0);")
    A("- (b) the `####` answer (thousands separators removed) equals the last annotation's value;")
    A("- (c) **added:** every annotation step lies in the dependency cone of the last annotation under the "
      "dataflow wiring (each step depends on every earlier step whose recorded value equals a numeric literal in "
      "its expression). Without it the faithful honest log fails Process Coherence and Tool Utilization;")
    A("- (d) all five honest tiers are accepted by `validation.validate`.")
    A("")
    A("## Counts")
    A("")
    A(row("Scope", "Scanned", "Eligible", "Dropped", *[r for r in g.DROP_REASONS]))
    A(row(*(["---"] + ["---:"] * (3 + len(g.DROP_REASONS)))))
    for scope, lbl in (("selection_prefix", f"Selection prefix (indices 0–{rep['selection_prefix']['last_index_scanned']})"),
                       ("whole_file", "Whole test file")):
        c = rep[scope]
        A(row(lbl, c["scanned"], c["eligible"], c["dropped"],
              *[c["dropped_by_primary_reason"][r] for r in g.DROP_REASONS]))
    A("")
    A("Counts are by **primary** (first failing) reason. Non-exclusive counts (a problem can fail several rules):")
    A("")
    for scope in ("selection_prefix", "whole_file"):
        c = rep[scope]["failing_rule_counts_non_exclusive"]
        A(f"- {scope}: " + ", ".join(f"{k} {v}" for k, v in c.items()))
    A("")
    A("## Dropped Problems in the Selection Prefix")
    A("")
    A(row("Index", "Primary reason", "Detail"))
    A(row("---:", "---", "---"))
    last = rep["selection_prefix"]["last_index_scanned"]
    for p in conv["problems"]:
        if p.index > last or p.eligible:
            continue
        det = next((d for d in p.details if d["rule"] == p.primary_reason), {})
        det_s = ", ".join(f"{k}={v}" for k, v in det.items() if k != "rule") or "—"
        A(row(p.index, p.primary_reason, det_s.replace("|", "\\|")))
    A("")
    A("## Whole-File Value Mismatches (Rule a)")
    A("")
    A("These are honest reference computations that fail exact re-execution, mostly IEEE-754 artefacts "
      "(e.g. `6*0.1` → 0.6000000000000001 vs recorded 0.60). A live agent that records a rounded calculator value "
      "would fail C1 the same way.")
    A("")
    A(row("Index", "Expression", "Recorded", "Recomputed"))
    A(row("---:", "---", "---", "---"))
    for p in conv["problems"]:
        for d in p.details:
            if d["rule"] == g.R_MISMATCH:
                A(row(p.index, f"`{d['expr']}`", d["recorded"], d["recomputed"]))
    A("")
    A("## Tiers and Variants")
    A("")
    A("Honest tiers (all accepted on all selected problems): minimal = one merged calculator step (terminal); "
      "terse = one tool_call step per annotation, last one terminal; standard = tool_call steps + terminal decision "
      "grounded in the last op; verbose = planning inference + (inference, tool_call) per annotation + decision; "
      "evidence = non-deterministic `retriever` op read by an evidence_use step that the first computations depend "
      "on, numeric op outputs, decision.")
    A("")
    fb = sum(1 for s in ds["selected"] if s["forged_op_fallback"])
    A(f"Free-rider variants (four, round-robin): bare_assertion_wrong (adversaries.py semantics: answer − 1, no op; "
      f"C4), bare_assertion_correct (BLUEPRINT D3: correct answer, no op; C4), forged_op (correct answer; last "
      f"literal of the final expression decremented; C1; fallback `(expr)-1` used on {fb} of {len(ds['selected'])} "
      "tasks), orphan_op (correct decoy op outside the answer cone, answer + 1; Tool Utilization).")
    A("")
    dv = res["meta"]["config"]["divergence"]
    A("## H1b Divergent Derivations")
    A("")
    A(f"{dv['construction']}. Selection: {dv['assignment']}. Of {len(ds['selected'])} tasks: operand from the "
      f"question {dv['operand_in_question']}; clean values {dv['clean_values']}; wiring identical "
      f"{dv['wiring_identical']}, subset {dv['wiring_subset_value_collision']}; deltas {dv['delta_counts']}. "
      "Per-task detail in `h1b_divergent_derivations.csv`.")
    A("")
    A("## Selected Problems")
    A("")
    A(", ".join(str(s["gsm8k_index"]) for s in ds["selected"]))
    A("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
