# RESULTS_SUMMARY — P2 offline H1/H1b/H3 evaluation on real GSM8K problems

*Audit-corrected by `audit/audit_corrections.py` from `generated_RESULTS_SUMMARY.md`; every number is in `results/results.json`, the numeric authority. Generated at 2026-09-24T08:28:28Z.*

**What was run.** 100 GSM8K test problems (GSM8K, `https://github.com/openai/grade-school-math` @ `3101c7d50724`, file SHA-256 `3730d312f6e34405…`), selected as the first 100 eligible problems by index (indices 0–117). Agents are **scripted**: each log is converted mechanically from the reference solution's calculator annotations; **no LLM is in the loop** and every log records `model_id = "scripted-gsm8k-reference"`. Primary configuration: N = 30 agents, k = 3, reject supermajority 2/3, r₀ = 0.5, α = 0.10, β = 0.30, harness seed 20260629; robustness at N = 10, 20. Scenarios: **H1** all honest; **H1b** all agents honest, with 3 per task (seeded, 10.0%) deriving a wrong answer through a valid chain (BLUEPRINT D2; an attribution test, §2); **H3** 3 free-riders (10.0%) cycling round-robin through 4 variants; **H1-U** as H1 on the first 100 problems passing only rules (a)+(b) (rule (c) not applied; 94 problems shared with the primary sample, §4). All validators are honest, including free-rider agents drawn into peer sets. The harness seed changes only peer-set draws (and commitment salts); seeds are never pooled. **The selection is not neutral for H1:** rules (c) and (d) admit only problems whose five honest tiers the predicate accepts, so H1's honest-log flag count is 0 by construction; §4 measures the honest-log FPR without rule (c).

**Unit of analysis and tests.** TPR and FPR are per log; accuracy is per task. Logs within a task are not independent (same problem; honest agents of one tier emit byte-identical payloads), so log-level intervals and p-values are optimistic; task-level figures are given alongside. Both arms judge the same logs, so arm comparisons use McNemar's exact two-sided test on discordant pairs (b = flagged/correct by the process arm only, c = by the output-only arm only); it replaces the independent-sample Newcombe interval of the blueprint. Intervals are on the primary seed only. The agents of one tier emit byte-identical payloads, so the 3000 honest logs of H1 are 500 distinct payloads (100 tasks × 5 tiers); readings quote task-level intervals, and distinct-payload intervals are in `results.json` → `audit.units`.

## 1. H1 — All-Honest Baseline (N = 30, 100 tasks, 3000 logs)

| Metric | Arm | Value | x/n | Wilson 95% | Clopper–Pearson 95% |
| --- | --- | ---: | ---: | ---: | ---: |
| Consensus accuracy (tasks) | process | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Consensus accuracy (tasks) | output-only | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| FPR (honest logs flagged) | process | 0.0% | 0/3000 | [0.0, 0.1] | [0.0, 0.1] |
| FPR (honest logs flagged) | output-only | 0.0% | 0/3000 | [0.0, 0.1] | [0.0, 0.1] |
| FPR, tier *minimal* | process | 0.0% | 0/600 | [0.0, 0.6] | [0.0, 0.6] |
| FPR, tier *terse* | process | 0.0% | 0/600 | [0.0, 0.6] | [0.0, 0.6] |
| FPR, tier *standard* | process | 0.0% | 0/600 | [0.0, 0.6] | [0.0, 0.6] |
| FPR, tier *verbose* | process | 0.0% | 0/600 | [0.0, 0.6] | [0.0, 0.6] |
| FPR, tier *evidence* | process | 0.0% | 0/600 | [0.0, 0.6] | [0.0, 0.6] |
| Tasks with ≥ 1 honest log flagged | process | 0.0% | 0/100 | [0.0, 3.7] | [0.0, 3.6] |
| Digest agreement (all k digests identical) | process | 100.0% | 3000/3000 | [99.9, 100.0] | [99.9, 100.0] |

- H1-FPR confirmatory interval (97.5%, BLUEPRINT D6, unsigned): Wilson [0.0, 0.2], CP [0.0, 0.1].
- Paired comparison: FPR b = 0, c = 0, p = 1.000; accuracy b = 0, c = 0, p = 1.000 (no discordant pairs).
- Honest reputation after 3 updates: 0.6355; final after 100 updates: mean 1.0000 (min 1.0000, max 1.0000).
- Ledger `verify()`: True. Label leaks: 0 (audited scan over 3000 logs + extended scan incl. variant labels).
- Log size per tier (steps / tool ops / canonical bytes, mean [min–max]):
  - *minimal*: 1.0 [1–1] / 1.0 [1–1] / 1086 [923–1380]
  - *terse*: 3.2 [1–7] / 3.2 [1–7] / 1574 [1006–2683]
  - *standard*: 4.2 [2–8] / 3.2 [1–7] / 1863 [1181–3053]
  - *verbose*: 8.4 [4–16] / 3.2 [1–7] / 2502 [1530–4175]
  - *evidence*: 5.2 [3–9] / 4.2 [2–8] / 2075 [1510–3204]

**Reading.** The process arm flagged 0/3000 honest logs (0/500 distinct payloads; 0/100 tasks, Wilson [0.0, 3.7]). This is guaranteed by the selection (rules (c) and (d)): H1 shows that the protocol reproduces single-log decisions end to end, not that the predicate spares honest logs, which §4 measures. Both arms reach 100.0% accuracy (Wilson lower bound 96.3%). Digest agreement of 100.0% is expected rather than informative: every validator runs the same deterministic predicate on the same bytes. The output-only arm flags nothing here only because every honest log carries the correct output.

## 2. H1b — Honest Error Versus Free-Riding: An Attribution Test (N = 30, 3000 Logs, 300 Honest-Error Logs)

H1b is not a false-positive test comparable to H1. Per task, 3 of the 30 honest agents (seeded, 10.0%) derive a **wrong** answer through a valid chain: one operand of the reference derivation is changed and every downstream calculator step is recomputed (BLUEPRINT D2). These *honest-error* logs are not attacks, but their answers are wrong, so a flag on them is correct for answer quality and a misattribution for free-rider detection. On single-answer tasks honest divergence can only appear as error; a valid minority answer (multi-answer tasks) is not exercised.

