# Experiments

All reported results come from one offline pipeline, `run_experiments.sh`. No language model is in
the loop: agents are scripted, honest logs are mechanical transcriptions of GSM8K reference
solutions, and free-rider logs are built by fixed rules. Every number below is read from
[`results/results.json`](../results/results.json); [`results/RESULTS_SUMMARY.md`](../results/RESULTS_SUMMARY.md)
renders the same numbers with commentary.

## Dataset

- **Source.** GSM8K test split, file `grade_school_math/data/test.jsonl` from
  <https://github.com/openai/grade-school-math> at commit
  `3101c7d5072418e28b9008a6636bde82a006892c` (1319 rows, SHA-256
  `3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14`).
- **Not committed.** `scripts/download_gsm8k.py` fetches the file from that commit, checks the
  SHA-256 recorded in `results/results.json` (`meta.dataset.file_sha256`) and refuses any mismatch.
  It also fetches the upstream licence file to `data/GSM8K_LICENSE`.
- **Licence.** MIT, Copyright (c) 2021 OpenAI; the full notice is at the end of this page. Short
  excerpts of GSM8K problems and solutions appear in this repository under that licence: three
  problems in `tests/test_gsm8k_logs.py`, and problem text inside `results/conversion_sample.md`,
  `results/rule_c_examples.md` and `results/example_logs.json`.

## Problem selection: rules (a)–(d)

Problems are scanned in file order (0-based line index), and the first 100 that pass all four rules
form the primary sample. Reaching 100 took indices 0–117: 118 scanned, 18 dropped.

- **(a)** Every calculator annotation `<<expr=value>>` re-executes exactly under the predicate's own
  arithmetic evaluator.
- **(b)** The final answer after `####` equals the value of the last annotation.
- **(c)** The recorded derivation is complete: every annotation lies in the final answer's dependency
  cone. This rule was added after the first runs showed that some faithful logs fail the predicate.
- **(d)** Safety net: the predicate accepts all five honest tiers of the problem.

Rules (c) and (d) admit only problems whose honest logs the predicate already accepts, so the zero
honest-log flag rate in H1 holds by construction. The honest-log cost without rule (c) is measured
separately (scenario H1-U and the whole-file measurement below).

Over the whole test file, 1199 problems pass rules (a) and (b); 52 of them fail rule (c).

## From a reference solution to logs

Each calculator annotation becomes one `calculator` operation and one `tool_call` step. A step
depends on every earlier step whose value appears as a literal in its expression. Each problem is
rendered in five honest tiers, all variations of the same valid derivation:

| Tier | Content |
|---|---|
| minimal | one step and one operation: the whole derivation merged into a single expression |
| terse | one tool step per annotation; the last one is terminal |
| standard | one tool step per annotation, then a terminal decision step |
| verbose | a planning step, then per annotation an inference step (the reference sentence) and its tool step, then a decision step |
| evidence | a non-deterministic retrieval step read by the first computations, one tool step per annotation, then a decision step |

## Scenarios

| Scenario | Population | Purpose |
|---|---|---|
| H1 | all honest | end-to-end check that the protocol reproduces single-log decisions; zero flags hold by construction |
| H1b | all honest; per task, 10% of agents derive a wrong answer through a valid chain (one operand of the reference derivation changed, downstream steps recomputed, negative answers rejected) | attribution: can each arm tell honest error from free-riding? Not a false-positive test |
| H3 | 10% free-riders, cycling round-robin through four variants | detection, exclusion and reputation penalty |
| H1-U | as H1, on the first 100 problems passing rules (a) and (b) only (indices 0–110) | honest-log cost inside the protocol without rule (c); shares 94 problems with the primary sample |

**Free-rider variants** (H3), each schema-valid and each built to fail a specific check:

| Variant | Final answer | Construction | Checks it fails |
|---|---|---|---|
| `bare_assertion_wrong` | wrong | answer asserted with no operation (answer − 1) | C4 |
| `bare_assertion_correct` | correct | correct answer asserted with no operation | C4 |
| `forged_op` | correct | a calculator operation whose recorded output is the correct answer but whose inputs compute a different value | C1, C2, C4 |
| `orphan_op` | wrong | a correct decoy operation that nothing in the answer's cone uses; answer + 1 | Process Coherence, Tool Utilization, C4 |

The attack model is not adaptive: no variant is designed to evade the predicate. The
identity-operation evasion described in [predicate.md](predicate.md) is an audit probe, not a
variant.

## Populations and seeds

- **Primary:** N = 30 agents; robustness runs at N = 10 and N = 20. Agent i renders tier i mod 5.
- **Free-riders (H3):** 3 of 30 (agents 4, 13, 22); 1 of 10 (agent 4); 2 of 20 (agents 4, 13).
  In the primary run each variant occurs 75 times, once per task in 75 of the 100 tasks.
- **Honest-error agents (H1b):** 3, 1 and 2 per task at N = 30, 10 and 20, drawn per task from a
  fixed seed (20260629) that does not change with the harness seed.
