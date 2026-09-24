#!/usr/bin/env python3
"""audit/independent_checks.py -- independent checks of the P2 results (audit of 2026-09-23).

The CHECKING code here (ind_predicate, the scans, the counts) is written from the documented gate
definitions and imports neither validation.py nor metrics.py. Logs are REGENERATED through the shipped
generators (the object under audit) and matched to per_log_records.csv by content-addressed log_id.

Usage: python3 audit/independent_checks.py RESULTS_DIR
Writes RESULTS_DIR/audit_checks.json, RESULTS_DIR/conversion_sample.md, RESULTS_DIR/rule_c_examples.md.
Deterministic: no timestamps, fixed sample seed.
"""
import ast
import csv
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))   # repository layout: modules live in src/

RES = sys.argv[1]
SAMPLE_SEED = 2027

# ============================================================ independent predicate ============
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div,
            ast.Pow, ast.Mod, ast.FloorDiv, ast.UAdd, ast.USub, ast.Call, ast.Name, ast.Load, ast.List, ast.Tuple)
_ENV = {"__builtins__": {},
        "sum": lambda *a: sum(a[0]) if len(a) == 1 and isinstance(a[0], (list, tuple)) else sum(a),
        "min": min, "max": max, "abs": abs, "round": round}


def ind_eval(expr):
    tree = ast.parse(expr, mode="eval")
    for n in ast.walk(tree):
        if not isinstance(n, _ALLOWED):
            raise ValueError(type(n).__name__)
        if isinstance(n, ast.Name) and n.id not in _ENV:
            raise ValueError("name")
        if isinstance(n, ast.Constant) and (isinstance(n.value, bool) or not isinstance(n.value, (int, float))):
            raise ValueError("const")
    return eval(compile(tree, "<expr>", "eval"), _ENV)


def _num(x):
    if isinstance(x, bool):
        return ("b", x)
    if isinstance(x, (int, float)):
        return ("n", x)
    s = str(x).strip()
    for f in (int, float):
        try:
            return ("n", f(s))
        except ValueError:
            pass
    return ("s", s)


def eq(a, b):
    ka, kb = _num(a), _num(b)
    return ka[1] == kb[1] if ka[0] == kb[0] == "n" else ka == kb


def ind_predicate(log):
    steps, ops = log["reasoning_steps"], {o["op_id"]: o for o in log["tool_operations"]}
    term, fv, n = log["final_output"]["derived_from_step"], log["final_output"]["value"], len(log["reasoning_steps"])
    earlier = all(isinstance(d, int) and 0 <= d < i for i, s in enumerate(steps) for d in s["depends_on"])
    used = {d for s in steps for d in s["depends_on"]}
    orphans = [i for i in range(n) if i != term and i not in used]
    pc = earlier and 0 <= term < n and not orphans
    cone, st = set(), [term]
    while st:
        i = st.pop()
        if i in cone or not 0 <= i < n:
            continue
        cone.add(i)
        st += steps[i]["depends_on"]
    tu = all(any(s.get("tool_op_ref") == oid and i in cone for i, s in enumerate(steps)) for oid in ops)
    verified, c1 = {}, True
    for oid, o in ops.items():
        if o["deterministic"] is True:
            try:
                if o["tool_name"] != "calculator":
                    raise ValueError("tool")
                v = ind_eval(o["inputs"]["expr"])
                ok = eq(v, o["output"])
            except Exception:
                v, ok = None, False
            verified[oid] = v if ok else None
            c1 &= ok
        else:
            verified[oid] = o["output"]
    c2 = all(verified.get(s["tool_op_ref"]) is not None and eq(s["produces"], verified[s["tool_op_ref"]])
             for s in steps if s.get("tool_op_ref") is not None and s.get("produces") is not None)
    c3 = steps[term].get("produces") is not None and eq(steps[term]["produces"], fv)
    c4 = any(steps[i].get("tool_op_ref") in ops and ops[steps[i]["tool_op_ref"]]["deterministic"] is True
             and verified.get(steps[i]["tool_op_ref"]) is not None and eq(verified[steps[i]["tool_op_ref"]], fv)
             for i in cone)
    cs = c1 and c2 and c3 and c4
    return {"decision": "accept" if (pc and tu and cs) else "reject", "process_coherence": pc,
            "tool_utilization": tu, "causal_sufficiency": cs, "C1": c1, "C2": c2, "C3": c3, "C4": c4,
            "orphans": orphans}


