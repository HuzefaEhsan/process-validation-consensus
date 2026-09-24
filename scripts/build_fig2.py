#!/usr/bin/env python3
"""build_fig2.py RESULTS_JSON OUTDIR
Builds Fig. 2 (SVG + 1200-dpi PNG + PDF) from the audited results.json ONLY. Every rendered number
is taken from the file's own `fmt` strings and re-checked against its raw x/n and Wilson values; any
mismatch aborts the build. This is the figure section of the authors' float-build script, unchanged;
the part that renders manuscript tables and captions is not included in this repository.

Usage (from the repository root):  python3 scripts/build_fig2.py results/results.json figures/
Needs matplotlib and the Liberation Sans font."""
import json
import math
import os
import sys
from statistics import NormalDist

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

RJ, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
R = json.load(open(RJ, encoding="utf-8"))
USED = []                                     # (label, rendered) for the provenance list

# ------------------------------------------------------------------ checked accessors
def wilson(x, n, conf=0.95):
    z = NormalDist().inv_cdf(1 - (1 - conf) / 2); p = x / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def ci(lo, hi):
    return f"[{100 * lo:.1f}, {100 * hi:.1f}]"


def prop(r, label):
    """x/n and Wilson from a results.json proportion record; verified three ways."""
    x, n = r["x"], r["n"]
    assert r["fmt"]["x_n"] == f"{x}/{n}", label
    assert r["fmt"]["p"] == f"{100 * x / n:.1f}%", label
    w = wilson(x, n)
    assert ci(*w) == r["fmt"]["wilson95"] == ci(*r["wilson95"]), (label, ci(*w), r["fmt"]["wilson95"])
    USED.append((label, f"{x}/{n} {r['fmt']['wilson95']}"))
    return r


def xn(r):
    return r["fmt"]["x_n"]


def xnw(r):
    return f"{r['fmt']['x_n']} {r['fmt']['wilson95']}"


def rep4(v):
    return f"{v:.4f}"


S = R["scenarios"]
H3 = S["H3"]["N30"]["primary"]
H1 = S["H1"]["N30"]["primary"]
HB = S["H1b"]["N30"]["primary"]
AU = R["audit"]
CFG, DS = R["meta"]["config"], R["meta"]["dataset"]
VAR = CFG["variants"]
assert VAR == ["bare_assertion_wrong", "bare_assertion_correct", "forged_op", "orphan_op"]
assert H3["N"] == 30 and H3["n_free_riders"] == 3 and H3["attacker_logs"] == 300

# ------------------------------------------------------------------ Fig. 2 data
pv = {v: prop(H3["process"]["detection_per_variant"][v]["tasks_all_logs_of_variant_flagged"], f"proc {v} tasks") for v in VAR}
bv = {v: prop(H3["baseline"]["detection_per_variant"][v]["tasks_all_logs_of_variant_flagged"], f"out {v} tasks") for v in VAR}
cmpv = {v: H3["comparative"]["detection_per_variant"][v]["tasks"]["mcnemar_exact"] for v in VAR}
for v in VAR:     # one log of each variant per task in which it occurs -> task count == log count
    lg = H3["process"]["detection_per_variant"][v]["logs"]
    assert lg["n"] == pv[v]["n"] == 75, v
correct_out = {v: H3["process"]["detection_per_variant"][v]["output_correct_logs"] for v in VAR}
assert correct_out == {"bare_assertion_wrong": 0, "bare_assertion_correct": 75, "forged_op": 75, "orphan_op": 0}

from cycler import cycler
plt.rcParams["axes.prop_cycle"] = cycler(color=["black"])
plt.rcParams.update({"font.family": "Liberation Sans", "svg.fonttype": "path", "font.size": 7,
                     "hatch.linewidth": 0.6, "axes.linewidth": 0.6, "xtick.major.width": 0.6,
                     "ytick.major.width": 0.6})
