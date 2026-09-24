#!/usr/bin/env python3
"""check_reproducibility.py -- compare two results/ directories produced by experiments.py.

results.json must be identical except meta.volatile (generation timestamp, runtime).
RESULTS_SUMMARY.md must be identical except its one "Generated at <timestamp>" line.
Every other output file (CSVs, conversion report, example_logs.json) must be byte-identical.
Exit code 0 = reproducible, 1 = difference found.
"""
import hashlib
import json
import os
import re
import sys

_TS = re.compile(r"Generated at \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
VOLATILE_MD = {"RESULTS_SUMMARY.md": _TS, "generated_RESULTS_SUMMARY.md": _TS}   # audit: corrected + generated pair


def strip_volatile(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d.get("meta", {}).pop("volatile", None)
    return d


def sha(path):
    with open(path, "rb") as f:
        data = f.read()
    pat = VOLATILE_MD.get(os.path.basename(path))
    if pat is not None:                      # mask the single volatile timestamp
        data = pat.sub("Generated at <volatile>", data.decode("utf-8")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def main(a, b):
    ok = True
    ja, jb = strip_volatile(os.path.join(a, "results.json")), strip_volatile(os.path.join(b, "results.json"))
    same = ja == jb
    print(f"results.json (excluding meta.volatile): {'IDENTICAL' if same else 'DIFFERENT'}")
    ok &= same
    files = sorted(set(os.listdir(a)) | set(os.listdir(b)))
    if os.path.exists(os.path.join(a, "generated_results.json")):   # audit: the generated copy is compared the same way
        sg = strip_volatile(os.path.join(a, "generated_results.json")) == strip_volatile(os.path.join(b, "generated_results.json"))
        print(f"generated_results.json (excluding meta.volatile): {'IDENTICAL' if sg else 'DIFFERENT'}")
        ok &= sg
    for f in files:
        if f in ("results.json", "generated_results.json"):
            continue
        pa, pb = os.path.join(a, f), os.path.join(b, f)
        if not (os.path.isfile(pa) and os.path.isfile(pb)):
            print(f"{f}: MISSING in one run"); ok = False; continue
        eq = sha(pa) == sha(pb)
        print(f"{f}: {'IDENTICAL' if eq else 'DIFFERENT'}  sha256={sha(pa)[:16]}")
        ok &= eq
    print("REPRODUCIBLE" if ok else "NOT REPRODUCIBLE")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: check_reproducibility.py RESULTS_DIR_A RESULTS_DIR_B")
    sys.exit(main(sys.argv[1], sys.argv[2]))