# ============================================================ regenerate ========================
import consensus                                  # noqa: E402  (pipeline under audit, used only to regenerate)
import experiments as E                           # noqa: E402
import gsm8k_logs as g                            # noqa: E402
import walking_skeleton as ws                     # noqa: E402
from sim_ledger import SimulatedLedger            # noqa: E402

rows = g.load_rows(E.DATA_PATH)
conv = g.convert(rows, E.N_TASKS)
selected = conv["selected"]
tasks = [g.attach_divergence(g.make_task(p, i)) for i, p in enumerate(selected)]
answers = {p.task_id: p.answer for p in selected}
ab_first = E.pass_ab(conv["problems"])[:E.N_TASKS]
tasks_u = []
for i, p in enumerate(ab_first):
    t = g.make_task(p, i)
    t["rule_c"] = g.R_INCOMPLETE in p.reasons
    tasks_u.append(t)
answers_u = {p.task_id: p.answer for p in ab_first}
E.H1B_ASSIGN[30] = E.h1b_assignment(30, tasks)
E.register_generators(h1b_ns=[30])

GATES = ["process_coherence", "tool_utilization", "causal_sufficiency"]
SUBS = ["C1", "C2", "C3", "C4"]
csv_rows = defaultdict(dict)
with open(os.path.join(RES, "per_log_records.csv"), newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["N"] == "30" and r["seed"] == str(E.PRIMARY_SEED) and r["scenario"] in ("H1", "H1b", "H3", "H1U"):
            csv_rows[r["scenario"]][r["log_id"]] = r

LABEL_VALUES = {"honest", "honest_divergent", "free_rider", "divergent", "wrong_block", "attack", "attacker",
                "minimal", "terse", "standard", "verbose", "evidence", "bare_assertion_wrong",
                "bare_assertion_correct", "forged_op", "orphan_op", "bare_assertion"}
LABEL_SUBSTRINGS = ["honest", "free_rider", "free-rider", "attack", "divergent", "bare_assertion", "forged",
                    "orphan", "wrong_block", "ground_truth", "gsm8k_index", "is_attack", "behavior_class",
                    "behaviour", "effort"]
STRICT_TOKENS = ["reference", "position", "gsm8k_index", "merged_expr", "deps"]


def strings(o, path=""):
    if isinstance(o, str):
        yield path, o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield path + "/" + k + "#key", k
            yield from strings(v, path + "/" + k)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v, path + "[]")


OUT = {"sample_seed": SAMPLE_SEED}
strict = {t: {"logs_containing": 0, "paths": Counter()} for t in STRICT_TOKENS}
strict_logs_checked = 0
indep = {"logs_checked": 0, "gate_or_decision_mismatches": 0}
leak = {"logs_scanned": 0, "exact_label_values": 0, "label_substrings_outside_prompt": 0,
        "tasks_with_extra_keys": 0, "model_ids": Counter()}
log_id_match = {}


def build(mode, n_fr, tset, ans):
    cfg = ws.ScenarioConfig(n_agents=30, model_id=g.SCRIPTED_MODEL_ID, effort_spread=list(E.TIERS),
                            behavior_plan=E.behavior_plan(30, mode, n_fr), k_peers=E.K,
                            supermajority=E.THRESHOLD, task_set=tset, harness_seed=E.PRIMARY_SEED)
    ledger = SimulatedLedger()
    return (ledger,) + E.build_population(ledger, cfg, ans)