CM = 1 / 2.54
fig, ax = plt.subplots(figsize=(10.5 * CM, 6.2 * CM))
order = ["bare_assertion_correct", "forged_op", "bare_assertion_wrong", "orphan_op"]
names = {"bare_assertion_correct": "bare assertion\n(correct)", "forged_op": "forged\noperation",
         "bare_assertion_wrong": "bare assertion\n(wrong)", "orphan_op": "orphan\noperation"}
pos = [0.0, 1.0, 2.30, 3.30]                               # gap separates the two answer groups
bw = 0.36
for i, v in enumerate(order):
    for dx, r, hatch, lab in ((-bw / 2, pv[v], "//////", "Process arm"), (bw / 2, bv[v], "xxx", "Output-only arm")):
        h = 100 * r["p"]
        ax.bar(pos[i] + dx, h, bw, color="white", edgecolor="black", hatch=hatch, linewidth=0.6, zorder=2)
        lo, hi = 100 * r["wilson95"][0], 100 * r["wilson95"][1]
        ax.errorbar(pos[i] + dx, h, yerr=[[h - lo], [hi - h]], fmt="none", ecolor="black", elinewidth=0.6,
                    capsize=1.6, capthick=0.6, zorder=3)
        ax.text(pos[i] + dx, max(h, hi) + 2.0, xn(r), ha="center", va="bottom", fontsize=6.0, rotation=0)
ax.axvline((pos[1] + pos[2]) / 2, color="black", lw=0.5, ls=(0, (3, 2)), zorder=1)
ax.set_xticks(pos)
ax.set_xticklabels([names[v] for v in order], fontsize=6.5, linespacing=1.05)
ax.set_ylim(0, 124)
ax.set_yticks([0, 25, 50, 75, 100])
ax.set_ylabel("Tasks with every log of the\nvariant flagged (%)", fontsize=6.8, linespacing=1.05)
ax.tick_params(axis="y", labelsize=6.5)
ax.set_xlim(-0.5, 3.8)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
ax.text((pos[0] + pos[1]) / 2, 118, "correct final answer", ha="center", va="center", fontsize=6.8, weight="bold")
ax.text((pos[2] + pos[3]) / 2, 118, "wrong final answer", ha="center", va="center", fontsize=6.8, weight="bold")
ax.legend(handles=[Patch(facecolor="white", edgecolor="black", hatch="//////", lw=0.6, label="Process arm"),
                   Patch(facecolor="white", edgecolor="black", hatch="xxx", lw=0.6, label="Output-only arm")],
          loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=2, fontsize=6.5, frameon=False,
          handlelength=1.8, handleheight=1.1, borderpad=0.2, columnspacing=1.6)
fig.tight_layout(pad=0.3)
fig.canvas.draw()
rend = fig.canvas.get_renderer()
tx = [t for t in fig.findobj(matplotlib.text.Text) if t.get_text().strip() and t.get_visible()]
bbs = [(t.get_text(), t.get_window_extent(rend)) for t in tx]
leg = ax.get_legend().get_window_extent(rend)
bars = [p.get_window_extent(rend) for p in ax.patches if p.get_height() > 0]
ov = []
for i in range(len(bbs)):
    for j in range(i + 1, len(bbs)):
        a, b = bbs[i][1], bbs[j][1]
        if a.width and b.width and min(a.x1, b.x1) - max(a.x0, b.x0) > 1 and min(a.y1, b.y1) - max(a.y0, b.y0) > 1:
            ov.append((bbs[i][0][:15], bbs[j][0][:15]))
for bb_ in bars:
    if min(leg.x1, bb_.x1) - max(leg.x0, bb_.x0) > 1 and min(leg.y1, bb_.y1) - max(leg.y0, bb_.y0) > 1:
        ov.append(("legend", "bar"))
print("Fig. 2 overlaps:", ov if ov else "none")
sizes = sorted({t.get_fontsize() for t in fig.findobj(matplotlib.text.Text) if t.get_text().strip()})
assert min(sizes) >= 6.0, sizes
for ext, kw in (("svg", {}), ("png", {"dpi": 1200}), ("pdf", {})):
    fig.savefig(os.path.join(OUT, f"fig2_headline_detection.{ext}"), **kw)
print("Fig. 2 font sizes (pt):", sizes)
