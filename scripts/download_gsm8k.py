#!/usr/bin/env python3
"""download_gsm8k.py -- fetch the GSM8K test split used by the experiments and verify it.

The dataset is not committed to this repository. This script downloads it from the pinned
upstream commit recorded in results/results.json (meta.dataset.source_url), checks its SHA-256
against meta.dataset.file_sha256 in the same file, and only then moves it into place as
data/gsm8k_test.jsonl. It also fetches the upstream MIT licence text to data/GSM8K_LICENSE.

  python3 scripts/download_gsm8k.py            # download if absent, verify in every case
  python3 scripts/download_gsm8k.py --check    # verify an existing file only; never download

Exit status 0 = verified. Any mismatch, missing record or network failure exits non-zero with a
message; a file that fails verification is never left at the target path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_JSON = os.path.join(ROOT, "results", "results.json")
DATA_DIR = os.path.join(ROOT, "data")
DATA_PATH = os.path.join(DATA_DIR, "gsm8k_test.jsonl")
LICENSE_PATH = os.path.join(DATA_DIR, "GSM8K_LICENSE")
# The upstream LICENSE file at the same pinned commit (MIT, Copyright (c) 2021 OpenAI).
LICENSE_SHA256 = "86bbb73e855821d7c401912fd4bf82e34313e6e3b6fd6f909f2b6cc9e209a53b"


def fail(msg: str) -> "None":
    sys.stderr.write("\n*** GSM8K DATA CHECK FAILED ***\n" + msg.rstrip() + "\n\n")
    sys.exit(1)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def recorded_dataset() -> dict:
    if not os.path.isfile(RESULTS_JSON):
        fail(f"{RESULTS_JSON} is missing, so the expected SHA-256 is unknown.\n"
             "Restore it with:  git checkout -- results/results.json")
    try:
        with open(RESULTS_JSON, encoding="utf-8") as f:
            ds = json.load(f)["meta"]["dataset"]
        return {"url": ds["source_url"], "sha256": ds["file_sha256"],
                "commit": ds["upstream_commit"], "repo": ds["source_repo"]}
    except (KeyError, ValueError) as e:
        fail(f"cannot read meta.dataset from {RESULTS_JSON}: {e!r}\n"
             "Restore it with:  git checkout -- results/results.json")


def fetch_verified(url: str, expected: str, target: str, label: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".download-")
    os.close(fd)
    try:
        print(f"downloading {label} from {url}")
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
            out.write(r.read())
        got = sha256(tmp)
        if got != expected:
            fail(f"{label}: downloaded file has SHA-256 {got}\n"
                 f"expected {expected}\nThe file was discarded; nothing was written to {target}.")
        os.replace(tmp, target)
        os.chmod(target, 0o644)
        print(f"{label}: SHA-256 OK ({expected})")
    except OSError as e:
        fail(f"{label}: download from {url} failed: {e!r}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def verify_existing(path: str, expected: str, label: str) -> None:
    got = sha256(path)
    if got != expected:
        fail(f"{label}: {path} has SHA-256 {got}\nexpected {expected}\n"
             f"Delete the file and rerun this script to fetch the pinned version.")
    print(f"{label}: SHA-256 OK ({expected})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify existing files only; never download")
    args = ap.parse_args(argv)
    os.makedirs(DATA_DIR, exist_ok=True)
    ds = recorded_dataset()
    print(f"GSM8K test split, {ds['repo']} @ {ds['commit']}")

    if os.path.isfile(DATA_PATH):
        verify_existing(DATA_PATH, ds["sha256"], "gsm8k_test.jsonl")
    elif args.check:
        fail(f"{DATA_PATH} is not present. Run:  python3 scripts/download_gsm8k.py")
    else:
        fetch_verified(ds["url"], ds["sha256"], DATA_PATH, "gsm8k_test.jsonl")

    lic_url = f"https://raw.githubusercontent.com/openai/grade-school-math/{ds['commit']}/LICENSE"
    if os.path.isfile(LICENSE_PATH):
        verify_existing(LICENSE_PATH, LICENSE_SHA256, "GSM8K_LICENSE")
    elif not args.check:
        fetch_verified(lic_url, LICENSE_SHA256, LICENSE_PATH, "GSM8K_LICENSE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
