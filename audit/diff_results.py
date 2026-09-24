#!/usr/bin/env python3
"""diff_results.py BEFORE_DIR AFTER_DIR -- every changed/added/removed results.json leaf (excluding
meta.volatile and source hashes), plus changed RESULTS_SUMMARY.md lines and per_log_records.csv rows.
Usage (from the repository root):  python3 audit/diff_results.py BEFORE_RESULTS_DIR AFTER_RESULTS_DIR"""
import csv
import difflib
import json
import os
import sys

A, B = sys.argv[1], sys.argv[2]
ra, rb = json.load(open(os.path.join(A, "results.json"))), json.load(open(os.path.join(B, "results.json")))
SKIP = ("/meta/volatile", "/meta/source_sha256")


def leaves(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from leaves(v, f"{p}/{k}")
    elif isinstance(o, list) and o and isinstance(o[0], (dict, list)):
        for i, v in enumerate(o):
            yield from leaves(v, f"{p}[{i}]")
    else:
        yield p, o


la = {p: v for p, v in leaves(ra) if not p.startswith(SKIP)}
lb = {p: v for p, v in leaves(rb) if not p.startswith(SKIP)}
changed = [(p, la[p], lb[p]) for p in la if p in lb and la[p] != lb[p]]
added = [(p, lb[p]) for p in lb if p not in la]
removed = [(p, la[p]) for p in la if p not in lb]
print(f"results.json leaves: {len(la)} before, {len(lb)} after; changed {len(changed)}, added {len(added)}, removed {len(removed)}")
print("\n== CHANGED")
for p, a, b in changed:
    print(f"{p}\n    before: {str(a)[:200]}\n    after:  {str(b)[:200]}")
print("\n== ADDED (paths)")
for p, b in added:
    print(f"{p} = {str(b)[:160]}")
print("\n== REMOVED")
for p, a in removed:
    print(f"{p} = {str(a)[:160]}")

sa = open(os.path.join(A, "RESULTS_SUMMARY.md"), encoding="utf-8").read().splitlines()
sb = open(os.path.join(B, "RESULTS_SUMMARY.md"), encoding="utf-8").read().splitlines()
print("\n== RESULTS_SUMMARY.md changed lines (timestamp line excluded)")
for ln in difflib.unified_diff(sa[3:], sb[3:], lineterm="", n=0):
    if ln.startswith(("+++", "---", "@@")):
        continue
    print(ln[:400])


def rows(d):
    with open(os.path.join(d, "per_log_records.csv"), newline="") as f:
        return {(r["scenario"], r["N"], r["seed"], r["task_id"], r["agent_id"]): r for r in csv.DictReader(f)}


pa, pb = rows(A), rows(B)
diff = [k for k in pa if k in pb and pa[k] != pb[k]]
print(f"\n== per_log_records.csv: {len(pa)} rows before, {len(pb)} after; {len(diff)} rows differ; "
      f"keys only before {len(set(pa) - set(pb))}, only after {len(set(pb) - set(pa))}")
from collections import Counter
print("differing rows by (scenario, N, task):", dict(Counter((k[0], k[1], k[3]) for k in diff)))
cols = Counter(c for k in diff for c in pa[k] if pa[k][c] != pb[k][c])
print("differing columns:", dict(cols))
