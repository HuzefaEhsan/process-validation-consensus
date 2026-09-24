# GSM8K Conversion Report

Source: `https://raw.githubusercontent.com/openai/grade-school-math/3101c7d5072418e28b9008a6636bde82a006892c/grade_school_math/data/test.jsonl` (upstream commit `3101c7d5072418e28b9008a6636bde82a006892c`, SHA-256 `3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14`, 1319 problems, MIT licence). Dataset citation: Cobbe et al. (2021) — **[VERIFY]** (out-of-matrix).

## Eligibility Rules

Rules (a) and (b) are the two requested rules; (c) was **added** and is flagged; (d) is a safety net.

- (a) every `<<expr=value>>` annotation re-executes under `validation._safe_arith` (the predicate's whitelisted AST evaluator) to exactly its recorded value (discrete canonical equality, tolerance 0);
- (b) the `####` answer (thousands separators removed) equals the last annotation's value;
- (c) **added:** every annotation step lies in the dependency cone of the last annotation under the dataflow wiring (each step depends on every earlier step whose recorded value equals a numeric literal in its expression). Without it the faithful honest log fails Process Coherence and Tool Utilization;
- (d) all five honest tiers are accepted by `validation.validate`.

## Counts

| Scope | Scanned | Eligible | Dropped | no_final_answer_line | no_calculator_annotations | malformed_annotation | annotation_not_executable | annotation_value_mismatch | final_answer_not_last_op | incomplete_recorded_derivation | converted_log_rejected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Selection prefix (indices 0–117) | 118 | 100 | 18 | 0 | 2 | 0 | 0 | 1 | 9 | 6 | 0 |
| Whole test file | 1319 | 1147 | 172 | 0 | 18 | 0 | 0 | 12 | 90 | 52 | 0 |

Counts are by **primary** (first failing) reason. Non-exclusive counts (a problem can fail several rules):

- selection_prefix: no_final_answer_line 0, no_calculator_annotations 2, malformed_annotation 0, annotation_not_executable 0, annotation_value_mismatch 1, final_answer_not_last_op 9, incomplete_recorded_derivation 8, converted_log_rejected 0
- whole_file: no_final_answer_line 0, no_calculator_annotations 18, malformed_annotation 0, annotation_not_executable 0, annotation_value_mismatch 12, final_answer_not_last_op 93, incomplete_recorded_derivation 75, converted_log_rejected 0

## Dropped Problems in the Selection Prefix

| Index | Primary reason | Detail |
| ---: | --- | --- |
| 13 | final_answer_not_last_op | last_op_value=12, final_answer=18, answer_in_earlier_op=False |
| 14 | final_answer_not_last_op | last_op_value=12, final_answer=60, answer_in_earlier_op=False |
| 15 | incomplete_recorded_derivation | steps_outside_answer_cone=[1], has_identity_annotation=True |
| 24 | no_calculator_annotations | — |
| 29 | final_answer_not_last_op | last_op_value=99, final_answer=104, answer_in_earlier_op=False |
| 30 | annotation_value_mismatch | annotation=1, expr=11/18*162, recorded=99, recomputed=99.00000000000001 |
| 34 | final_answer_not_last_op | last_op_value=25, final_answer=23, answer_in_earlier_op=False |
| 37 | incomplete_recorded_derivation | steps_outside_answer_cone=[0, 1, 2], has_identity_annotation=False |
| 43 | final_answer_not_last_op | last_op_value=60, final_answer=48, answer_in_earlier_op=False |
| 52 | incomplete_recorded_derivation | steps_outside_answer_cone=[0, 1], has_identity_annotation=True |
| 62 | incomplete_recorded_derivation | steps_outside_answer_cone=[0], has_identity_annotation=False |
| 81 | final_answer_not_last_op | last_op_value=37, final_answer=17, answer_in_earlier_op=False |
| 84 | incomplete_recorded_derivation | steps_outside_answer_cone=[0], has_identity_annotation=False |
| 88 | no_calculator_annotations | — |
| 92 | incomplete_recorded_derivation | steps_outside_answer_cone=[0], has_identity_annotation=False |
| 98 | final_answer_not_last_op | last_op_value=25, final_answer=5, answer_in_earlier_op=False |
| 107 | final_answer_not_last_op | last_op_value=5, final_answer=3, answer_in_earlier_op=False |
| 114 | final_answer_not_last_op | last_op_value=300, final_answer=6, answer_in_earlier_op=False |

## Whole-File Value Mismatches (Rule a)

These are honest reference computations that fail exact re-execution, mostly IEEE-754 artefacts (e.g. `6*0.1` → 0.6000000000000001 vs recorded 0.60). A live agent that records a rounded calculator value would fail C1 the same way.

| Index | Expression | Recorded | Recomputed |
| ---: | --- | --- | --- |
| 30 | `11/18*162` | 99 | 99.00000000000001 |
| 152 | `6*0.1` | 0.60 | 0.6000000000000001 |
| 272 | `4.2+9.45+1.35` | 15 | 14.999999999999998 |
| 319 | `3/4` | 3/4 | 0.75 |
| 434 | `3*.4` | 1.2 | 1.2000000000000002 |
| 543 | `0.8-0.5` | 0.3 | 0.30000000000000004 |
| 598 | `6*.1` | .6 | 0.6000000000000001 |
| 598 | `2+.6+.7+.15` | 3.45 | 3.4499999999999997 |
| 638 | `3.30/3` | 1.10 | 1.0999999999999999 |
| 919 | `0.8-0.5` | 0.3 | 0.30000000000000004 |
| 1144 | `9/14*84` | 54 | 54.00000000000001 |
| 1152 | `.2*3` | .6 | 0.6000000000000001 |
| 1176 | `2.2*.2` | .44 | 0.44000000000000006 |
| 1176 | `2.2-.44` | 1.76 | 1.7600000000000002 |

## Tiers and Variants

Honest tiers (all accepted on all selected problems): minimal = one merged calculator step (terminal); terse = one tool_call step per annotation, last one terminal; standard = tool_call steps + terminal decision grounded in the last op; verbose = planning inference + (inference, tool_call) per annotation + decision; evidence = non-deterministic `retriever` op read by an evidence_use step that the first computations depend on, numeric op outputs, decision.

Free-rider variants (four, round-robin): bare_assertion_wrong (adversaries.py semantics: answer − 1, no op; C4), bare_assertion_correct (BLUEPRINT D3: correct answer, no op; C4), forged_op (correct answer; last literal of the final expression decremented; C1; fallback `(expr)-1` used on 2 of 100 tasks), orphan_op (correct decoy op outside the answer cone, answer + 1; Tool Utilization).

## H1b Divergent Derivations

misread operand: one operand of the reference derivation that is not an earlier step's result is changed by delta (tried in order [1, 2, -1, 10]); every downstream calculator step is recomputed with the whitelisted executor; one divergent derivation per task, rendered in the agent's own tier; candidates with a negative divergent answer are rejected in favour of the next (audit O4). Selection: per task, sha256(seed|N|task_id) seeds random.sample(range(N), n); fixed across harness seeds. Of 100 tasks: operand from the question 90; clean values 98; wiring identical 98, subset 2; deltas {'-1': 1, '1': 88, '10': 1, '2': 10}. Per-task detail in `h1b_divergent_derivations.csv`.

## Selected Problems

0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 17, 18, 19, 20, 21, 22, 23, 25, 26, 27, 28, 31, 32, 33, 35, 36, 38, 39, 40, 41, 42, 44, 45, 46, 47, 48, 49, 50, 51, 53, 54, 55, 56, 57, 58, 59, 60, 61, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 82, 83, 85, 86, 87, 89, 90, 91, 93, 94, 95, 96, 97, 99, 100, 101, 102, 103, 104, 105, 106, 108, 109, 110, 111, 112, 113, 115, 116, 117

