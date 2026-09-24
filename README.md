# process-validation-consensus

This repository contains a prototype of process-validation consensus for decentralized multi-agent
systems built on large language models. Each agent commits not only its final answer but also a
structured action log of the reasoning and tool operations that produced it. Randomly assigned
peers then re-check that log with a mechanical predicate: they re-execute the recorded operations
and trace the recorded dependencies to see whether the log actually yields the answer. Agreement is
therefore reached over validated reasoning logs rather than over matching outputs, and a log that
fails validation is excluded and costs its author reputation. A simulated hash-chained ledger stores
logs, verdict commitments and reputation updates so that none of them can be altered unnoticed; it
is a tamper-evident record only, not a consensus mechanism of its own, and it supplies no values to
the agents.

**Status:** research prototype supporting an ACIIDS 2027 submission. Scenarios H1, H1b, H3 and H1-U
are evaluated offline on GSM8K problems with scripted agents. H2 (collusion), H4 (Byzantine
validators) and a live run with Llama 3.1 8B are future work; no results for them exist here.

## Headline Result

- In scenario H3 (N = 30 agents, 3 free-riders, 100 GSM8K tasks, primary seed), output-only voting flags **0/150** free-rider logs that carry the correct final answer, while process validation flags **150/150**.
- By construction, process validation catches all 150: each free-rider variant is built to fail a specific check of a deterministic predicate, every validator is honest, and the free-riders do not adapt.
- By construction of the output-only arm, it misses all 150: it reads only the final value, which is correct in these logs; the result measures that blind spot, not how hard free-riders are to detect.

## Install

Python 3.12 (results were produced with 3.12.3).