tier_payloads, tier_struct = defaultdict(dict), defaultdict(dict)
for name, mode, n_fr, tset, ans in [("H1", "honest", 0, tasks, answers), ("H1b", "h1b", 0, tasks, answers),
                                     ("H3", "round_robin", 3, tasks, answers), ("H1U", "honest", 0, tasks_u, answers_u)]:
    ledger, recs, tbid, aids, ls, meta, specs = build(mode, n_fr, tset, ans)
    log_id_match[name] = {r.log_id for r in recs} == set(csv_rows[name])
    for r in recs:
        log = ledger.get_log(r.log_id)
        ip = ind_predicate(log)
        row = csv_rows[name][r.log_id]
        indep["logs_checked"] += 1
        bad = (ip["decision"] != row["predicate_decision"]
               or any(str(ip[k]) != row["gate_" + k] for k in GATES) or any(str(ip[k]) != row[k] for k in SUBS))
        indep["gate_or_decision_mismatches"] += int(bad)
        # leakage
        leak["logs_scanned"] += 1
        leak["model_ids"][log["model_id"]] += 1
        if set(log["task"]) != {"task_id", "task_type", "dataset", "prompt"}:
            leak["tasks_with_extra_keys"] += 1
        for path, s in strings(log):
            if path.startswith("/task/prompt"):
                continue
            leak["exact_label_values"] += int(s in LABEL_VALUES)
            leak["label_substrings_outside_prompt"] += sum(1 for lab in LABEL_SUBSTRINGS if lab in s.lower())
        # strict form of the relaxed test: substring anywhere in the serialised log
        dumped = json.dumps(log)
        strict_logs_checked += 1
        for tok in STRICT_TOKENS:
            if tok in dumped:
                strict[tok]["logs_containing"] += 1
                for path, s in strings(log):
                    if tok in s:
                        strict[tok]["paths"][re.sub(r"\[\]", "[i]", path)] += 1
        # tier distinctness (H1 only)
        if name == "H1":
            m = meta[r.log_id]
            payload = {k: log[k] for k in ("reasoning_steps", "tool_operations", "final_output")}
            ph = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            struct = {"s": [(s["step_type"], s["depends_on"], s["produces"], s["tool_op_ref"]) for s in log["reasoning_steps"]],
                      "o": [(o["tool_name"], str(ind_eval(o["inputs"]["expr"])) if o["tool_name"] == "calculator" else "ev",
                             str(o["output"]), o["deterministic"]) for o in log["tool_operations"]],
                      "f": log["final_output"]}
            sh = hashlib.sha256(json.dumps(struct, sort_keys=True, default=str).encode()).hexdigest()
            tier_payloads[r.task_id].setdefault(m["effort_tier"], set()).add(ph)
            tier_struct[r.task_id][m["effort_tier"]] = (sh, len(log["reasoning_steps"]), m["gsm8k_index"])
    if name == "H3":
        res = consensus.run_round(ledger, recs, tbid, aids, ground_truth=ls.ground_truth, attack_labels=ls.attack_label,
                                  k=E.K, threshold=E.THRESHOLD, harness_rng=random.Random(E.PRIMARY_SEED),
                                  round_tick=len(tset), verbose=False)
        fr = {s.agent_id for s in specs if not E.is_honest_class(s.behavior_class)}
        va = {"revealed_verdicts": 0, "disagreeing_with_independent_predicate": 0, "free_rider_verdicts": 0,
              "free_rider_verdicts_disagreeing": 0, "self_validations": 0}
        fr_logs, fr_atk = set(), set()
        for r in recs:
            ip = ind_predicate(ledger.get_log(r.log_id))
            for v in ledger.get_verdicts(r.log_id, phase="revealed"):
                ok = v["decision"] == ip["decision"] and all(v["rule_results"][k] == ip[k] for k in GATES)
                va["revealed_verdicts"] += 1
                va["disagreeing_with_independent_predicate"] += int(not ok)
                va["self_validations"] += int(v["validator_id"] == r.author_id)
                if v["validator_id"] in fr:
                    va["free_rider_verdicts"] += 1
                    va["free_rider_verdicts_disagreeing"] += int(not ok)
                    fr_logs.add(r.log_id)
                    if ls.attack_label[r.log_id]:
                        fr_atk.add(r.log_id)
        va["logs_with_a_free_rider_validator"] = len(fr_logs)
        va["attacker_logs_with_a_free_rider_validator"] = len(fr_atk)
        OUT["h3_validator_audit"] = va
        chain = ledger.get_history()
        by_type = dict(sorted(Counter(tx["tx_type"] for tx in chain).items()))
        genesis = sum(1 for tx in chain if tx["tx_type"] == "reputation_set" and tx["payload"]["reason_ref"] == "genesis-init")
        n_logs = len(recs)
        proto = (by_type.get("peer_set", 0) + by_type.get("verdict_commit", 0) + by_type.get("verdict_reveal", 0)
                 + by_type.get("verdict_seal", 0) + by_type.get("reputation_set", 0) - genesis)
        OUT["h3_ledger_count"] = {"tx_by_type": by_type, "genesis_reputation_records": genesis, "logs": n_logs,
                                  "total_tx": len(chain), "protocol_tx": proto,
                                  "protocol_tx_per_log": round(proto / n_logs, 4),
                                  "all_tx_per_log": round(len(chain) / n_logs, 4)}
    del ledger

