#!/usr/bin/env python3
"""walking_skeleton.py -- Component/playbook A.5: the END-TO-END SCENARIO HARNESS.

WHAT THIS IS
------------
The "walking skeleton": one runnable pipeline that wires the four implemented components
(A ledger / B agents / C predicate / D- consensus+reputation) into a single end-to-end run,
driven by ONE config block at the top. It GENERALIZES `consensus.run_h1` -- it does NOT
duplicate the consensus pipeline. The pipeline itself is `consensus.run_round`; this harness
only generalizes the *population construction + label store* that `consensus.build_h1`/`run_h1`
hardcoded (fixed N=5, fixed task set, all-honest), so that a later behavior/attack config can
drive H2 (collusion) and H3 (free-riding) through the SAME codebase with NO pipeline changes.
Adversaries are NOT implemented here -- that is the next build step, A.6 (Component E).

This is the runner that A.6 plugs into:
  * the population is built from a CONFIG (N, model id, k, supermajority, task set, RNG seed,
    effort spread, behavior plan), not hardwired;
  * each behavior_class resolves through a PAYLOAD-GENERATOR REGISTRY -- A.6 registers a
    `colluder` / `free_rider` generator and flips entries of the behavior plan; nothing else
    changes;
  * the harness owns the row-0 EXPERIMENT LABEL STORE (sec 0.7): ground-truth answer,
    honesty/effort tier, and (for A.6) an attack label. NONE of these ever enter a committed
    log, a verdict, a seal, a reputation record, or any ledger payload. The predicate (C) and
    the consensus layer (D) never see them; ONLY metrics.py consumes ground_truth and the
    attack labels (inside `run_round`).

LOCKED FRAMING honored (Fact Sheet / prototype_plan sec 0), unchanged from the components:
  * Process, not output, is the unit of agreement (the predicate gates the committed log).
  * Blockchain is a tamper-evident SUBSTRATE ONLY and is NEVER a randomness beacon -- peer-set
    randomness comes from the harness RNG (recorded seed); randomness flows
    harness -> assignment -> ledger-record, never ledger -> agent.
  * The predicate is mechanical + recomputable (no LLM-as-judge), so any verdict is publicly
    refutable.

OFFLINE. This walking skeleton runs through the offline `agents.ScriptedModelClient`: the model
"output" is a canned, schema-valid reasoning payload, so the whole assemble -> commit -> validate
-> consensus -> seal path runs with no Ollama. The LIVE Llama-3.1-8B emission-rate run
(`validate_live.py`) is a SEPARATE, STILL-PENDING item to be executed on a machine with Ollama;
it is not part of this skeleton (flagged below).

This file IMPORTS sim_ledger / agents / validation / consensus / metrics and modifies NONE of
them. The five modules sit beside it (byte-identical to the audited versions).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import agents
import consensus
import metrics
import validation  # noqa: F401  (imported so the predicate's presence in the wiring is explicit)
from sim_ledger import SimulatedLedger

# ==============================================================================================
# 0. SINGLE CONFIG BLOCK  (the only thing you edit to change the scenario)
# ==============================================================================================
# Five legitimate effort tiers, ALL grounding the answer in a re-executable calculator op (so all
# ACCEPT under causal-sufficiency). "minimal" is a single tool_call step that is itself terminal
# -- the floor of honest effort -- carried to demonstrate minimal-but-valid reasoning is NOT
# penalised (condition iii / FPR < 5%). Reused verbatim from the audited consensus module.
EFFORT_TIERS = consensus.EFFORT_TIERS                       # ["minimal","terse","standard","verbose","evidence"]


@dataclass
class ScenarioConfig:
    """Everything that defines a scenario. With the defaults below the harness reproduces the
    locked A.4 H1 all-honest baseline; change the fields to drive other scenarios. A.6 adds
    colluders/free-riders purely by editing `behavior_plan` (+ registering a generator) -- the
    pipeline (`consensus.run_round`) is untouched."""
    # -- population --------------------------------------------------------------------------
    n_agents: int = 5                                       # 3..5 for the A.5 walking skeleton
    model_id: str = agents.DEFAULT_MODEL                    # "llama3.1:8b" (recorded in each log)
    effort_spread: list[str] = field(default_factory=lambda: list(EFFORT_TIERS))
    # behavior_plan[i] is agent i's behavior_class (a HARNESS label, never logged). All "honest"
    # here; A.6 sets e.g. [...,"colluder"] / [...,"free_rider"]. len is normalised to n_agents.
    behavior_plan: list[str] = field(default_factory=lambda: ["honest"] * 5)
    # -- consensus knobs (owned by D; surfaced here so the scenario is self-describing) -------
    k_peers: int = consensus.PEER_SET_SIZE                  # F4: peer-set size (3)
    supermajority: float = consensus.SUPERMAJORITY_THRESHOLD  # F4: reject fraction to flag (2/3)
    # -- task set ----------------------------------------------------------------------------
    # Discrete GSM8K-style tasks (answer == a + b). Ground truth is a HARNESS label and never
    # appears in any log; it mirrors the A.4 H1 set so the parity check below is meaningful.
    task_set: list[dict] = field(default_factory=lambda: [
        {"task_id": "gsm8k-A", "task_type": "discrete", "dataset": "GSM8K",
         "prompt": "A box has 12 red and 25 blue marbles. How many marbles in total?", "a": 12, "b": 25},
        {"task_id": "gsm8k-B", "task_type": "discrete", "dataset": "GSM8K",
         "prompt": "There are 9 apples and 8 pears in a basket. How many fruits in total?", "a": 9, "b": 8},
        {"task_id": "gsm8k-C", "task_type": "discrete", "dataset": "GSM8K",
         "prompt": "A shelf holds 40 novels and 2 atlases. How many books in total?", "a": 40, "b": 2},
    ])
    # -- recorded seeds (F3: environment, NOT protocol; never read by an agent) ---------------
    harness_seed: int = consensus.HARNESS_SEED              # drives peer-set selection + salts
    model_seed_base: int = consensus.MODEL_SEED_BASE        # per-agent sampling seed = base + i
    model_rng_seed: int = 7                                 # Mesa scheduler RNG (activation order)

    def normalised_behavior_plan(self) -> list[str]:
        """Pad/truncate the behavior plan to exactly n_agents (default-fill 'honest')."""
        bp = list(self.behavior_plan)
        if len(bp) < self.n_agents:
            bp += ["honest"] * (self.n_agents - len(bp))
        return bp[: self.n_agents]


# Default scenario == the H1 walking skeleton (5 honest agents, full effort spread, 3 tasks).
H1_CONFIG = ScenarioConfig()


# ==============================================================================================
# 1. EXPERIMENT LABEL STORE  (sec 0.7 -- harness-private; never on-ledger, never seen by C/D)
# ==============================================================================================
@dataclass
class LabelStore:
    """The row-0 ground-truth store. It is the ONLY place honesty/effort/attack/ground-truth
    facts live. The predicate and the consensus layer are constructed WITHOUT a reference to it;
    only metrics.py (via run_round) is handed `ground_truth` and `attack_label` to score the run.

    Keyed deliberately by different identifiers to match how each consumer reads them:
      * ground_truth : task_id  -> correct answer        (metrics.consensus_accuracy)
      * attack_label : log_id   -> is-this-log-an-attack (metrics.detection_rate / false_positive_rate)
      * effort_tier  : agent_id -> effort tier label     (harness bookkeeping / reporting only)
      * honesty      : agent_id -> behavior_class label  (harness bookkeeping; defines attack_label)
    """
    ground_truth: dict[str, Any] = field(default_factory=dict)
    attack_label: dict[str, bool] = field(default_factory=dict)
    effort_tier: dict[str, str] = field(default_factory=dict)
    honesty: dict[str, str] = field(default_factory=dict)

    def assert_invisible(self, ledger: SimulatedLedger, log_recs: list) -> dict:
        """Prove sec 0.7: no label-store FACT leaked into any committed log. Three checks per log:
          (a) the audited structural validator passes (it rejects forbidden label KEYS);
          (b) no behavior_class label string appears as a VALUE anywhere in the log;
          (c) no effort-tier label string appears as a VALUE anywhere in the log.
        NB: the ground-truth ANSWER legitimately appears in `final_output.value` -- that is the
        agent's OUTPUT, not a label -- so it is (correctly) NOT asserted absent."""
        labels = set(self.honesty.values()) | set(self.effort_tier.values())
        n_logs = 0
        for rec in log_recs:
            log = ledger.get_log(rec.log_id)
            ok, errors = agents.validate_log(log, require_anchors=True)
            if not ok:
                raise AssertionError(f"sec0.7/schema: committed log {rec.log_id[:12]} invalid: {errors}")
            leaked = labels.intersection(_string_values(log))
            if leaked:
                raise AssertionError(f"sec0.7 LEAK: label value(s) {sorted(leaked)} found in log "
                                     f"{rec.log_id[:12]}")
            n_logs += 1
        return {"logs_scanned": n_logs, "label_values_checked": sorted(labels), "leaks": 0}