| Metric | Arm | Value | x/n | Wilson 95% | Clopper–Pearson 95% |
| --- | --- | ---: | ---: | ---: | ---: |
| Flag rate, honest-error logs (valid chain, wrong answer) | process | 0.0% | 0/300 | [0.0, 1.3] | [0.0, 1.2] |
| Flag rate, honest-error logs (valid chain, wrong answer) | output-only | 100.0% | 300/300 | [98.7, 100.0] | [98.8, 100.0] |
| Flag rate, honest logs with the correct answer | process | 0.0% | 0/2700 | [0.0, 0.1] | [0.0, 0.1] |
| Flag rate, honest logs with the correct answer | output-only | 0.0% | 0/2700 | [0.0, 0.1] | [0.0, 0.1] |
| Flag rate, all logs | process | 0.0% | 0/3000 | [0.0, 0.1] | [0.0, 0.1] |
| Flag rate, all logs | output-only | 10.0% | 300/3000 | [9.0, 11.1] | [8.9, 11.1] |
| Tasks with ≥ 1 honest-error log flagged | process | 0.0% | 0/100 | [0.0, 3.7] | [0.0, 3.6] |
| Tasks with ≥ 1 honest-error log flagged | output-only | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Consensus accuracy (tasks) | process | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Consensus accuracy (tasks) | output-only | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Digest agreement | process | 100.0% | 3000/3000 | [99.9, 100.0] | [99.9, 100.0] |

**Attribution across H1b and H3** (same 100 problems, N = 30, primary seed; each free-rider row pools two variants of 75 logs):

| Log class | Logs | Process arm flagged | Output-only arm flagged |
| --- | ---: | ---: | ---: |
| Honest, correct answer (H1b) | 2700 | 0/2700 (0.0%) | 0/2700 (0.0%) |
| Honest, wrong answer (H1b) | 300 | 0/300 (0.0%) | 300/300 (100.0%) |
| Free-rider, wrong answer (H3: bare_assertion_wrong + orphan_op) | 150 | 150/150 (100.0%) | 150/150 (100.0%) |
| Free-rider, correct answer (H3: bare_assertion_correct + forged_op) | 150 | 150/150 (100.0%) | 0/150 (0.0%) |