OUT["log_id_sets_equal_to_csv"] = log_id_match
OUT["independent_predicate_primary"] = indep
leak["model_ids"] = dict(leak["model_ids"])
OUT["leakage"] = leak
OUT["strict_reference_test"] = {
    "reconstructed_strict_form": "assert token not in json.dumps(log) for each token (substring form); the "
                                 "original assertion text is not in the package, so this is a reconstruction",
    "logs_checked": strict_logs_checked,
    "tokens": {t: {"logs_containing": v["logs_containing"], "paths": dict(v["paths"].most_common(6))}
               for t, v in strict.items()}}

# tiers
dp = Counter(len({next(iter(s)) for s in d.values()}) for d in tier_payloads.values())
within = all(len(s) == 1 for d in tier_payloads.values() for s in d.values())
dsteps = Counter(len({v[1] for v in d.values()}) for d in tier_struct.values())
same_struct = sorted({(v["minimal"][2], a, b) for d in tier_struct.values() for v in [d]
                      for a in E.TIERS for b in E.TIERS if a < b and d[a][0] == d[b][0]})
OUT["tiers"] = {"tasks": len(tier_payloads),
                "tasks_with_5_distinct_payloads": dp.get(5, 0),
                "payload_byte_identical_within_tier": within,
                "tasks_with_5_distinct_step_counts": dsteps.get(5, 0),
                "same_computation_tier_pairs": [{"gsm8k_index": i, "tiers": [a, b]} for i, a, b in same_struct]}

# ============================================================ whole-file rule (c), independent ===========
abp = E.pass_ab(conv["problems"])
wf = {"problems": len(abp), "logs": 0, "rejected": 0, "per_tier": Counter(), "gate_failures": Counter(),
      "rejected_problems": 0, "all_rejections_on_rule_c_problems": True, "decision_mismatch_vs_predicate": 0}
for p in abp:
    task = g.make_task(p, 0)
    any_rej = False
    for t in E.TIERS:
        log = g.build_log(g.honest_payload(t, task), task)
        ip = ind_predicate(log)
        wf["logs"] += 1
        wf["decision_mismatch_vs_predicate"] += int(ip["decision"] != g.gate_breakdown(log)["decision"])
        if ip["decision"] == "reject":
            any_rej = True
            wf["rejected"] += 1
            wf["per_tier"][t] += 1
            for k in GATES + SUBS:
                wf["gate_failures"][k] += int(not ip[k])
    if any_rej:
        wf["rejected_problems"] += 1
        wf["all_rejections_on_rule_c_problems"] &= g.R_INCOMPLETE in p.reasons