- **Harness seeds:** 20260629 (primary), 20260630, 20260631, 20260632, 20260633. A seed changes
  only which validators are drawn and the commitment salts; payloads, task order and population are
  identical across seeds. Seeds are therefore not independent replications and are never pooled.
  Every summary metric is identical across the five seeds in every scenario and population.

## Arms, metrics and units

- **Process arm:** the protocol in [architecture.md](architecture.md) with k = 3 validators and a
  2/3 reject supermajority. **Output-only arm:** unweighted plurality over all final outputs of a
  task; a log is flagged if and only if its output differs from the plurality.
- **Metrics:** consensus accuracy (tasks whose consensus value equals the reference answer), flag
  rates over honest logs, detection over free-rider logs, per-variant detection, exclusion of flagged
  logs, reputation trajectories, and ledger transaction counts per log.
- **Units.** Logs within a task are not independent, and the agents of one tier emit byte-identical
  payloads: the 3000 honest logs of H1 are 500 distinct payloads, and the 300 free-rider logs of H3
  are 258. Task-level figures, then distinct-payload figures, are the defensible units; log-level
  intervals are optimistic.
- **Statistics.** Wilson and Clopper–Pearson 95% intervals for every proportion; exact McNemar tests
  on paired units, reported as discordant counts b (flagged by the process arm only) and c (flagged
  by the output-only arm only). On deterministic outcomes the p-values carry little information.
- **Targets.** Consensus accuracy ≥ 95%, detection ≥ 90%, false-positive rate < 5%, each counted as
  met only if the interval bound clears the target.

## Reading `results/RESULTS_SUMMARY.md`

| Section | Content | Note |
|---|---|---|
| 1 | H1 | zero honest flags hold by construction (rules (c) and (d)) |
| 2 | H1b and the four-cell attribution table | report as attribution, not as a false-positive rate |
| 3 | H3, per-variant detection, gate attribution, reputation | process-arm detection holds by construction; the output-only misses are structural |
| 4 | honest-log cost without rule (c): whole file and H1-U | the per-tier target is not met |
| 5 | paired arm comparisons (McNemar) | discordant counts are the informative part |
| 6 | verification cost | cite per-log ledger transactions only |
| 7 | robustness over N and seeds | seed invariance is expected, not evidence of stability |
| 8 | exact-artifact agreement illustration | constructed example |
| 9 | targets | met only if the interval bound clears the target |
| 10 | code-level confirmations | |
| 11 | out-of-scope diagnostic | **not reportable**: at a 50% tie the plurality tie-break orders by value, which favours the correct answer by construction |
| 12–13 | integrity, reproducibility and the independent audit | |

**Corrected and generated files.** `src/experiments.py` writes the raw outputs; `audit/audit_corrections.py`
then renames the raw `results.json`, `RESULTS_SUMMARY.md`, `mcnemar.csv`, `robustness.csv` and
`h1b_divergence.csv` to `generated_*` and writes the audit-corrected versions under the original
names. The corrected versions are the authority and are committed; the `generated_*` files are
regenerated on every run and ignored by git. The corrections rename keys and reword text (for
example, H1b flag rates are keyed as attribution, not as false-positive rates) and add an `audit`
block of independent recomputations; every numeric value of the generated `results.json` is carried
over unchanged.

**Other files in `results/`.**

| File | Content |
|---|---|
| `per_log_records.csv` | one row per log per run: scenario, N, seed, task, agent, gate results, flags in both arms, payload hash |
| `h1_baseline.csv`, `h1b_divergence.csv`, `h3_freeriding.csv`, `h3_per_variant.csv` | proportion tables of sections 1–3 |
| `rule_c_unfiltered.csv`, `rule_c_gate_breakdown.csv` | section 4 |
| `mcnemar.csv`, `robustness.csv`, `cost.csv` | sections 5–7 |
| `h1b_divergent_derivations.csv` | the H1b divergent derivation per task |
| `example_logs.json` | one committed log per tier and variant, with its gate results |
| `gsm8k_conversion_report.md`, `conversion_sample.md`, `rule_c_examples.md` | conversion statistics, 10 hand-checkable conversions, and the rule-(c) failure patterns |
| `audit_checks.json` | outputs of `audit/independent_checks.py` |
| `summary_verification.txt` | `audit/verify_summary.py` on the generated summary |
| `corrected_verification.txt` | `audit/verify_corrected.py` on the corrected summary |

## Verification scripts

| Script | Checks |
|---|---|
| `audit/verify_summary.py` | every number in the generated summary is derivable from the generated `results.json`, and every x/n row prints the matching percentage and intervals |
| `audit/independent_checks.py` | re-implements the three gates without importing the predicate and compares every recorded decision; checks conversion faithfulness, validator verdicts and label leakage; runs the identity-operation probe |
| `audit/audit_corrections.py` | produces the corrected pair from the generated one |
| `audit/verify_corrected.py` | the same provenance and row checks on the corrected pair |
| `audit/check_reproducibility.py` | compares two results directories: `results.json` ignoring `meta.volatile`, the summary ignoring its timestamp line, every other file byte for byte |
| `audit/diff_results.py` | lists every changed `results.json` leaf (ignoring `meta.volatile` and source hashes), changed summary lines and changed per-log rows between two results directories |