def _string_values(obj: Any):
    """Yield every string VALUE in a nested JSON-like object (dict values + list items + scalars).
    Used to prove no label string leaked into a log (exact-value match -- so the schema enum
    'evidence_use' is correctly distinct from the effort label 'evidence')."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _string_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _string_values(v)


# ==============================================================================================
# 2. PAYLOAD-GENERATOR REGISTRY  (the behavior/attack SEAM A.6 extends; no adversaries here)
# ==============================================================================================
# A payload generator maps (effort, task) -> a raw reasoning-payload JSON string (exactly what a
# model would emit). The honest generator delegates to the audited `consensus.make_payload`, so
# there is ONE source of honest-log truth. To add H2/H3, A.6 registers a "colluder"/"free_rider"
# generator here and sets behavior_plan entries -- build_population and run_round are untouched.
PayloadGenerator = Callable[[str, dict], str]


def honest_payload(effort: str, task: dict) -> str:
    """Honest reasoning payload for a discrete (a+b) task at the given effort tier."""
    return consensus.make_payload(effort, task["a"], task["b"])


PAYLOAD_GENERATORS: dict[str, PayloadGenerator] = {
    "honest": honest_payload,
    # "colluder":   <registered by A.6 -- covertext-modification tier; co-gated by F2>
    # "free_rider": <registered by A.6 -- causal-insufficiency tier>
}


def payload_for(behavior_class: str, effort: str, task: dict) -> str:
    gen = PAYLOAD_GENERATORS.get(behavior_class)
    if gen is None:
        raise NotImplementedError(
            f"no payload generator registered for behavior_class={behavior_class!r}. "
            "The honest path is implemented; colluder/free-rider generators are build step A.6 "
            "(register them in PAYLOAD_GENERATORS and set behavior_plan entries -- the pipeline "
            "does not change)."
        )
    return gen(effort, task)  # a generator sees only (effort, task) -- never a label or ground truth


# ==============================================================================================
# 3. POPULATION BUILDER  (this is the part that GENERALIZES consensus.build_h1)
# ==============================================================================================
@dataclass
class AgentSpec:
    index: int
    agent_id: str
    behavior_class: str       # harness label (never logged)
    effort: str               # harness label (never logged)


def plan_population(cfg: ScenarioConfig) -> list[AgentSpec]:
    """Turn the config into N agent specs: a DID, a behavior_class, and an effort tier each.
    Effort tiers cycle through `effort_spread` (so any N gets a legitimate spread; N=5 with the
    default spread gives exactly one agent per tier, minimal..evidence)."""
    bplan = cfg.normalised_behavior_plan()
    spread = cfg.effort_spread or list(EFFORT_TIERS)
    specs = []
    for i in range(cfg.n_agents):
        specs.append(AgentSpec(index=i, agent_id=agents.make_did(i),
                               behavior_class=bplan[i], effort=spread[i % len(spread)]))
    return specs


def build_population(ledger: SimulatedLedger, cfg: ScenarioConfig):
    """Spawn an N-agent Mesa population at a spread of effort tiers, have each agent produce an
    (output, action log) for EVERY task and COMMIT the log via `agents.finalize_and_commit`
    (reached through the Mesa `Agent.solve_and_commit` path), then assemble the harness-private
    LABEL STORE. Returns (log_recs, tasks_by_id, agent_ids, label_store).

    This is `consensus.build_h1` generalized: N / effort spread / task set / behavior plan / seeds
    all come from `cfg`; the commit + Mesa-stepping machinery is identical (so it stays in lock
    step with the audited honest path), and the consensus PIPELINE is NOT reimplemented here."""
    specs = plan_population(cfg)

    # one Mesa agent per spec; its ScriptedModelClient returns the per-task payloads in task order
    configs, clients = [], []
    for spec in specs:
        configs.append(agents.AgentConfig(
            agent_id=spec.agent_id, model_id=cfg.model_id,
            seed=cfg.model_seed_base + spec.index, temperature=agents.DEFAULT_TEMPERATURE,
            behavior_class=spec.behavior_class))                 # harness label, never written to a log
        clients.append(agents.ScriptedModelClient(
            *[payload_for(spec.behavior_class, spec.effort, t) for t in cfg.task_set]))

    # Mesa population + scheduler (activation order only; never an agent coordination input)
    model = agents.ConsensusModel(configs, clients, ledger, cfg.task_set[0], seed=cfg.model_rng_seed)
    for t in cfg.task_set:                                       # one Mesa tick per task
        model.current_task = t
        model.step()                                            # -> Agent.solve_and_commit -> agents.finalize_and_commit

    # collect per-(agent,task) committed logs as consensus.LogRec rows
    log_recs = []
    for agent in sorted(model.agents, key=lambda a: a.agent_id):
        for res in agent.results:
            log_recs.append(consensus.LogRec(log_id=res["log_id"], task_id=res["task_id"],
                                             author_id=agent.agent_id))

    # ---- the harness-private LABEL STORE (sec 0.7) ----
    by_id = {s.agent_id: s for s in specs}
    label_store = LabelStore(
        ground_truth={t["task_id"]: str(t["a"] + t["b"]) for t in cfg.task_set},
        attack_label={rec.log_id: (by_id[rec.author_id].behavior_class != "honest") for rec in log_recs},
        effort_tier={s.agent_id: s.effort for s in specs},
        honesty={s.agent_id: s.behavior_class for s in specs},
    )
    tasks_by_id = {t["task_id"]: t for t in cfg.task_set}
    agent_ids = [s.agent_id for s in specs]
    return log_recs, tasks_by_id, agent_ids, label_store


# ==============================================================================================
# 4. HARNESS-OWNED REPORTING PANELS  (additive to run_round's canonical trace -- not duplicated)
# ==============================================================================================
def _rule(title: str) -> None:
    print("\n" + "#" * 86 + f"\n# {title}\n" + "#" * 86)


def print_header(cfg: ScenarioConfig) -> None:
    _rule("A.5 WALKING SKELETON  --  end-to-end scenario harness (OFFLINE / ScriptedModelClient)")
    bplan = cfg.normalised_behavior_plan()
    n_attack = sum(1 for b in bplan if b != "honest")
    print("Pipeline  : A ledger  ->  B agents  ->  C predicate  ->  D- consensus+reputation  "
          "(run via consensus.run_round)")
    print("Scenario  : H1 walking skeleton -- all honest; the H2/H3 attack arms reuse THIS harness "
          "(register a generator + edit behavior_plan).")
    print("\nCONFIG")
    print(f"  agents (N)        : {cfg.n_agents}        behavior_plan : {bplan}  "
          f"({n_attack} attacker(s))")
    print(f"  effort spread     : {_effort_assignment(cfg)}")
    print(f"  model id          : {cfg.model_id}")
    print(f"  peer-set size k   : {cfg.k_peers}        supermajority(reject) : >= {cfg.supermajority:.3f}")
    print(f"  task set          : {[t['task_id'] for t in cfg.task_set]}  "
          f"(types: {sorted({t['task_type'] for t in cfg.task_set})})")
    print(f"  seeds (recorded)  : harness={cfg.harness_seed}  model_base={cfg.model_seed_base}  "
          f"mesa_rng={cfg.model_rng_seed}   [F3: environment, never an agent input]")


def _effort_assignment(cfg: ScenarioConfig) -> list[str]:
    spread = cfg.effort_spread or list(EFFORT_TIERS)
    return [spread[i % len(spread)] for i in range(cfg.n_agents)]


def print_build_trace(cfg: ScenarioConfig, ledger: SimulatedLedger, log_recs, agent_ids,
                      label_store: LabelStore) -> None:
    _rule("BUILD  --  spawn Mesa population, commit logs via agents.finalize_and_commit")
    specs = plan_population(cfg)
    print(f"  spawned {len(agent_ids)} Mesa agents; each solved {len(label_store.ground_truth)} "
          f"task(s) -> committed {len(log_recs)} logs (log-commit path; phase-independent).")
    for s in specs:
        print(f"      agent {consensus._aid(s.agent_id)}  effort={s.effort:<9} "
              f"behavior={s.behavior_class:<8} seed={cfg.model_seed_base + s.index}")
    # sec 0.7 demonstration -- prove no label leaked into any committed log
    rep = label_store.assert_invisible(ledger, log_recs)
    print(f"\n  sec 0.7 check     : scanned {rep['logs_scanned']} committed logs; "
          f"label values {rep['label_values_checked']} appear in 0 logs  ->  OK (no leakage)")
    print("  label store       : harness-PRIVATE; the predicate (C) and consensus (D) are built "
          "without it; only metrics.py reads ground_truth + attack_label.")


def print_cost_vs_baseline(result: dict, cfg: ScenarioConfig) -> None:
    """Verification cost expressed as overhead RELATIVE TO the output-only baseline arm (which
    performs ~0 per-log validation -- it just votes on outputs). This is the v1.4 forward-note
    framing: make the process-vs-output cost contrast explicit."""
    cost = result["verification_cost"]
    n_logs = cost.get("logs", 0)
    proc_ops = (cost.get("verdicts_computed", 0) + cost.get("commits", 0)
                + cost.get("reveals", 0) + cost.get("seals", 0) + cost.get("peer_set_assignments", 0))
    _rule("VERIFICATION COST  --  process arm vs output-only baseline (overhead is the contrast)")
    print("  The baseline arm does NO per-log validation: no predicate run, no commit-reveal, no "
          "seal -- only a plurality vote over outputs.")
    print(f"    process arm  : peer_set_assignments={cost.get('peer_set_assignments',0)}  "
          f"verdicts_computed={cost.get('verdicts_computed',0)}  commits={cost.get('commits',0)}  "
          f"reveals={cost.get('reveals',0)}  seals={cost.get('seals',0)}")
    print(f"                   = {proc_ops} verification operations over {n_logs} logs  "
          f"({cost.get('avg_validators_per_validated_log',0)} validators / validated log, k={cfg.k_peers})")
    print(f"    baseline arm : 0 verification operations (output vote only)")
    print(f"    OVERHEAD     : +{proc_ops} ops vs baseline  "
          f"(~{cost.get('avg_validators_per_validated_log',0)}x predicate evaluations per log; "
          f"baseline = 0)")
    print("  NOTE: this is the PRICE of the detection/robustness the process arm buys; the benefit "
          "(accuracy gap, attack detection) is an H2/H3 phenomenon -- in H1 both arms are 100%.")


def print_label_store(label_store: LabelStore) -> None:
    _rule("EXPERIMENT LABEL STORE  (sec 0.7 -- harness-private; shown for the write-up only)")
    print("  ground_truth (task_id -> answer; read ONLY by metrics.consensus_accuracy):")
    for tid in sorted(label_store.ground_truth):
        print(f"      {tid} -> {label_store.ground_truth[tid]!r}")
    print("  honesty / effort (agent_id -> label; harness bookkeeping; honesty defines attack_label):")
    for aid in sorted(label_store.honesty):
        print(f"      {consensus._aid(aid)}  honesty={label_store.honesty[aid]:<8} "
              f"effort={label_store.effort_tier[aid]}")
    n_atk = sum(1 for v in label_store.attack_label.values() if v)
    print(f"  attack_label (log_id -> bool; read ONLY by metrics.detection_rate/FPR): "
          f"{n_atk} attacker logs / {len(label_store.attack_label)} total")


def print_parity_check(result: dict) -> None:
    """Confirm the GENERALIZED harness is behaviour-preserving: with the H1 config its metrics
    match the locked A.4 H1 baseline (process=baseline=100%, FPR=0, every reputation -> 0.6355,
    verify() True). These quantities are invariant to per-run log_id values."""
    _rule("PARITY CHECK  --  generalized harness vs locked A.4 H1 baseline (behaviour-preserving)")
    proc = result["process_accuracy"]["accuracy"]
    base = result["baseline_accuracy"]["accuracy"]
    fpr = result["false_positive_rate"]
    reps = sorted({round(v, 4) for v in result["final_reputation"].values()})
    ledger_ok = result["ledger_ok"]
    checks = [
        ("process accuracy == 1.0", proc == 1.0),
        ("baseline accuracy == 1.0", base == 1.0),
        ("comparative delta == 0.0", result["comparative"]["delta"] == 0.0),
        ("FPR == 0.0", fpr == 0.0),
        ("detection_rate is N/A (no attackers)", result["detection_rate"] is None),
        ("all reputations == 0.6355", reps == [0.6355]),
        ("whole-ledger verify() is True", ledger_ok is True),
    ]
    for label, ok in checks:
        print(f"      [{'PASS' if ok else 'FAIL'}]  {label}")
    print(f"  -> reputations observed: {reps}   ledger verify(): {ledger_ok}")
    if not all(ok for _, ok in checks):
        raise AssertionError("PARITY FAILED: generalized harness diverged from the A.4 H1 baseline.")


COMPONENT_INTERACTION_SUMMARY = """\
COMPONENT-INTERACTION SUMMARY  (for the Method-section architecture diagram)
============================================================================
Boxes = components; arrows = calls; [brackets] = the artifact that crosses the boundary.
The harness is the experiment driver; it OWNS the RNG and the label store and NEVER lets a
ground-truth/honesty/effort/attack fact cross into the protocol.

  walking_skeleton (HARNESS, A.5)
    |  owns: ScenarioConfig (N,k,supermajority,task set,seeds) + EXPERIMENT LABEL STORE (sec 0.7)
    |
    |-- build_population --------------------------------------------------------------------.
    |       |                                                                                 |
    |       v                                                                                 |
    |   agents.ConsensusModel/Agent (B, Mesa population)                                      |
    |       |  per (agent,task): produce_log() -> [anchorless action log]                     |
    |       |  agents.finalize_and_commit([log]) --> sim_ledger.commit_log                    |
    |       v                                                                                 |
    |   sim_ledger.SimulatedLedger (A, substrate)  <== [committed log, content-addressed]     |
    |                                                                                         |
    |-- consensus.run_round(ledger, log_recs, tasks_by_id, agent_ids,                         |
    |                       ground_truth, attack_labels, k, supermajority, harness_rng) -------'
    |       (this IS the pipeline -- generalized run_h1; the harness does not reimplement it)
    |       |
    |       |  Stage 1 assign_peer_sets:  harness_rng -> [peer set + seed] -> ledger.assign_peer_set
    |       |  Stage 2 compute_log_verdicts: per assigned validator,
    |       |             validation.validate_committed(ledger, log_id) -> [Verdict]
    |       |             (loop CATCHES SchemaError -> reject/exclude, F2PendingError -> defer)
    |       |  Stage 3 commit_all_verdicts -> ledger.commit_verdict  [hiding commitment]   (COMMIT phase)
    |       |           reveal_all_verdicts -> ledger.reveal_verdict [verdict+salt]         (REVEAL phase)
    |       |           (one GLOBAL commit before ANY reveal -- verdict independence; F11 batch-lockstep)
    |       |  Stage 4 detection: supermajority reject over ADMISSIBLE verdicts -> [flag]   (F4)
    |       |  Stage 5 aggregate_round: reputation-weighted over ACCEPT-outcome logs
    |       |             -> metrics.weighted_majority / geometric_median  [consensus value/task]
    |       |  Stage 6 seal_and_update_reputation -> ledger.seal_verdict_set [seal_id];
    |       |             ledger.set_reputation(reason_ref=seal_id)  [asymmetric rep update; F6]
    |       v
    |   metrics (D eval) <== [consensus/task], [flags], [ground_truth], [attack_labels]
    |       consensus_accuracy, baseline_consensus (OUTPUT-ONLY arm), comparative_vs_baseline,
    |       detection_rate, false_positive_rate, verification_cost
    |
    '-- harness panels: cost-vs-baseline overhead, label-store dump, parity check, this summary