wf["per_tier"] = {t: wf["per_tier"].get(t, 0) for t in E.TIERS}
wf["gate_failures"] = {k: wf["gate_failures"].get(k, 0) for k in GATES + SUBS}
OUT["independent_predicate_whole_file"] = wf

# ============================================================ evasion probe (limitation) =================
def _dump(steps, ops, v, d):
    return json.dumps({"reasoning_steps": steps, "tool_operations": ops, "final_output": {"value": v, "derived_from_step": d}})


probe = {"tasks": 0, "identity_op_accepted_by_predicate": 0, "identity_op_accepted_independent": 0,
         "times_one_op_accepted_by_predicate": 0, "answer_appears_in_question": 0}
import validation  # noqa: E402  (only to show the SHIPPED predicate's decision on the probe logs)
for i, p in enumerate(selected):
    t = g.make_task(p, i)
    A = t["reference"]["annotations"][-1]["value"]
    l1 = g.build_log(_dump([g._step(0, "tool_call", f"Compute {A}.", [], A, "op-0")], [g._calc_op(0, A, A)], A, 0), t)
    l2 = g.build_log(_dump([g._step(0, "inference", "Recall the quantities.", []),
                            g._step(1, "tool_call", f"Compute {A}*1.", [0], A, "op-0"),
                            g._step(2, "decision", f"The answer is {A}.", [1], A)],
                           [g._calc_op(0, f"{A}*1", A)], A, 2), t)
    probe["tasks"] += 1
    probe["identity_op_accepted_by_predicate"] += int(validation.validate(l1).decision == "accept")
    probe["identity_op_accepted_independent"] += int(ind_predicate(l1)["decision"] == "accept")
    probe["times_one_op_accepted_by_predicate"] += int(validation.validate(l2).decision == "accept")
    probe["answer_appears_in_question"] += int(any(eq(q, p.answer) for q in re.findall(r"\d+\.\d*|\.\d+|\d+", p.question.replace(",", ""))))
OUT["evasion_probe"] = probe

# ============================================================ H1-U vs primary sample ======================
hu, pr = [p.index for p in ab_first], [p.index for p in selected]
OUT["h1u_vs_primary"] = {"h1u_last_index": hu[-1], "primary_last_index": pr[-1], "shared": len(set(hu) & set(pr)),
                         "h1u_only": sorted(set(hu) - set(pr)), "primary_only": sorted(set(pr) - set(hu))}

# ============================================================ conversion faithfulness =====================
def faithful(p):
    task = g.make_task(p, 0)
    log = g.build_log(g.honest_payload("standard", task), task)
    ops = [o for o in log["tool_operations"] if o["tool_name"] == "calculator"]
    return {"ops_match_annotations": [o["inputs"]["expr"] for o in ops] == [a.expr for a in p.annotations]
            and all(eq(o["output"], a.value) for o, a in zip(ops, p.annotations)),
            "final_equals_gsm8k_answer": eq(log["final_output"]["value"], p.answer),
            "log": log}


fa = [faithful(p) for p in selected]
OUT["conversion_faithfulness_all_selected"] = {
    "problems": len(selected), "ops_match_annotations": sum(x["ops_match_annotations"] for x in fa),
    "final_equals_gsm8k_answer": sum(x["final_equals_gsm8k_answer"] for x in fa)}
sample = sorted(random.Random(SAMPLE_SEED).sample(range(len(selected)), 10))
OUT["conversion_sample_indices"] = [selected[i].index for i in sample]


def render_log(log):
    out = ["| Step | Type | Depends on | Produces | Op | Content |", "| ---: | --- | --- | --- | --- | --- |"]
    for s in log["reasoning_steps"]:
        out.append(f"| {s['step_index']} | {s['step_type']} | {s['depends_on']} | {s['produces']} | "
                   f"{s['tool_op_ref'] or ''} | {s['content'].replace('|', '/')} |")
    out += ["", "| Op | Tool | Input | Output | Deterministic |", "| --- | --- | --- | --- | --- |"]
    for o in log["tool_operations"]:
        inp = o["inputs"].get("expr", json.dumps(o["inputs"]))
        out.append(f"| {o['op_id']} | {o['tool_name']} | `{inp}` | {json.dumps(o['output'])} | {o['deterministic']} |")
    out.append(f"\nFinal output: {log['final_output']['value']} (from step {log['final_output']['derived_from_step']})")
    return "\n".join(out)