```bash
git clone https://github.com/HuzefaEhsan/process-validation-consensus.git
cd process-validation-consensus
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Reproduce

```bash
bash run_experiments.sh
bash run_experiments.sh --verify-repro
```

`run_experiments.sh` runs offline apart from one download, and needs no GPU or model. After
installing any missing requirements, it:

1. downloads the GSM8K test split from its pinned upstream commit into `data/` and checks it against
   the SHA-256 recorded in `results/results.json`, stopping on any mismatch;
2. checks the five audited modules against `src/AUDITED_SHA256.txt`;
3. runs the test suite;
4. runs every scenario and writes `results/`;
5. checks every number in the generated summary against the generated `results.json`;
6. runs the independent audit checks, writes the audit-corrected `results/results.json` and
   `results/RESULTS_SUMMARY.md`, and checks those against each other.

`--verify-repro` then repeats steps 4–6 into a temporary directory and compares the two runs file
by file. Measured on one CPU core with 4 GB of memory, the plain run takes about 10 minutes and
`--verify-repro` about 20 minutes. Both verifiers print `RESULT: PASS`; `--verify-repro` ends by
printing `REPRODUCIBLE` and exits with status 0. A rerun rewrites `results/` identically except
for the generation timestamp and runtime (`meta.volatile` in `results.json` and one line of
`RESULTS_SUMMARY.md`) and, on another machine, the recorded platform (`meta.environment`).

**Dataset.** GSM8K test split from <https://github.com/openai/grade-school-math> at commit
`3101c7d5072418e28b9008a6636bde82a006892c`, released under the MIT licence, Copyright (c) 2021
OpenAI. It is downloaded by `scripts/download_gsm8k.py` and never committed. Short excerpts appear
in the tests and results; the list of excerpts and the full licence notice are in
[docs/experiments.md](docs/experiments.md#dataset).

**Figures.** `python3 scripts/fig1_architecture.py` and
`python3 scripts/build_fig2.py results/results.json figures/` rebuild the two figures (the Liberation
Sans font is needed for identical output).

## Test

```bash
pytest
```

Expected: `131 passed` once the dataset is in `data/` (run `python3 scripts/download_gsm8k.py`
first). Without the dataset, the six tests that read it are skipped (`125 passed, 6 skipped`).

## Repository Map

| Directory | Contents |
|---|---|
| `src/` | ledger, agent layer, validation predicate, consensus layer, metrics, GSM8K conversion, free-rider variants, experiment driver and report writer |
| `tests/` | unit and integration tests (`pytest`) |
| `audit/` | independent re-checks of the results, the audit corrections, and verification and reproducibility scripts |
| `scripts/` | dataset download and figure builders |
| `results/` | audit-corrected `results.json` (the numeric authority), `RESULTS_SUMMARY.md`, per-log records and tables |
| `figures/` | Fig. 1 (architecture) and Fig. 2 (detection per free-rider variant) as SVG, PNG and PDF |
| `docs/` | [architecture](docs/architecture.md), [log schema](docs/schema.md), [predicate](docs/predicate.md), [experiments](docs/experiments.md) |
| `data/` | empty in the repository; the dataset is downloaded here |

## Scope and Non-Claims

- **No node consensus.** The ledger is simulated and has no leader election, block ordering, fork
  choice or finality rule; nothing about blockchain consensus is designed, proved or benchmarked.
- **Not a randomness beacon.** Validators are drawn by the experiment harness's seeded generator; no
  ledger value is ever given to agents as a coordination input.
- **No Sybil resistance.** Agent identities (W3C DIDs) are presupposed, not verified.
- **No Byzantine-majority resilience.** The evaluated attack fraction is 10%, and every validator in
  the reported runs is honest.
- **Simulated chain.** No production blockchain is used.
- **No collusion claim.** Collusion detection (H2) is planned, not evaluated.
- **No LLM as judge.** The predicate is mechanical and recomputable; no model scores a log.
- **Non-adaptive attack model.** The free-rider variants are fixed constructions, not attackers that
  respond to the predicate.
- **No LLM in the loop.** Honest logs are transcriptions of GSM8K reference solutions; the live
  model harness `src/validate_live.py` is included for the planned run, but no result comes from it.

## Known Limitations

- **Honest cost of a strict predicate.** Without selection rule (c), the predicate rejects 4.3%
  (52/1199, Wilson 95% [3.3, 5.6]) of faithful honest logs in every tier that records the full
  derivation, so the < 5% false-positive target is not met; over all tiers of the whole test file
  the rate is 3.5% (208/5995). Every rejection comes from Process Coherence and Tool Utilization,
  none from Causal Sufficiency: the reference solutions finish some arithmetic in prose, leaving a
  recorded result without a recorded consumer. Logs written by a language model may fare worse.
- **Identity-operation evasion.** A free-rider that copies the correct answer A and records the
  single operation `A` (or `A*1`) is accepted on 100/100 tasks. The predicate verifies that the
  answer is produced by an operation that re-executes, not that the operation uses the question's
  quantities.
- **Zero honest flags in H1 hold by construction.** Selection rules (c) and (d) admit only problems
  whose honest logs the predicate accepts; the unfiltered scenario H1-U flags at least one honest log
  in 6/100 tasks.
- **Small effective sample.** Honest agents of one tier emit identical logs, so the 3000 honest
  logs of H1 are 500 distinct payloads; task-level figures are the defensible unit.
- **No accuracy gain at 10%.** Consensus accuracy is 100/100 in both arms (+0.0 pp), because the
  few free-rider answers are outvoted anyway. At this fraction the protocol adds detection, exclusion
  and a reputation penalty, not accuracy.
- **Reputation is written but not used.** Each scenario is a single batch, so all authors are
  weighted equally when aggregating.
- **Constructed honest error.** In H1b the wrong-but-valid derivations are made by changing one
  operand, not observed from a model.
- **Cost.** The process arm needs 9.00 protocol ledger transactions per log (10.01 in all); the
  output-only arm performs no validation.

## Citation

Citation metadata are in [CITATION.cff](CITATION.cff); GitHub's "Cite this repository" button reads
that file. Until the paper's reference is final:

```bibtex
@software{ahsan_process_validation_consensus_2026,
  author  = {Ahsan, Huzaifa and Wardana, Aulia Arif and Sukarno, Parman},
  title   = {process-validation-consensus},
  year    = {2026},
  license = {MIT},
  url     = {https://github.com/HuzefaEhsan/process-validation-consensus}
}
```

Accompanying paper: [ACIIDS 2027 REFERENCE — to be added after review].

## Authors

- **Huzaifa Ahsan** (contact: huzaifaahsan@student.telkomuniversity.ac.id), Master's programme in
  Informatics, School of Computing, Telkom University, Bandung, Indonesia
- **Aulia Arif Wardana**, Telkom University (supervisor)
- **Parman Sukarno**, Telkom University (supervisor)

This work was carried out at Telkom University as part of the first author's Master's thesis.

## License

MIT; see [LICENSE](LICENSE). The GSM8K data are distributed separately under their own MIT licence
(see above).