- The output-only arm flags a log exactly when its answer is wrong: 6000/6000 logs of H1b and H3.
- Paired, task level: tasks with ≥ 1 honest-error log flagged b = 0, c = 100, p < 0.001; accuracy b = 0, c = 0, p = 1.000.
- Construction of the divergent derivations (one per task, rendered in each divergent agent's own tier): operand taken from the question text in 90/100 tasks (in 10 it is a constant not in the question); change of +1 in 88/100; integer or ≤ 2-decimal values in 98/100; dependency wiring identical to the reference in 98/100 (the other 2 drop one spurious link caused by two reference steps with equal values); more than one step recomputed in 44/100. Per-task detail: `h1b_divergent_derivations.csv`.
- All 300 honest-error logs carry a wrong output (0 correct). Vote arithmetic per task: ≥ 27 correct outputs against one wrong-answer bloc of ≤ 3.
- Ledger `verify()`: True. Label leaks: 0.

**Reading.** Output voting responds only to whether an answer is right. It flags 300/300 honest-error logs and 150/150 wrong-answer free-riders alike, and passes 150/150 correct-answer free-riders, so it cannot tell honest error from free-riding. The process arm separates them: it accepts the honest-error logs, whose chains re-execute and are grounded (0/300 flagged), and rejects every free-rider log whatever its answer (300/300). The accepted wrong answers enter aggregation and are outvoted (100.0% accuracy in both arms), so the process arm is not an answer-quality filter: were honest error the plurality, it would carry the wrong answer without a flag. Under a free-rider-detection objective the output-only flags on honest error are misattributions; this is the attribution half of seminar comment (b). The divergence is constructed (one changed operand per task), not observed from a model.

## 3. H3 — Free-Riding at 10.0% (N = 30, 3 free-riders, 300 of 3000 logs)

| Metric | Arm | Value | x/n | Wilson 95% | Clopper–Pearson 95% |
| --- | --- | ---: | ---: | ---: | ---: |
| Detection TPR (attacker logs flagged) | process | 100.0% | 300/300 | [98.7, 100.0] | [98.8, 100.0] |
| Detection TPR (attacker logs flagged) | output-only | 50.0% | 150/300 | [44.4, 55.6] | [44.2, 55.8] |
| FPR (honest logs flagged) | process | 0.0% | 0/2700 | [0.0, 0.1] | [0.0, 0.1] |
| FPR (honest logs flagged) | output-only | 0.0% | 0/2700 | [0.0, 0.1] | [0.0, 0.1] |
| Consensus accuracy (tasks) | process | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Consensus accuracy (tasks) | output-only | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Flagged logs excluded from aggregate | process | 100.0% | 300/300 | [98.7, 100.0] | [98.8, 100.0] |
| Tasks with all attacker logs flagged | process | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Tasks with all attacker logs flagged | output-only | 0.0% | 0/100 | [0.0, 3.7] | [0.0, 3.6] |
| Digest agreement | process | 100.0% | 3000/3000 | [99.9, 100.0] | [99.9, 100.0] |

- Comparative accuracy (process − output-only): +0.0 pp (b = 0, c = 0, p = 1.000).
- Comparative detection, task level: all attacker logs flagged +100.0 pp (b = 100, c = 0, p < 0.001); at least one attacker log flagged +0.0 pp (process 100/100, output-only 100/100; b = 0, c = 0, p = 1.000: every task has at least one wrong-answer free-rider). Pooled logs: +50.0 pp (150 discordant, all process-only; log-level p not cited).
- H3-TPR confirmatory interval (97.5%, D6 unsigned): Wilson [98.4, 100.0], CP [98.5, 100.0].

**Per variant** (process arm = supermajority reject; output-only arm = dissent from the task plurality; McNemar on the variant's logs):

| Variant | Logs | Correct output | Process TPR | Process Wilson 95% | Output-only TPR | Output-only Wilson 95% | Δ detection | McNemar (logs) | Intended gate failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| bare_assertion_wrong | 75 | 0 | 100.0% | [95.1, 100.0] | 100.0% | [95.1, 100.0] | +0.0 pp | b = 0, c = 0, p = 1.000 | C4: 75/75 |
| bare_assertion_correct | 75 | 75 | 100.0% | [95.1, 100.0] | 0.0% | [0.0, 4.9] | +100.0 pp | b = 75, c = 0, p < 0.001 | C4: 75/75 |
| forged_op | 75 | 75 | 100.0% | [95.1, 100.0] | 0.0% | [0.0, 4.9] | +100.0 pp | b = 75, c = 0, p < 0.001 | C1: 75/75 |
| orphan_op | 75 | 0 | 100.0% | [95.1, 100.0] | 100.0% | [95.1, 100.0] | +0.0 pp | b = 0, c = 0, p = 1.000 | tool_utilization: 75/75 |

Gate attribution (rejected logs failing each gate / sub-check):

| Variant | Rejected | Process coherence | Tool utilization | Causal sufficiency | C1 | C2 | C3 | C4 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bare_assertion_wrong | 75 | 0 | 0 | 75 | 0 | 0 | 0 | 75 |
| bare_assertion_correct | 75 | 0 | 0 | 75 | 0 | 0 | 0 | 75 |
| forged_op | 75 | 0 | 0 | 75 | 75 | 75 | 0 | 75 |
| orphan_op | 75 | 75 | 75 | 75 | 0 | 0 | 0 | 75 |

- Reputation after 3 updates: honest 0.6355; free-rider 0.1715.
- Final reputation after 100 updates: honest mean 1.0000 (min 1.0000); free-riders 0.0000, 0.0000, 0.0000 (floor 1e-06); separation (min honest − max free-rider) 1.0000.
- Validators: free-rider agents cast 890 of 9000 verdicts, on 827 logs (64 of them attacker logs). All validators run the same honest predicate by construction (Byzantine validators are H4, future work), so agreement with a separate call of that predicate (827/827 logs; every verdict on every log, 3000/3000) is expected. An independent re-implementation of the gates agrees with all 9000 revealed verdicts (0 disagreements; §13).
- Honest-log gate failures: 0. Ledger `verify()`: True. Label leaks: 0.
- Vote arithmetic per task: ≥ 28 logs carry the correct output; no wrong output is shared by more than 1 log (≤ 3 attacker logs per task).
- Gate carrying detection: C4 (answer grounded in a re-executed operation) fails on 300/300 attacker logs; process coherence and tool utilization fail only on the orphan_op logs, which C4 also rejects (0 logs caught by them alone).
- Units: the 300 attacker logs (one per variant per task) are 258 distinct payloads, because the predicate never reads the question and an assertion of the same value on two tasks is the same payload; the 2700 honest logs are 500 distinct payloads. Honest FPR 0/500 distinct payloads (Wilson [0.0, 0.8]), 0/100 tasks (Wilson [0.0, 3.7]). The 150 correct-answer free-rider logs (bare_assertion_correct + forged_op) are 135 distinct payloads: the process arm flags 135/135 (Wilson [97.2, 100.0]), the output-only arm 0/135 (Wilson [0.0, 2.8]).

**Reading.** The process arm flagged 300/300 free-rider logs (every attacker log flagged in 100/100 tasks, Wilson lower bound 96.3%) and 0/2700 honest logs, and kept 300/300 flagged logs out of aggregation. Every rejected log failed its variant's intended gate, and all 300 also fail C4. Free-rider reputation fell to the floor (1e-06) while honest reputation rose, but only as a ledger write: the run is a single batch and aggregation weights every author at r₀ = 0.5, so reputation never influenced a consensus value here. Output matching misses bare_assertion_correct and forged_op entirely (0/75 and 0/75, all with the correct answer): the false-negative leg of seminar comment (b). Consensus accuracy is 100.0% in both arms (+0.0 pp): at least 28 of 30 logs per task carry the correct output and no wrong output is shared by more than 1 log, so at this fraction the protocol adds detection, exclusion and a reputation penalty, not accuracy. Detection is 100% by construction: each variant was built to violate a gate, the predicate is deterministic and the validators are honest. It is not an estimate of robustness to adaptive free-riders: an audit probe that wraps the copied answer in a trivial calculator operation is accepted on 100/100 tasks (§13).

## 4. Honest-Log FPR Without the Added Rule (c)

The primary sample is selected with rule (c) and the rule-(d) safety net, which admit only problems whose five honest tiers the predicate accepts. This section is the non-circular measurement of how the predicate treats faithful honest logs: rule (c) is **not** applied.

**Table 4a. Whole test file: every problem passing rules (a)+(b) (1199 problems), one faithful log per problem and tier (5995 logs).**

| Metric | Unit | Value | x/n | Wilson 95% | Clopper–Pearson 95% |
| --- | --- | ---: | ---: | ---: | ---: |
| Honest-log FPR, all tiers | log | 3.5% | 208/5995 | [3.0, 4.0] | [3.0, 4.0] |
| Honest-log FPR, tier *minimal* | log | 0.0% | 0/1199 | [0.0, 0.3] | [0.0, 0.3] |
| Honest-log FPR, tier *terse* | log | 4.3% | 52/1199 | [3.3, 5.6] | [3.3, 5.6] |
| Honest-log FPR, tier *standard* | log | 4.3% | 52/1199 | [3.3, 5.6] | [3.3, 5.6] |
| Honest-log FPR, tier *verbose* | log | 4.3% | 52/1199 | [3.3, 5.6] | [3.3, 5.6] |
| Honest-log FPR, tier *evidence* | log | 4.3% | 52/1199 | [3.3, 5.6] | [3.3, 5.6] |
| Problems with ≥ 1 tier rejected | problem | 4.3% | 52/1199 | [3.3, 5.6] | [3.3, 5.6] |

- Targets (< 5% on the Wilson upper bound): pooled over tiers met ([3.0, 4.0]); per tier recording the full annotated derivation *terse* not met ([3.3, 5.6]); *standard* not met ([3.3, 5.6]); *verbose* not met ([3.3, 5.6]); *evidence* not met ([3.3, 5.6]); problem level not met ([3.3, 5.6]). The pooled rate is diluted by the *minimal* tier, which records only the answer's dependency cone and so cannot be rejected; the per-tier rows are the binding ones.

**Table 4b. End-to-end protocol run H1-U (N = 30, primary seed).** Sample: the first 100 problems passing (a)+(b) (indices 0–110), i.e. the primary selection rule without rule (c). It shares 94 problems with the primary sample: it adds the rule-(c) problems 15, 37, 52, 62, 84, 92 and drops 111, 112, 113, 115, 116, 117.

| Metric | Arm | Value | x/n | Wilson 95% | Clopper–Pearson 95% |
| --- | --- | ---: | ---: | ---: | ---: |
| FPR, all honest logs | process | 4.8% | 144/3000 | [4.1, 5.6] | [4.1, 5.6] |
| FPR, all honest logs | output-only | 0.0% | 0/3000 | [0.0, 0.1] | [0.0, 0.1] |
| FPR, distinct honest payloads | process | 4.8% | 24/500 | [3.2, 7.0] | [3.1, 7.1] |
| Tasks with ≥ 1 honest log flagged | process | 6.0% | 6/100 | [2.8, 12.5] | [2.2, 12.6] |
| FPR, logs on rule-(c) problems | process | 80.0% | 144/180 | [73.6, 85.2] | [73.4, 85.6] |
| FPR, logs on other problems | process | 0.0% | 0/2820 | [0.0, 0.1] | [0.0, 0.1] |
| FPR on rule-(c) problems, tier *minimal* | process | 0.0% | 0/36 | [0.0, 9.6] | [0.0, 9.7] |
| FPR on rule-(c) problems, tier *terse* | process | 100.0% | 36/36 | [90.4, 100.0] | [90.3, 100.0] |
| FPR on rule-(c) problems, tier *standard* | process | 100.0% | 36/36 | [90.4, 100.0] | [90.3, 100.0] |
| FPR on rule-(c) problems, tier *verbose* | process | 100.0% | 36/36 | [90.4, 100.0] | [90.3, 100.0] |
| FPR on rule-(c) problems, tier *evidence* | process | 100.0% | 36/36 | [90.4, 100.0] | [90.3, 100.0] |
| Rule-(c) problems with ≥ 1 log flagged | process | 100.0% | 6/6 | [61.0, 100.0] | [54.1, 100.0] |
| Consensus accuracy (tasks) | process | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |
| Consensus accuracy (tasks) | output-only | 100.0% | 100/100 | [96.3, 100.0] | [96.4, 100.0] |

- Paired comparison (H1-U), task level: b = 6, c = 0, p = 0.031.
- Targets (< 5%): not met at any unit — log [4.1, 5.6], distinct payload [3.2, 7.0], task [2.8, 12.5]. The log-level interval is too narrow: the agents of one tier emit byte-identical logs.
- Seed invariance of H1-U over the 5 harness seeds: True.

**What fails.** Over the 208 rejected faithful logs: process coherence 208, tool utilization 208, causal sufficiency 0, C1 0, C2 0, C3 0, C4 0. In every rejected log the answer chain re-executes and is grounded; what fails is that at least one recorded calculator result has no recorded consumer. In the references this happens when the solution continues its arithmetic in prose or in an equation without an annotation (index 265: the house price 350000 + 17500 + 42000 is summed in prose, so both fee computations lack a recorded consumer), restates a prose result as an identity annotation (index 52: 7.5 / .5 appears only as `<<15=15>>`; 21 of the 52 problems contain one), or computes both options of a comparison and keeps one (index 15). Index 489 was a converter defect, not an incomplete record: a negative intermediate result was never wired to its consumer. It is fixed in this run (audit D8) and the problem is now eligible. Full examples: `rule_c_examples.md`. Every rejection falls on a rule-(c) problem (checked in code, and by an independent re-implementation of the predicate, §13).

| GSM8K index | Annotations | Steps outside answer cone | Identity ann. | Tiers rejected | Tier accepted | Gates failed (every rejected tier) |
| ---: | ---: | --- | --- | --- | --- | --- |
| 15 | 3 | 1 | yes | 4 | minimal | process_coherence + tool_utilization |
| 37 | 4 | 0, 1, 2 | no | 4 | minimal | process_coherence + tool_utilization |
| 52 | 3 | 0, 1 | yes | 4 | minimal | process_coherence + tool_utilization |
| 62 | 2 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 84 | 3 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 92 | 2 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 119 | 4 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 186 | 4 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 189 | 3 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 209 | 4 | 0, 1 | yes | 4 | minimal | process_coherence + tool_utilization |
| 214 | 2 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 265 | 3 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 311 | 4 | 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 313 | 3 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 358 | 3 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 367 | 3 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 407 | 4 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 494 | 3 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 500 | 5 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 505 | 4 | 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 580 | 3 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 585 | 5 | 1, 2 | no | 4 | minimal | process_coherence + tool_utilization |
| 594 | 3 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 622 | 4 | 0, 1, 2 | no | 4 | minimal | process_coherence + tool_utilization |
| 637 | 3 | 0, 1 | yes | 4 | minimal | process_coherence + tool_utilization |
| 652 | 6 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 672 | 4 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 675 | 4 | 0, 1, 2 | no | 4 | minimal | process_coherence + tool_utilization |
| 687 | 5 | 1 | yes | 4 | minimal | process_coherence + tool_utilization |
| 726 | 2 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 737 | 3 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 753 | 4 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 754 | 6 | 3 | yes | 4 | minimal | process_coherence + tool_utilization |
| 772 | 6 | 0, 1, 2, 3, 4 | yes | 4 | minimal | process_coherence + tool_utilization |
| 802 | 2 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 809 | 2 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 815 | 5 | 0, 2, 3 | yes | 4 | minimal | process_coherence + tool_utilization |
| 831 | 5 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 884 | 2 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 935 | 4 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 969 | 5 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 1014 | 5 | 0, 1, 2, 3 | no | 4 | minimal | process_coherence + tool_utilization |
| 1022 | 3 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 1042 | 5 | 0, 1, 2, 3 | no | 4 | minimal | process_coherence + tool_utilization |
| 1045 | 2 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 1075 | 6 | 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 1095 | 3 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 1100 | 3 | 0 | no | 4 | minimal | process_coherence + tool_utilization |
| 1137 | 4 | 0, 1 | no | 4 | minimal | process_coherence + tool_utilization |
| 1161 | 6 | 0, 1 | yes | 4 | minimal | process_coherence + tool_utilization |
| 1190 | 2 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |
| 1200 | 2 | 0 | yes | 4 | minimal | process_coherence + tool_utilization |

Not evaluated: problems failing rule (a) (value mismatch / not executable) or rule (b) (answer not the last calculator result) were NOT converted or evaluated: a faithful log for them is not uniquely defined, so this FPR covers only problems passing (a)+(b).

**Reading.** Without rule (c) the predicate rejects 208/5995 faithful honest logs over the whole file (pooled 3.5%, Wilson [3.0, 4.0]); for each tier that records the full annotated derivation the rate is 4.3% ([3.3, 5.6]), whose upper bound exceeds the 5% target. These logs are faithful to the reference's calculator annotations, which are an incomplete record of the derivation; an agent that recorded its prose steps would pass. The cost falls on logs that record more: the *minimal* tier is never rejected. Inside the protocol, H1-U flags 144/3000 honest logs (4.8%), all on its 6 rule-(c) problems; it differs from the whole-file rate because its sample contains rule-(c) problems at 6.0% against 4.3% in the whole file, whose figure is the population estimate. Both come from reference solutions, not from a model, so neither is a live-LLM rate.

## 5. Paired Arm Comparisons (McNemar Exact, Primary Configuration)

| Scenario | Comparison | Unit | n | Process positive | Output-only positive | b | c | p |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| H1 | correct consensus | task | 100 | 100 | 100 | 0 | 0 | = 1.000 |
| H1 | honest log flagged | log | 3000 | 0 | 0 | 0 | 0 | = 1.000 |
| H1 | any honest log flagged | task | 100 | 0 | 0 | 0 | 0 | = 1.000 |
| H1b | correct consensus | task | 100 | 100 | 100 | 0 | 0 | = 1.000 |
| H1b | flagged, all logs (incl. honest error) | log | 3000 | 0 | 300 | 0 | 300 | < 0.001 |
| H1b | any log flagged (= any honest-error log) | task | 100 | 0 | 100 | 0 | 100 | < 0.001 |
| H1b | honest-error log flagged | log | 300 | 0 | 300 | 0 | 300 | < 0.001 |
| H3 | correct consensus | task | 100 | 100 | 100 | 0 | 0 | = 1.000 |
| H3 | honest log flagged | log | 2700 | 0 | 0 | 0 | 0 | = 1.000 |
| H3 | any honest log flagged | task | 100 | 0 | 0 | 0 | 0 | = 1.000 |
| H3 | attacker log flagged | log | 300 | 300 | 150 | 150 | 0 | < 0.001 |
| H3 | all attacker logs flagged | task | 100 | 100 | 0 | 100 | 0 | < 0.001 |
| H3 | any attacker log flagged | task | 100 | 100 | 100 | 0 | 0 | = 1.000 |
| H3 | bare_assertion_wrong flagged | log | 75 | 75 | 75 | 0 | 0 | = 1.000 |
| H3 | all bare_assertion_wrong logs flagged | task | 75 | 75 | 75 | 0 | 0 | = 1.000 |
| H3 | bare_assertion_correct flagged | log | 75 | 75 | 0 | 75 | 0 | < 0.001 |
| H3 | all bare_assertion_correct logs flagged | task | 75 | 75 | 0 | 75 | 0 | < 0.001 |
| H3 | forged_op flagged | log | 75 | 75 | 0 | 75 | 0 | < 0.001 |
| H3 | all forged_op logs flagged | task | 75 | 75 | 0 | 75 | 0 | < 0.001 |
| H3 | orphan_op flagged | log | 75 | 75 | 75 | 0 | 0 | = 1.000 |
| H3 | all orphan_op logs flagged | task | 75 | 75 | 75 | 0 | 0 | = 1.000 |
| H1-U | correct consensus | task | 100 | 100 | 100 | 0 | 0 | = 1.000 |
| H1-U | honest log flagged | log | 3000 | 144 | 0 | 144 | 0 | < 0.001 |
| H1-U | any honest log flagged | task | 100 | 6 | 0 | 6 | 0 | = 0.031 |

p is exact two-sided; '< 0.001' is the canonical form (exact values in results.json and `mcnemar.csv`). With b + c = 0 the test is uninformative (p = 1 by convention). Cite only task-level rows: logs within a task are not independent and the honest logs of one tier are byte-identical. Every discordance here is deterministic by construction, so a p-value measures the size of a designed effect, not evidence that it generalises.

## 6. Verification Cost (Counts, Not Latency; Primary Seed)

| Scenario | N | Logs | Peer-set records | Verdicts | Commits | Reveals | Seals | Reputation updates | Protocol ledger tx / log | All ledger tx / log | Walking-skeleton overhead ops |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| H1 | 30 | 3000 | 3000 | 9000 | 9000 | 9000 | 3000 | 3000 | 9.00 | 10.01 | 33000 |
| H1 | 10 | 1000 | 1000 | 3000 | 3000 | 3000 | 1000 | 1000 | 9.00 | 10.01 | 11000 |
| H1 | 20 | 2000 | 2000 | 6000 | 6000 | 6000 | 2000 | 2000 | 9.00 | 10.01 | 22000 |
| H1b | 30 | 3000 | 3000 | 9000 | 9000 | 9000 | 3000 | 3000 | 9.00 | 10.01 | 33000 |
| H1b | 10 | 1000 | 1000 | 3000 | 3000 | 3000 | 1000 | 1000 | 9.00 | 10.01 | 11000 |
| H1b | 20 | 2000 | 2000 | 6000 | 6000 | 6000 | 2000 | 2000 | 9.00 | 10.01 | 22000 |
| H3 | 30 | 3000 | 3000 | 9000 | 9000 | 9000 | 3000 | 3000 | 9.00 | 10.01 | 33000 |
| H3 | 10 | 1000 | 1000 | 3000 | 3000 | 3000 | 1000 | 1000 | 9.00 | 10.01 | 11000 |
| H3 | 20 | 2000 | 2000 | 6000 | 6000 | 6000 | 2000 | 2000 | 9.00 | 10.01 | 22000 |
| H1-U | 30 | 3000 | 3000 | 9000 | 9000 | 9000 | 3000 | 3000 | 9.00 | 10.01 | 33000 |

Output-only arm: 0 validation operations (one plurality per task). Per log, the process arm performs k = 3 predicate evaluations (verdicts), 3 commits, 3 reveals, 1 seal, 1 peer-set record and 1 reputation write. "Walking-skeleton overhead ops" = `peer_set_assignments + verdicts_computed + commits + reveals + seals` — the formula behind the earlier "+165 ops" figure (15 logs × 11); the "150" figure omits the 15 peer-set records. Counted directly from the H3 (N = 30) chain by the audit: 27000 protocol transactions and 30032 in all over 3000 logs (9.00 and 10.01 per log), which matches the table's protocol and all-ledger columns; the walking-skeleton count (33000) is not a ledger count, since it includes the 9000 off-ledger verdict computations and excludes the 3000 reputation writes.

Steps, tool operations and canonical bytes per log (H3 primary; divergent logs from H1b):

| Log class | Steps (mean) | Tool ops (mean) | Bytes (mean) |
| --- | ---: | ---: | ---: |
| honest / minimal | 1.0 | 1.0 | 1086 |
| honest / terse | 3.2 | 3.2 | 1574 |
| honest / standard | 4.2 | 3.2 | 1863 |
| honest / verbose | 8.4 | 3.2 | 2502 |
| honest / evidence | 5.2 | 4.2 | 2075 |
| honest divergent (H1b, all tiers) | 4.4 | 3.0 | 1808 |
| free-rider / bare_assertion_wrong | 2.0 | 0.0 | 1065 |
| free-rider / bare_assertion_correct | 2.0 | 0.0 | 1074 |
| free-rider / forged_op | 3.0 | 1.0 | 1316 |
| free-rider / orphan_op | 3.0 | 1.0 | 1290 |

**Reading.** Protocol ledger transactions per log are the same in every scenario and population (9.00); the rest of the chain is one log commit per log plus per-run genesis and phase records, so cost grows linearly with the number of logs. Detection does not track step count: every honest *minimal* log has 1 step, fewer than any free-rider log (at least 2), and all minimal logs are accepted. It does coincide with tool-operation count for the two bare-assertion variants, the only logs with no tool operation (every honest log has at least 1).

## 7. Robustness: Population Size × Harness Seed

| Scenario | N | Seed | Proc TPR | Proc flag (non-attack) | Proc acc | Out TPR | Out flag (non-attack) | Out acc | Out flag (honest error) | Out TPR bare-correct | Out TPR forged-op | Digest agr. | verify() | Leaks | Honest rep | FR rep (max) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| H1 | 30 | 20260629 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 30 | 20260630 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 30 | 20260631 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 30 | 20260632 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 30 | 20260633 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 10 | 20260629 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 10 | 20260630 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 10 | 20260631 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 10 | 20260632 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 10 | 20260633 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 20 | 20260629 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 20 | 20260630 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 20 | 20260631 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 20 | 20260632 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1 | 20 | 20260633 | n/a | 0.0% | 100.0% | n/a | 0.0% | 100.0% | n/a | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 30 | 20260629 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 30 | 20260630 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 30 | 20260631 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 30 | 20260632 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 30 | 20260633 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 10 | 20260629 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 10 | 20260630 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 10 | 20260631 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 10 | 20260632 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 10 | 20260633 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 20 | 20260629 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 20 | 20260630 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 20 | 20260631 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 20 | 20260632 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H1b | 20 | 20260633 | n/a | 0.0% | 100.0% | n/a | 10.0% | 100.0% | 100.0% | n/a | n/a | 100.0% | True | 0 | 1.0000 | n/a |
| H3 | 30 | 20260629 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 30 | 20260630 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 30 | 20260631 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 30 | 20260632 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 30 | 20260633 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 10 | 20260629 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 10 | 20260630 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 10 | 20260631 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 10 | 20260632 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 10 | 20260633 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 20 | 20260629 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 20 | 20260630 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 20 | 20260631 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 20 | 20260632 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |
| H3 | 20 | 20260633 | 100.0% | 0.0% | 100.0% | 50.0% | 0.0% | 100.0% | n/a | 0.0% | 0.0% | 100.0% | True | 0 | 1.0000 | 0.0000 |

Non-attack flag rates are FPRs for H1 and H3; for H1b they include the 10% honest-error logs and are not FPRs (§2).

Seed invariance (every summary metric identical across the 5 seeds): H1 N=30: True; H1 N=10: True; H1 N=20: True; H1b N=30: True; H1b N=10: True; H1b N=20: True; H3 N=30: True; H3 N=10: True; H3 N=20: True.

**Reading.** Across N = 30, 10, 20 (3, 1, 2 free-riders in H3; 3, 1, 2 divergent agents per task in H1b) and five harness seeds the headline metrics do not move. This is expected rather than reassuring: the predicate is deterministic, validators are honest, and payloads are identical across seeds, so the seed can only change which honest validators are drawn — and every honest validator returns the same verdict. Seeds are therefore not independent replications. Per-variant counts differ with N because the four variants cycle over F × 100 attacker slots.

## 8. Exact-Artifact Agreement Illustration (Constructed; H1 and H1b Logs)

**H1** (3000 honest logs):

| Agreement rule | Honest logs flagged | x/n | Wilson 95% | Distinct artifacts / task | Tied pluralities |
| --- | ---: | ---: | ---: | ---: | ---: |
| Exact artifact: full log content hash | 96.7% | 2900/3000 | [96.0, 97.3] | 30.0 | 100 |
| Exact artifact: reasoning payload hash | 80.0% | 2400/3000 | [78.5, 81.4] | 5.0 | 100 |
| Output plurality (output-only arm) | 0.0% | 0/3000 | [0.0, 0.1] | — | — |
| Process predicate | 0.0% | 0/3000 | [0.0, 0.1] | — | — |

*H1 reading.* CONSTRUCTED ILLUSTRATION, not a measurement: the only honest divergence in this scripted population is the five effort tiers (agents of one tier emit byte-identical payloads). full_log_content_hash is the literal rule (every log embeds its author DID, so every hash is unique); reasoning_payload_hash compares reasoning_steps + tool_operations + final_output only. Ties are broken by metrics.weighted_majority's deterministic order. These rates are arithmetic, not empirical: with equal-sized artifact blocs per task, the full-log-hash rate is exactly 1 − 1/30 = 96.7% and the payload-hash rate is exactly 1 − 1/5 = 80.0%, so they characterise the rule, not any system. An exact-artifact rule flags most honest logs although all are valid derivations; the predicate flags none.

**H1b** (3000 non-attack logs, 300 of them honest error):

| Agreement rule | Non-attack logs flagged | x/n | Wilson 95% | Distinct artifacts / task | Tied pluralities |
| --- | ---: | ---: | ---: | ---: | ---: |
| Exact artifact: full log content hash | 96.7% | 2900/3000 | [96.0, 97.3] | 30.0 | 100 |
| Exact artifact: reasoning payload hash | 80.0% | 2400/3000 | [78.5, 81.4] | 7.5 | 100 |
| Output plurality (output-only arm) | 10.0% | 300/3000 | [9.0, 11.1] | — | — |
| Process predicate | 0.0% | 0/3000 | [0.0, 0.1] | — | — |

*H1b reading.* CONSTRUCTED ILLUSTRATION, not a measurement: the only honest divergence in this scripted population is the five effort tiers plus 300 validly derived divergent-output logs (3 of 30 per task, BLUEPRINT D2). full_log_content_hash is the literal rule (every log embeds its author DID, so every hash is unique); reasoning_payload_hash compares reasoning_steps + tool_operations + final_output only. Ties are broken by metrics.weighted_majority's deterministic order. With equal-sized artifact blocs per task, the full-log-hash rate is exactly 1 − 1/30 = 96.7%, so it characterises the rule, not any system. The payload-hash rate (unequal blocs) also follows from the construction, not from any system. An exact-artifact rule flags most honest logs although all are valid derivations; the predicate flags none.

## 9. Targets (Met Only if the Interval Bound Clears the Target)

- H1: accuracy_wilson_lower_ge_95 = True; accuracy_cp_lower_ge_95 = True; fpr_wilson_upper_lt_5 = True; fpr_cp_upper_lt_5 = True; fpr_task_level_wilson_upper_lt_5 = True; fpr_distinct_payload_wilson_upper_lt_5 = True
- H1b: accuracy_wilson_lower_ge_95 = True; accuracy_cp_lower_ge_95 = True; fpr_target_not_applicable = attribution test: honest-error logs have wrong answers (section 2)
- H3: accuracy_wilson_lower_ge_95 = True; accuracy_cp_lower_ge_95 = True; fpr_wilson_upper_lt_5 = True; fpr_cp_upper_lt_5 = True; detection_wilson_lower_ge_90 = True; detection_cp_lower_ge_90 = True; per_variant_detection_wilson_lower_ge_90 = {'bare_assertion_wrong': True, 'bare_assertion_correct': True, 'forged_op': True, 'orphan_op': True}; fpr_task_level_wilson_upper_lt_5 = True; fpr_distinct_payload_wilson_upper_lt_5 = True; detection_task_level_all_attackers_wilson_lower_ge_90 = True
- H1U: accuracy_wilson_lower_ge_95 = True; accuracy_cp_lower_ge_95 = True; fpr_wilson_upper_lt_5 = False; fpr_cp_upper_lt_5 = False; fpr_task_level_wilson_upper_lt_5 = False; fpr_distinct_payload_wilson_upper_lt_5 = False
- rule_c_unfiltered_whole_file: fpr_wilson_upper_lt_5 = True; fpr_cp_upper_lt_5 = True; per_tier_wilson_upper_lt_5 = {'minimal': True, 'terse': False, 'standard': False, 'verbose': False, 'evidence': False}; problem_level_wilson_upper_lt_5 = False

Met only if the interval bound clears the target. The whole-file pooled rate is diluted by the minimal tier, which records only the answer's dependency cone and cannot be rejected; the per-tier and problem-level rows are the binding ones. H1's honest-log FPR is 0 by construction of the sample.

## 10. Code-Level Confirmations

- **Model-agnostic predicate.** Every log of the primary H3 run (3000 logs) was re-validated with `model_id` replaced by each of 'llama3.1:8b', 'any-other-model': decision, gate results and recompute digest identical in 6000/6000 re-validations. Static check: `test_validation_and_consensus_never_branch_on_model_id` (run before every experiment by `run_experiments.sh`) asserts that `validation.py` contains no `model_id` identifier and that in `consensus.py` it occurs only inside `build_h1` (legacy demo population builder), outside the verdict and aggregation path. This shows that the predicate and the consensus decision never read `model_id`; it does not show that logs written by other models meet the schema, which needs the live run (future work).
- **Validators.** Peer sets: all agents except the log's author (consensus.assign_peer_sets); free-rider agents are eligible. Verdicts: consensus.compute_log_verdicts calls validation.validate_committed for every validator; the validator id is a label on the Verdict, never an input to the decision; consensus has no access to behavior_class. Empirically (H3, N = 30): 890 verdicts by free-rider agents, all equal to the reference predicate. All validators are honest by construction; Byzantine validators (H4) are future work.

## 11. Out-of-Scope Diagnostic — NOT FOR THE PAPER

OUT OF SCOPE -- NOT FOR THE PAPER (fact sheet sec 5; BLUEPRINT D10). Wrong-answer free-riders sharing one wrong value (answer + 10), N = 30, primary seed.

| Wrong-answer share | Free-riders | Proc detection | Proc FPR | Proc acc | Out acc | Δ acc |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 10% | 3 | 100.0% | 0.0% | 100.0% | 100.0% | +0.0 pp |
| 30% | 9 | 100.0% | 0.0% | 100.0% | 100.0% | +0.0 pp |
| 50% | 15 | 100.0% | 0.0% | 100.0% | 100.0% | +0.0 pp |
| 60% | 18 | 100.0% | 0.0% | 100.0% | 0.0% | +100.0 pp |

At 50% the honest and wrong-answer blocs tie in the output-only plurality; `metrics.weighted_majority` breaks the tie by ascending value, and the wrong value (answer + 10) is always larger, so the tie resolves to the correct answer by construction. Do not cite any row of this table.

## 12. Integrity and Reproducibility

- Audited modules unmodified (SHA-256 vs `AUDITED_SHA256.txt`): {'sim_ledger.py': True, 'agents.py': True, 'validation.py': True, 'consensus.py': True, 'metrics.py': True}.
- Environment: Python 3.12.3, rfc8785 0.1.4, Mesa 3.5.1, pytest 9.1.1.
- Log metadata pinned (timestamp, runtime_ms; seed and temperature null), so every `log_id` is byte-stable; the log-id set digest per scenario is in results.json. Ledger transaction timestamps are wall-clock and never enter results.
- `verify_summary.py` checks `generated_RESULTS_SUMMARY.md` against `generated_results.json`; `audit/verify_corrected.py` checks every number in this file against this `results.json` (`corrected_verification.txt`). `run_experiments.sh --verify-repro` reruns everything, including the audit steps, and compares both results.json files (ignoring `meta.volatile`) and every other output byte for byte. Source hashes cover source files and pinned inputs only (audit D1).

## 13. Audit (Independent Checks)

- **Independent predicate.** A re-implementation of the three gates written from their definitions (it imports neither `validation.py` nor `metrics.py`) agrees with the recorded gates and decision on 12000 primary logs (0 mismatches; H1, H1b, H3, H1-U at N = 30), with all 9000 revealed H3 verdicts (890 cast by free-rider agents; 0 disagreements; 0 self-validations) and on 5995 whole-file faithful logs (0 mismatches; 208 rejected, all on rule-(c) problems: True).
- **Leakage.** The regenerated logs have the same log-id sets as `per_log_records.csv` (H1, H1U, H1b, H3). Over 12000 logs: 0 label values, 0 label substrings outside the prompt, 0 tasks with keys beyond task_id, task_type, dataset, prompt. By design, reference expressions (every tier) and reference sentences (standard, verbose) do enter logs: the scripted agent reproduces the reference derivation.
- **Relaxed test.** In the strict substring form, 'reference' occurs in 12000 of 12000 logs, at: /model_id. 'position' in 0; 'gsm8k_index' in 0; 'merged_expr' in 0; 'deps' in 0. The strict form fails only on provenance and dataset text, not on a harness-private key, so the relaxation hid no defect; the test is renamed `test_task_private_keys_never_enter_log`.
- **Tiers.** 100/100 tasks have 5 distinct payloads and 97/100 have 5 distinct step counts; within a tier all agents' payloads are byte-identical (True). In tasks at GSM8K indices 26, 27, 46 (one annotation each) *minimal* and *terse* encode the same single computation and differ only in text and number formatting.
- **Conversion.** Over the 100 selected problems, calculator ops equal the reference annotations in order (100/100) and the final value equals the `####` answer (100/100). A random sample of 10 (seed 2027; indices 1, 10, 16, 27, 50, 54, 61, 70, 95, 99) is shown in full in `conversion_sample.md`.
- **Evasion probe (limitation, not an H3 result).** A free-rider that copies the answer A and records one calculator operation 'A' is accepted on 100/100 tasks (independent predicate: 100/100); with 'A*1' on 100/100. The predicate checks that the answer is produced by a re-executable operation, not that the operation does work on the question's quantities. In 6/100 selected tasks the answer itself appears as a number in the question.
- **Fixes applied in this run.** D1: meta.source_sha256 covers .py/.sh sources and AUDITED_SHA256.txt / requirements.txt only; run_log_final.txt (overwritten by the run) made results.json irreproducible. D8: gsm8k_logs.numeric_constants and merged_expression read '-x' as one negative literal; before the fix a negative intermediate result was never wired to its consumer. Effects: GSM8K test index 489 is now eligible (whole-file rule-(c) problems 53 -> 52, rejected faithful logs 212 -> 208); problem selection, H1, H3 and H1-U logs are byte-identical; in H1b the divergent derivation of task gsm8k-test-0012 changed (7 -> 8 lemons, answer 11, was chosen only because the bug hid a link in the preferred candidate 3 -> 13, answer -35, which the fixed search then selected; 3 logs per seed and N = 30 run change, no H1b rate changes). A negative answer is implausible as an honest misreading; superseded for that task by O4 below. O3: section 6 closing sentence regenerated: the audit's direct ledger count matches the table's per-log protocol and all-ledger columns (the earlier 'does NOT match' came from comparing a 4-dp audit value with the unrounded table value); walking-skeleton ops are explained as a non-ledger count. O4: the H1b divergence search rejects a candidate whose divergent answer is below zero in favour of the next candidate in search order. Selection changed on 1 task(s): gsm8k-test-0012 (rejected step 1: 3 -> 13 (delta 10), answer -35; selected step 0: 7 -> 8 (delta 1), answer 11). Negative divergent answers after the change: 0; negative answers kept as fallback: 0. Only the divergent logs of the changed task(s) differ; H1, H3 and H1-U logs are unaffected. O7: added audit.headline_payload_level: distinct payloads among the correct-answer free-rider logs, with Wilson and Clopper-Pearson intervals, from per_log_records.csv.
