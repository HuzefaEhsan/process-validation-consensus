# Validation Predicate

`validation.validate(log)` judges one committed action log and returns a verdict: `accept` or
`reject`, the result of each check, and a `recompute_digest`. The predicate re-executes recorded
operations and walks the recorded dependency graph. It never asks a language model for a judgement
and never reads natural-language `content` strings, so anyone holding the committed bytes can
recompute the same verdict.

## Order of evaluation

0. **Structural precondition.** `agents.validate_log` must pass first (see [schema.md](schema.md)).
   A structurally invalid log raises `SchemaError`; the consensus layer treats that as a reject.
1. **Three gates.** Process Coherence, Tool Utilization and Causal Sufficiency are computed on
   the log. The decision is `accept` only if all three pass.
2. **Cross-agent coherence** (optional). If peer logs are supplied, a similarity signal is computed
   for tie-breaking only. It is never a gate and is excluded from the digest, so an honest log whose
   answer differs from the majority is never rejected for disagreeing.
3. **Digest.** `recompute_digest` is the SHA-256 of the RFC 8785 canonical transcript of the three
   gates and the decision, together with the predicate version and the log and task identifiers.
   Validator identity is not part of it, so two honest validators of the same log obtain the same
   digest.

## Gate 1: Causal Sufficiency

Does the recorded reasoning reproduce the final output? For `discrete` tasks (all tasks in the
reported runs) it is the conjunction of four sub-checks, compared by exact canonical match:

- **C1.** Every operation marked `deterministic: true` re-executes to its recorded output. An
  operation whose tool has no registered re-executor, or whose inputs cannot be evaluated, fails.
- **C2.** Every step that references a tool operation and records a `produces` value carries that
  operation's re-executed value (for a non-deterministic operation, its recorded output).
- **C3.** The terminal step (`final_output.derived_from_step`) produces `final_output.value`.
- **C4.** The final value is grounded: it equals the re-executed value of a deterministic operation
  referenced by a step in the terminal step's dependency cone (the terminal step and everything it
  transitively depends on).

For `continuous` tasks the same checks apply within a stated numeric tolerance. For `open_ended`
tasks the interface exists, but the check is deliberately unimplemented and `validate` raises
`F2PendingError` instead of returning a verdict; no reported run uses such tasks.

## Gate 2: Process Coherence

Every `depends_on` entry refers to an earlier step (so the graph is acyclic), the terminal index
exists, and there are no orphan steps: every step other than the terminal one is depended on by some
other step.

## Gate 3: Tool Utilization

Every recorded tool operation is referenced by at least one step that lies in the terminal step's
dependency cone. An operation that nothing uses, or that only feeds reasoning which never reaches
the answer, fails the gate.

## What the checks catch, and what they do not

The following are measured in the reported runs (all values from
[`results/results.json`](../results/results.json)):

- **C4 carries every detection.** C4 fails on 300/300 free-rider logs in scenario H3. Process
  Coherence and Tool Utilization fail only on the orphan-operation variant, which C4 also rejects,
  so no free-rider log is caught by those two gates alone.
- **The false-positive cost comes from the other two gates.** Without selection rule (c), the
  predicate rejects 208/5995 faithful honest logs over the whole GSM8K test file; all 208 fail
  Process Coherence and Tool Utilization and none fails Causal Sufficiency. The recorded answer
  chain re-executes in every one of them; what fails is that some recorded calculator result has no
  recorded consumer, because the reference solution continued the arithmetic in prose.
- **Identity-operation evasion.** A free-rider that copies a correct answer A and records a single
  calculator operation `A` is accepted on 100/100 tasks; recording `A*1` is also accepted on
  100/100. The predicate checks that the answer is produced by an operation that re-executes, not
  that the operation works on the question's quantities. This was an audit probe, not one of the
  evaluated free-rider variants, and it bounds what a single-log check can establish.
- **Model identity is not read.** Re-validating all 3000 H3 logs with `model_id` replaced by two
  other identifiers gave an identical decision, gate results and digest in 6000/6000 cases. This
  shows the predicate is independent of the model identifier; it does not show that logs written by
  a real model satisfy the schema, which needs the planned live run.
