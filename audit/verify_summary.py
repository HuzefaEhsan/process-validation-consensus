#!/usr/bin/env python3
"""verify_summary.py -- check every number in RESULTS_SUMMARY.md against results.json.

Independent of report.py (reads results.json from disk, own formatters, own interval formulas):
  1. PROVENANCE: every numeric token in the summary must be derivable from a value in results.json
     (in one of the canonical renderings) or appear inside a results.json string. The few tokens that
     legitimately come from elsewhere are whitelisted below WITH a reason and listed in the report.
  2. ROW CONSISTENCY: every Markdown table row holding an x/n cell must print the percentage, Wilson
     95% and Clopper-Pearson 95% intervals that x/n implies (Wilson closed form; CP via scipy if present,
     else by bisection on the binomial CDF -- neither shares code with stats_ci.py).
  3. KEY ROWS: headline rows are rebuilt straight from JSON paths and must occur verbatim.
Writes a report and exits 1 on any failure.
"""
from __future__ import annotations

import json
import math
import re
import sys
from statistics import NormalDist

WHITELIST = {  # token -> reason (tokens not derivable from results.json by construction)
    "165": "historical '+165 ops' figure from the proposal (fact sheet), explained by the cost formula",
    "150": "historical '150' cost figure from the proposal (fact sheet)",
    "11": "'15 logs x 11' -- arithmetic behind the historical +165 figure",
    "15": "'15 logs' of the historical H1 run / '<<15=15>>' example annotation text",
}

NUM_RE = re.compile(r"(?<![\w.])[+\-−]?\d+(?:\.\d+)?(?:e[+\-]\d+)?(?![\w])")


# ---------------------------------------------------------------- independent statistics
def wilson(x, n, conf=0.95):
    if n == 0:
        return None
    z = NormalDist().inv_cdf(1 - (1 - conf) / 2)
    p = x / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def _binom_cdf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))


def clopper_pearson(x, n, conf=0.95):
    a = 1 - conf
    try:
        from scipy.stats import beta
        lo = 0.0 if x == 0 else float(beta.ppf(a / 2, x, n - x + 1))
        hi = 1.0 if x == n else float(beta.ppf(1 - a / 2, x + 1, n - x))
        return lo, hi
    except ImportError:
        def solve(f, lo=0.0, hi=1.0):
            for _ in range(200):
                mid = (lo + hi) / 2
                if f(mid):
                    hi = mid
                else:
                    lo = mid
            return (lo + hi) / 2
        lo = 0.0 if x == 0 else solve(lambda p: 1 - _binom_cdf(x - 1, n, p) >= a / 2)
        hi = 1.0 if x == n else solve(lambda p: _binom_cdf(x, n, p) <= a / 2)
        return lo, hi


def fpct(p):
    return f"{100 * p:.1f}%"


def fci(ci):
    return f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


# ---------------------------------------------------------------- provenance universe
def renderings(v):
    out = set()
    if isinstance(v, bool) or v is None:
        return out
    if isinstance(v, int):
        out |= {str(v), f"{v:+d}"}
        return out
    if isinstance(v, float):
        for f in (f"{100 * v:.1f}", f"{v:.4f}", f"{v:.2f}", f"{v:.1f}", f"{v:.0f}", f"{v:g}", repr(v),
                  f"{v:+.1f}", f"{abs(v):.1f}", f"{v:.1e}", f"{100 * v:.0f}"):
            out.add(f)
        if v.is_integer():
            out.add(str(int(v)))
        return out
    if isinstance(v, str):
        return {m.group(0).replace("−", "-") for m in NUM_RE.finditer(v)}
    return out


def universe(obj, acc):
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc |= {m.group(0) for m in re.finditer(r"\d+(?:\.\d+)?", str(k))}
            universe(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            universe(v, acc)
        if len(obj) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in obj):
            pass
    else:
        acc |= renderings(obj)
    return acc


def norm(tok):
    t = tok.replace("−", "-")
    return t


# ---------------------------------------------------------------- checks
def check_rows(md_lines):
    """Rows with exactly one x/n cell: % / Wilson / CP cells must match x/n."""
    checked, fails = 0, []
    for ln, line in enumerate(md_lines, 1):
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        xns = [c for c in cells if re.fullmatch(r"\d+/\d+", c)]
        if len(xns) != 1:
            continue
        x, n = map(int, xns[0].split("/"))
        if n == 0:
            continue
        i = cells.index(xns[0])
        pcts = [c for c in cells[:i] if re.fullmatch(r"\d+\.\d%", c)]
        cis = [c for c in cells[i + 1:] if re.fullmatch(r"\[\d+\.\d, \d+\.\d\]", c)]
        if not pcts and not cis:
            continue
        checked += 1
        exp_p = fpct(x / n)
        if pcts and pcts[-1] != exp_p:
            fails.append(f"line {ln}: {xns[0]} prints {pcts[-1]}, expected {exp_p}")
        if len(cis) >= 1 and cis[0] != fci(wilson(x, n)):
            fails.append(f"line {ln}: {xns[0]} Wilson {cis[0]}, expected {fci(wilson(x, n))}")
        if len(cis) >= 2 and cis[1] != fci(clopper_pearson(x, n)):
            fails.append(f"line {ln}: {xns[0]} CP {cis[1]}, expected {fci(clopper_pearson(x, n))}")
    return checked, fails


