#!/usr/bin/env python3
"""audit/verify_corrected.py -- check the corrected RESULTS_SUMMARY.md against the corrected results.json.
1. PROVENANCE: every numeric token in the summary is a rendering of some value in results.json (int, 1/2/4-dp,
   percent at 1 dp, or a number printed inside a results.json string). Whitelisted tokens carry a reason.
2. ROWS: every table row with an 'x/n' cell followed by a percentage and two intervals must print the value,
   Wilson 95% and Clopper-Pearson 95% intervals implied by x/n (own formulas; scipy beta for CP).
Writes RESULTS_DIR/corrected_verification.txt; exit 1 on any failure."""
import json, math, os, re, sys
from statistics import NormalDist
from scipy.stats import beta
RES = sys.argv[1]
S = open(os.path.join(RES, "RESULTS_SUMMARY.md"), encoding="utf-8").read()
R = json.load(open(os.path.join(RES, "results.json"), encoding="utf-8"))
WHITELIST = {"165": "historical '+165 ops' figure (proposal), explained by the cost formula",
             "150": "historical '150' figure (proposal) / free-rider cells of 150 logs derivable as 75 + 75",
             "11": "'15 logs x 11' behind the historical +165 figure",
             "15": "'15 logs' of the historical H1 run / '<<15=15>>' annotation text",
             "256": "'SHA-256' (hash name)",
             "350000": "GSM8K index 265 reference-solution value quoted as an example (dataset text)",
             "17500": "GSM8K index 265 reference-solution value quoted as an example (dataset text)",
             "42000": "GSM8K index 265 reference-solution value quoted as an example (dataset text)"}
NUM = re.compile(r"(?<![\w.])[+\-−]?\d+(?:\.\d+)?(?:e[+\-]\d+)?(?![\w])")
U = set()
def add(v):
    if isinstance(v, bool) or v is None: return
    if isinstance(v, (int, float)):
        for f in (lambda x: str(int(x)) if float(x).is_integer() else str(x), lambda x: f"{x:.1f}", lambda x: f"{x:.2f}",
                  lambda x: f"{x:.4f}", lambda x: f"{100*x:.1f}", lambda x: f"{x:g}", lambda x: f"{x:+.1f}", lambda x: f"{x:.3f}", lambda x: f"{x:.0f}"):
            try: U.add(f(v).lstrip("+"))
            except Exception: pass
    elif isinstance(v, str):
        for t in NUM.findall(v): U.add(t.lstrip("+"))
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            add(k) if not isinstance(k, str) else [U.add(t) for t in NUM.findall(k)]
            walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
    else: add(o)
walk(R)
bad, wl = [], []
text = S.split("\n", 2)[2]               # skip title + generated-at line (timestamp)
for ln in text.split("\n"):
    for t in NUM.findall(ln):
        tt = t.replace("−", "-").lstrip("+")
        if tt in U or tt.lstrip("-") in U: continue
        if tt in WHITELIST: wl.append(tt); continue
        bad.append((tt, ln[:110]))
def wilson(x, n, conf=0.95):
    z = NormalDist().inv_cdf(1 - (1 - conf) / 2); p = x / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, c - h), min(1, c + h)
def cp(x, n, conf=0.95):
    a = 1 - conf
    return (0.0 if x == 0 else beta.ppf(a / 2, x, n - x + 1), 1.0 if x == n else beta.ppf(1 - a / 2, x + 1, n - x))
f = lambda ci: f"[{100*ci[0]:.1f}, {100*ci[1]:.1f}]"
rows_checked, row_bad = 0, []
for ln in S.split("\n"):
    m = re.search(r"\| ([\d.]+)% \| (\d+)/(\d+) \| (\[[^\]]+\]) \| (\[[^\]]+\]) \|", ln)
    if not m: continue
    rows_checked += 1
    x, n = int(m.group(2)), int(m.group(3))
    if (f"{100*x/n:.1f}" != m.group(1) or f(wilson(x, n)) != m.group(4) or f(cp(x, n)) != m.group(5)):
        row_bad.append(ln[:120])
# inline "x/n (p%)" cells
for m in re.finditer(r"(\d+)/(\d+) \(([\d.]+)%\)", S):
    x, n = int(m.group(1)), int(m.group(2)); rows_checked += 1
    if f"{100*x/n:.1f}" != m.group(3): row_bad.append(m.group(0))
out = [f"numeric tokens unmatched: {len(bad)}", f"whitelisted tokens used: {sorted(set(wl))}",
       f"x/n rows and cells checked: {rows_checked}; inconsistent: {len(row_bad)}"]
out += [f"  UNMATCHED {t!r} in: {l}" for t, l in bad] + [f"  BAD ROW {r}" for r in row_bad]
out.append("RESULT: " + ("PASS" if not bad and not row_bad else "FAIL"))
open(os.path.join(RES, "corrected_verification.txt"), "w").write("\n".join(out) + "\n")
print("\n".join(out))
sys.exit(0 if not bad and not row_bad else 1)