md = ["# GSM8K Conversion Sample (Audit)", "",
      f"Ten of the 100 selected problems, drawn with `random.Random({SAMPLE_SEED}).sample`. Each shows the "
      "question, the reference solution verbatim, and the *standard*-tier log generated from it. Mechanical "
      f"checks over all 100 selected problems: calculator ops equal the annotations in order "
      f"{OUT['conversion_faithfulness_all_selected']['ops_match_annotations']}/100; final value equals the "
      f"`####` answer {OUT['conversion_faithfulness_all_selected']['final_equals_gsm8k_answer']}/100.", ""]
for i in sample:
    p, x = selected[i], fa[i]
    md += [f"## GSM8K Test Index {p.index}", "", "**Question.** " + rows[p.index]["question"], "",
           "**Reference solution.**", "", "```", rows[p.index]["answer"], "```", "",
           "**Generated log (standard tier).**", "", render_log(x["log"]), "",
           f"Checks: ops match annotations = {x['ops_match_annotations']}; final equals `####` answer = "
           f"{x['final_equals_gsm8k_answer']}; independent predicate = {ind_predicate(x['log'])['decision']}.", ""]
open(os.path.join(RES, "conversion_sample.md"), "w", encoding="utf-8").write("\n".join(md))

# ============================================================ rule (c) examples ===========================
by_index = {p.index: p for p in conv["problems"]}
ex_md = ["# Rule (c) Examples (Audit)", "",
         "Three problems that pass rules (a)+(b) but not rule (c), shown in full with the *terse*-tier log "
         "(one calculator step per annotation) and the gates it fails. Index 489 is shown as it was wired "
         "before the negative-literal fix (audit D8) and after it.", ""]
for idx, why in [(265, "arithmetic continued in prose without an annotation"),
                 (52, "an identity annotation stands in for a computation done in prose"),
                 (489, "converter defect: a negative intermediate result was not wired (fixed, D8)")]:
    p = by_index[idx]
    variants = [("as converted", p)]
    if idx == 489:
        q = g.Problem(index=p.index, question=p.question, answer=p.answer, annotations=p.annotations)
        q.deps = [[] for _ in p.annotations]
        q.merged_expr = p.annotations[-1].expr
        variants = [("before the fix (deps [[], []])", q), ("after the fix", p)]
    ex_md += [f"## GSM8K Test Index {idx} — {why}", "", "**Question.** " + rows[idx]["question"], "",
              "**Reference solution.**", "", "```", rows[idx]["answer"], "```", ""]
    for label, pp in variants:
        task = g.make_task(pp, 0)
        log = g.build_log(g.honest_payload("terse", task), task)
        ip = ind_predicate(log)
        ex_md += [f"**Terse log, {label}.** Dependency wiring {pp.deps}.", "", render_log(log), "",
                  f"Gates: process coherence {ip['process_coherence']} (orphan steps {ip['orphans']}), tool "
                  f"utilization {ip['tool_utilization']}, causal sufficiency {ip['causal_sufficiency']} "
                  f"(C1 {ip['C1']}, C2 {ip['C2']}, C3 {ip['C3']}, C4 {ip['C4']}); decision **{ip['decision']}**.", ""]
open(os.path.join(RES, "rule_c_examples.md"), "w", encoding="utf-8").write("\n".join(ex_md))

json.dump(OUT, open(os.path.join(RES, "audit_checks.json"), "w", encoding="utf-8"), indent=1, sort_keys=True, default=str)
print(json.dumps({k: v for k, v in OUT.items() if k not in ("strict_reference_test",)}, indent=1, default=str)[:4000])
print(json.dumps(OUT["strict_reference_test"], indent=1)[:2500])