def key_rows(res):
    """(description, exact substring expected in the summary), built from JSON paths only."""
    cfg = res["meta"]["config"]
    PN = cfg["primary_N"]
    S = res["scenarios"]

    def prow(label, arm, rec):
        return (f"| {label} | {arm} | {fpct(rec['p'])} | {rec['x']}/{rec['n']} | "
                f"{fci(rec['wilson95'])} | {fci(rec['cp95'])} |")

    out = []
    for sc in ("H1", "H1b", "H3", "H1U"):
        blk = S[sc][f"N{PN}"]["primary"]
        for arm, lab in (("process", "process"), ("baseline", "output-only")):
            a = blk[arm]
            out.append((f"{sc} {lab} accuracy", prow("Consensus accuracy (tasks)", lab, a["consensus_accuracy"])))
            if sc in ("H1", "H3"):
                out.append((f"{sc} {lab} FPR", prow("FPR (honest logs flagged)", lab, a["fpr"])))
            else:
                out.append((f"{sc} {lab} FPR", prow("FPR, all honest logs", lab, a["fpr"])))
            if a["detection_tpr"]:
                out.append((f"{sc} {lab} TPR", prow("Detection TPR (attacker logs flagged)", lab, a["detection_tpr"])))
            if "fpr_divergence_split" in a:
                out.append((f"{sc} {lab} FPR divergent",
                            prow("FPR, divergent logs", lab, a["fpr_divergence_split"]["divergent_logs"])))
    h3 = S["H3"][f"N{PN}"]["primary"]
    for v in cfg["variants"]:
        dp = h3["process"]["detection_per_variant"][v]["logs"]
        db = h3["baseline"]["detection_per_variant"][v]["logs"]
        m = h3["comparative"]["detection_per_variant"][v]["logs"]["mcnemar_exact"]
        pstr = f"p {m['p_fmt']}" if m["p_fmt"].startswith("<") else f"p = {m['p_fmt']}"
        out.append((f"H3 variant {v}",
                    f"| {v} | {dp['n']} | {h3['process']['detection_per_variant'][v]['output_correct_logs']} | "
                    f"{fpct(dp['p'])} | {fci(dp['wilson95'])} | {fpct(db['p'])} | {fci(db['wilson95'])} |"))
        out.append((f"H3 variant {v} McNemar", f"b = {m['b']}, c = {m['c']}, {pstr}"))
    rc = res["rule_c_unfiltered"]
    r = rc["fpr_logs"]
    out.append(("rule (c) whole-file FPR", f"| Honest-log FPR, all tiers | log | {fpct(r['p'])} | {r['x']}/{r['n']} | "
                                           f"{fci(r['wilson95'])} | {fci(r['cp95'])} |"))
    for t, r in rc["fpr_per_tier"].items():
        out.append((f"rule (c) tier {t}", f"| Honest-log FPR, tier *{t}* | log | {fpct(r['p'])} | {r['x']}/{r['n']} |"))
    out.append(("rule (c) per-problem rows", f"{len(rc['per_problem'])}"))
    for pp in rc["per_problem"]:
        out.append((f"rule (c) problem {pp['gsm8k_index']}", f"| {pp['gsm8k_index']} | {pp['annotations']} | "))
    rep = h3["process"]["reputation"]
    out.append(("H3 reputation after 3", f"honest {rep['honest_after_3_updates'][0]:.4f}; free-rider "
                                         f"{rep['free_rider_after_3_updates'][0]:.4f}"))
    return out


def main(md_path, json_path, out_path):
    with open(json_path, encoding="utf-8") as f:
        res = json.load(f)
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    lines = md.splitlines()
    uni = universe(res, set())
    uni = {norm(u) for u in uni}

    tokens, unmatched, whitelisted = 0, [], []
    for ln, line in enumerate(lines, 1):
        for mt in NUM_RE.finditer(line):
            tok = norm(mt.group(0))
            tokens += 1
            if tok in uni or tok.lstrip("+") in uni or tok.lstrip("-") in uni:
                continue
            if tok.lstrip("+-") in WHITELIST:
                whitelisted.append((ln, tok, WHITELIST[tok.lstrip("+-")]))
                continue
            ctx = line[max(0, mt.start() - 40): mt.end() + 40]
            unmatched.append((ln, tok, ctx))

    rows_checked, row_fails = check_rows(lines)
    keys = key_rows(res)
    key_fails = [(d, e) for d, e in keys if e not in md]

    ok = not unmatched and not row_fails and not key_fails
    rep = ["# Summary verification (RESULTS_SUMMARY.md vs results.json)", "",
           f"numeric tokens checked: {tokens}",
           f"  derivable from results.json: {tokens - len(unmatched) - len(whitelisted)}",
           f"  whitelisted (not from results.json, reason given): {len(whitelisted)}",
           f"  UNMATCHED: {len(unmatched)}",
           f"table rows with x/n re-derived (%, Wilson, CP): {rows_checked}; failures: {len(row_fails)}",
           f"key rows rebuilt from JSON paths: {len(keys)}; missing: {len(key_fails)}",
           f"RESULT: {'PASS' if ok else 'FAIL'}", ""]
    if whitelisted:
        rep.append("## Whitelisted tokens")
        seen = set()
        for ln, tok, why in whitelisted:
            if tok not in seen:
                rep.append(f"- {tok}: {why}")
                seen.add(tok)
        rep.append("")
    for title, items in (("## Unmatched tokens", [f"- line {ln}: {tok!r} in ...{ctx}..." for ln, tok, ctx in unmatched]),
                         ("## Row failures", [f"- {x}" for x in row_fails]),
                         ("## Missing key rows", [f"- {d}: {e}" for d, e in key_fails])):
        if items:
            rep.append(title)
            rep.extend(items)
            rep.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rep) + "\n")
    print("\n".join(rep[:9]))
    return 0 if ok else 1


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "results"
    sys.exit(main(f"{d}/RESULTS_SUMMARY.md", f"{d}/results.json", f"{d}/summary_verification.txt"))