`scripts/fig1_architecture.py` and `scripts/build_fig2.py` rebuild the two figures; `build_fig2.py`
reads only `results/results.json` and aborts if any plotted value disagrees with its x/n and
interval.

## Glossary of internal labels

Code comments and generated result text use labels from internal planning documents (the design
plan, the experiment blueprint, the project's scope statement, and review notes). These documents
are not included in this repository; the labels mean the following.

| Label | Meaning |
|---|---|
| Components A, B, C, D | ledger (`sim_ledger.py`), agent layer (`agents.py`), validation predicate (`validation.py`), consensus layer (`consensus.py`) |
| "section N", "sec N" in code comments | sections of the design plan, e.g. 0.2 (the ledger is not a beacon), 0.6 (identity presupposed), 0.7 (no ground-truth labels in logs), 2 (action-log schema), 3.2 (verdict format), 5 (design flags) |
| "section N", "§N" in generated result text | sections of `results/RESULTS_SUMMARY.md` |
| F1 | predicate design per task type (implemented for `discrete` and `continuous`; `open_ended` deferred) |
| F2 | task set and scorer for open-ended or multi-answer tasks (deferred) |
| F3 | peer-set randomness comes from the experiment harness, never from the ledger |
| F4 | supermajority threshold and peer-set size |
| F5 | equivocation and non-reveal policy (not implemented) |
| F6 | reputation default and update curve |
| F8 | RFC 8785 canonical JSON as a hard dependency |
| F10 | ledger snapshot and export (planned) |
| F11 | batch-lockstep rounds (one commit phase, then one reveal phase) |
| playbook A.3–A.6, build-order step | implementation plan steps |
| BLUEPRINT D2, D3, D4, D6, D10 | experiment-design decisions: H1b divergence construction; the correct-answer bare-assertion variant; primary N = 30; 97.5% confirmatory intervals; the out-of-scope crossover diagnostic (section 11 of the summary, not reportable) |
| BLUEPRINT 4.3, 4.6 | experiment-blueprint sections: the validator audit; the planned Newcombe interval, replaced by the McNemar test |
| P2, P3, P4 | project stages: experiment, independent audit, post-audit rerun |
| D1, D8, O3, O4, O7 | audit fixes and follow-ups, described in `results.json` → `audit.fixes_applied` (unrelated to the BLUEPRINT D-labels) |
| "Fact Sheet", "fact sheet sec 5" | the project's scope and reporting statements (scope, non-claims, number formatting); the non-claims are listed in the README |
| seminar comments (a), (b) | thesis-seminar review comments: (a) the framework should not depend on a particular model; (b) show concretely where output matching fails |
| H2, H4 | planned scenarios (collusion; Byzantine validators); no results exist |
| "the paper" | the ACIIDS 2027 submission this repository supports |

## Provenance of this repository

This repository reorganises the audited experiment package into directories. The five audited
modules (`sim_ledger.py`, `agents.py`, `validation.py`, `consensus.py`, `metrics.py`) are
byte-identical to the audited versions; `src/AUDITED_SHA256.txt` records their hashes and
`run_experiments.sh` checks them before every run. The only code changes are path adjustments for
the new layout (`src/experiments.py`, `tests/test_gsm8k_logs.py`, `tests/conftest.py`,
`audit/independent_checks.py`, `audit/audit_corrections.py`, `run_experiments.sh`), an output
directory argument for `scripts/fig1_architecture.py`, and `scripts/download_gsm8k.py`.
`run_experiments.sh` also deletes `results/generated_*` before each run, because
`audit/audit_corrections.py` reuses existing `generated_*` files instead of replacing them.
`scripts/build_fig2.py` is the figure section of the authors' float-build script, unchanged.
`src/experiments.py` now records source hashes for `src/`, `tests/`, `audit/`, `scripts/` and the
root files, keyed by relative path, and no longer states that the working directory is not a git
repository.

The files in `results/` were regenerated in this layout by `run_experiments.sh --verify-repro`
(exit status 0, `REPRODUCIBLE`) and compared with the audited results using
`audit/diff_results.py` and byte comparison. Every value in `results.json` is identical apart from
metadata: the generation timestamp and runtime, the source hashes (now keyed by repository path) and
the git note described above. The per-log records and every other output file are byte-identical,
and `RESULTS_SUMMARY.md` differs only in its timestamp line.

`src/validate_live.py` is the harness for the planned live run with Llama 3.1 8B served by Ollama.
It is included for completeness; no result in this repository comes from it, and its
`--selftest` mode exercises only the measurement plumbing.

## GSM8K licence

The GSM8K dataset is distributed under the following licence, reproduced in full:

```text
MIT License

Copyright (c) 2021 OpenAI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