WHAT CROSSES EACH BOUNDARY (and what deliberately does NOT)
  harness -> agents      : task spec (id/type/dataset/prompt) + agent config (DID, model, seed,
                           temperature, behavior_class).  behavior_class stays in config/label
                           store; it is NEVER written into a log.
  agents  -> ledger      : the committed action log ONLY (log-commit path; phase-independent).
  harness -> ledger      : the recorded peer-set SEED (audit/reproducibility only -- NOT an agent
                           input; randomness flows harness -> assignment -> ledger, never back).
  C/D     -> ledger      : verdict commitments, revealed verdicts, seals, reputation writes.
  ledger  -> agents      : NOTHING is exposed to an agent as a coordination input (not a beacon).
  label store -> protocol: NOTHING. ground_truth/honesty/effort/attack reach ONLY metrics.py.
"""


def print_component_map() -> None:
    _rule("COMPONENT-INTERACTION SUMMARY  (who calls whom; what crosses each boundary)")
    print(COMPONENT_INTERACTION_SUMMARY)


# ==============================================================================================
# 5. RUN  (build -> generalized pipeline -> harness panels)
# ==============================================================================================
def run_skeleton(cfg: ScenarioConfig = H1_CONFIG, *, verbose: bool = True) -> dict:
    """Run ONE end-to-end scenario and return the full results dict from consensus.run_round
    (augmented with the harness's label store and config)."""
    if verbose:
        print_header(cfg)

    ledger = SimulatedLedger()
    log_recs, tasks_by_id, agent_ids, label_store = build_population(ledger, cfg)
    if verbose:
        print_build_trace(cfg, ledger, log_recs, agent_ids, label_store)

    # --- the pipeline: generalized run_h1. We call run_round directly (NO duplication). ------
    result = consensus.run_round(
        ledger, log_recs, tasks_by_id, agent_ids,
        ground_truth=label_store.ground_truth,        # <- only metrics.py consumes these
        attack_labels=label_store.attack_label,       # <- only metrics.py consumes these
        k=cfg.k_peers, threshold=cfg.supermajority,
        harness_rng=random.Random(cfg.harness_seed),
        round_tick=len(cfg.task_set), verbose=verbose,
    )
    result["label_store"] = label_store
    result["config"] = cfg

    if verbose:
        print_cost_vs_baseline(result, cfg)
        print_label_store(label_store)
        print_parity_check(result)
        print_component_map()
        _rule("DONE  --  H1 walking skeleton ran end-to-end; ledger verify() == "
              f"{result['ledger_ok']}")
        _print_open_items()
    return result


def _print_open_items() -> None:
    print("\nOPEN ITEMS (flagged honestly -- nothing here is fabricated):")
    print("  * LIVE MODEL run is PENDING: this skeleton is OFFLINE (ScriptedModelClient). The real")
    print("    Llama-3.1-8B schema-valid emission rate + honest-log pass rate (validate_live.py)")
    print("    must be run on a machine with Ollama; its numbers are NOT measured here.")
    print("  * ADVERSARIES (A.6) are NOT implemented: only the honest payload generator is")
    print("    registered. H2 (collusion, covertext tier) and H3 (free-riding) plug into THIS")
    print("    harness via PAYLOAD_GENERATORS + behavior_plan, with no pipeline change.")
    print("  * F2 still gates H2: the open-ended task set + semantic/rubric scorer are deferred")
    print("    (the predicate's open_ended branch raises F2-pending; aggregation defers it).")
    print("  * H4 (bounded-Byzantine VALIDATORS) is build steps 7-8 (Option-B fallback noted).")
    print("  * Reproducibility: log metadata.timestamp/runtime_ms are wall-clock, so log_ids vary")
    print("    run-to-run; the reported metrics are invariant in the all-honest case. A fixed")
    print("    timestamp (assemble_log supports timestamp=) + the F10 snapshot/export would give")
    print("    byte-identical cross-machine logs for offline re-verification.")


if __name__ == "__main__":
    run_skeleton(H1_CONFIG)
