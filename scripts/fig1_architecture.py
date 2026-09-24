"""Fig. 1 -- three-layer architecture and one validation round. Black and white only.
Canvas in cm: 12.2 cm = LNCS text width. All lettering >= 6 pt at print size. SVG text as paths.

Usage (from the repository root):  python3 scripts/fig1_architecture.py [OUTDIR]   (default: figures/)
Needs matplotlib and the Liberation Sans font; with another font the layout checks may report overflow."""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle, FancyArrowPatch

from cycler import cycler
plt.rcParams["axes.prop_cycle"] = cycler(color=["black"])
plt.rcParams.update({"font.family": "Liberation Sans", "mathtext.fontset": "custom",
                     "mathtext.rm": "Liberation Sans", "mathtext.it": "Liberation Sans:italic",
                     "svg.fonttype": "path", "font.size": 6.5})
W, H, CM = 12.2, 7.4, 1 / 2.54
fig = plt.figure(figsize=(W * CM, H * CM))
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.set_aspect("equal"); ax.axis("off")
K, LW = "black", 0.6
FT, FB, FS, FL = 6.8, 6.3, 6.0, 7.5     # box title, box body, small notes, band labels (pt)
DASH, DOT = (0, (3, 2)), (0, (1, 1.2))

def rbox(x, y, w, h, ls="-", lw=LW, r=0.07):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                fc="white", ec=K, lw=lw, ls=ls, zorder=3))

def tbox(x, y, w, h, title, body="", ls="-", lw=LW):
    rbox(x, y, w, h, ls, lw)
    if body:
        ax.text(x + w / 2, y + h - 0.16, title, ha="center", va="top", fontsize=FT, weight="bold",
                linespacing=1.05, zorder=4)
        nt = title.count("\n") + 1
        ax.text(x + w / 2, y + h - 0.20 - 0.27 * nt, body, ha="center", va="top", fontsize=FB,
                linespacing=1.12, zorder=4)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=FB, linespacing=1.12, zorder=4)

def num(x, y, n):
    ax.add_patch(Circle((x, y), 0.16, fc="black", ec=K, lw=0.5, zorder=8))
    ax.text(x, y - 0.01, str(n), ha="center", va="center", fontsize=6.3, color="white", weight="bold", zorder=9)

def arr(p0, p1, ls="-", lw=LW, head=True, z=2):
    z = 2.8 if (abs(p0[1] - p1[1]) < 1e-9 and p0[1] > 2.4 and p0[1] < 4.1) else z
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>" if head else "-", mutation_scale=5.5, lw=lw, ls=ls,
                                 color=K, shrinkA=0, shrinkB=0, zorder=z))

def band(y, h, title):
    ax.add_patch(Rectangle((0.05, y), W - 0.10, h, fc="none", ec=K, lw=0.5, ls=(0, (5, 2.5)), zorder=1))
    ax.text(0.30, y + h / 2, title, rotation=90, ha="center", va="center", fontsize=FL, weight="bold")

# -------------------------------------------------------------------------- bands
YL, HL = 0.05, 1.90
YC, HC = 2.10, 3.20
YA, HA = 5.45, 1.90
band(YA, HA, "Agent layer"); band(YC, HC, "Consensus layer"); band(YL, HL, "Ledger layer")

# -------------------------------------------------------------------------- consensus layer
bw, bh, gap, x0, by = 1.62, 1.62, 0.18, 1.00, 2.42
xs = [x0 + i * (bw + gap) for i in range(6)]
boxes = [("Peer-set\nassignment", "k = 3 peers;\nthe author is\nexcluded"),
         ("Mechanical\npredicate", "CS (C1\u2013C4),\nPC, TU;\nno LLM judge"),
         ("Commit\u2013\nreveal", "salted hashes;\nrevealed after\nall commits"),
         ("Detection", "flag if \u2265 2/3\nof the k peers\nreject; exclude\nflagged logs"),
         ("Aggregation", "majority over\naccepted logs\nonly"),
         ("Reputation\nupdate", "keyed to the\nvalidation\noutcome")]
for i, (t, b) in enumerate(boxes):
    tbox(xs[i], by, bw, bh, t, b)
    num(xs[i] + 0.02, by + bh - 0.02, i + 2)
    if i:
        arr((xs[i - 1] + bw, by + bh / 2), (xs[i], by + bh / 2))

# harness RNG (environment, not protocol) above step 2
rng = (x0, 4.38, 1.50, 0.58)
tbox(*rng, "harness RNG\n(environment)", ls=DOT)
arr((x0 + 0.62, rng[1]), (x0 + 0.62, by + bh))
ax.text(4.95, 4.72, "Outcome: a deterministic function\nof the revealed on-ledger verdicts,\nrecomputable by any party.",
        ha="left", va="center", fontsize=FS, style="italic", linespacing=1.12)
ax.text(3.62, 5.05, "peers\nvalidate", ha="right", va="center", fontsize=FS, style="italic", linespacing=1.05)

# -------------------------------------------------------------------------- agent layer
ay, ah = 5.82, 0.62
gx = xs[0] + bw + gap / 2                              # channel between steps 2 and 3
agents = [(gx, 1.26, "agent $a_1$", "-"), (4.55, 1.26, "agent $a_2$", "-"),
          (6.95, 1.56, "agent $a_j$\n(free-rider)", DASH), (9.35, 1.26, "agent $a_N$", "-")]
for cx, w, t, ls in agents:
    tbox(cx - w / 2, ay, w, ah, t, ls=ls, lw=0.9 if ls != "-" else LW)
for cx in (5.75, 8.15):
    ax.text(cx, ay + ah / 2, "\u2026", ha="center", va="center", fontsize=9)
ax.text(1.00, 6.95, "Each agent commits its final output and action log before review.",
        ha="left", va="center", fontsize=FS, style="italic")
# peers validate: dashed arrow from an agent to the predicate
arr((4.55 - 0.30, ay), (xs[1] + bw * 0.80, by + bh), ls=DASH)

# -------------------------------------------------------------------------- ledger layer
sy, sh, sw = 0.52, 0.92, 1.36
stores = {"peer": (xs[0] - 0.12, "Peer-set\nrecords"),
          "log": (0, "Log\ncommitments"),
          "verd": (xs[2] + (bw - sw) / 2, "Verdict\ncommits and\nreveals"),
          "seal": (xs[3] + (bw - sw) / 2, "Seals"),
          "rep": (xs[5] + (bw - sw) / 2, "Reputation\nrecords")}
stores["log"] = (gx - 0.36, stores["log"][1])  # 1.46 wide
for k_, (x, t) in stores.items():
    tbox(x, sy, sw if k_ != "log" else 1.46, sh, t)
ax.text(W / 2 + 0.25, 0.26, "Tamper-evident substrate: hash chain and verify(); it records and never decides.",
        ha="center", va="center", fontsize=FS, style="italic")

# (1) commit log: agent a1 straight down the channel to the log commitments store
arr((gx, ay), (gx, sy + sh))
num(gx, YC - 0.05 + 0.0, 1)
# line hop: white gap in the (1) line where the 2 -> 3 arrow crosses it
ax.add_patch(Rectangle((gx - 0.05, by + bh / 2 - 0.09), 0.10, 0.18, fc="white", ec="none", zorder=2.5))
# validators read the committed bytes from the store
lx = stores["log"][0] + 1.46
arr((lx - 0.28, sy + sh), (lx - 0.28, by))
ax.text(lx - 0.20, 2.02, "read", ha="left", va="center", fontsize=FS, style="italic")
# writes into the ledger
arr((xs[0] + 0.56, by), (xs[0] + 0.56, sy + sh))
for k_, i in (("verd", 2), ("seal", 3), ("rep", 5)):
    arr((xs[i] + bw / 2, by), (xs[i] + bw / 2, sy + sh))

# -------------------------------------------------------------------------- severed ledger -> agent arrow
rx = W - 0.30
ry0 = sy + sh / 2
rep_right = stores["rep"][0] + sw
arr((rep_right, ry0), (rx, ry0), ls=DASH, head=False, lw=0.7)
arr((rx, ry0), (rx, ay + ah / 2), ls=DASH, head=False, lw=0.7)
arr((rx, ay + ah / 2), (9.35 + 0.63, ay + ah / 2), ls=DASH, lw=0.7)
cx, cy = rx, 4.62
ax.add_patch(Rectangle((cx - 0.19, cy - 0.19), 0.38, 0.38, fc="white", ec="none", zorder=6))
for s in (1, -1):
    ax.plot([cx - 0.16, cx + 0.16], [cy - 0.16 * s, cy + 0.16 * s], color=K, lw=1.4, zorder=7,
            solid_capstyle="butt")
ax.text(rx - 0.28, 4.62, "no ledger value is\nfed back to agents\n(not a beacon)", ha="right", va="center",
        fontsize=FS, linespacing=1.1)


# ------------------------------------------------------------------ layout check (text overflow / overlap)
fig.canvas.draw()
rend = fig.canvas.get_renderer()
inv = ax.transData.inverted()
def bb(t):
    e = t.get_window_extent(rend); (a, b), (c, d) = inv.transform([[e.x0, e.y0], [e.x1, e.y1]]); return a, b, c, d
texts = [t for t in ax.texts if t.get_text().strip() and not t.get_text().strip().isdigit()]
boxes_ = [p for p in ax.patches if isinstance(p, FancyBboxPatch)]
problems = []
for t in texts:
    a, b, c, d = bb(t)
    if a < 0.02 or c > W - 0.02 or b < 0.02 or d > H - 0.02:
        problems.append(("off-canvas", t.get_text()[:30]))
    for p in boxes_:
        px, py, pw, ph = p.get_x(), p.get_y(), p.get_width(), p.get_height()
        cx_, cy_ = (a + c) / 2, (b + d) / 2
        if px < cx_ < px + pw and py < cy_ < py + ph:           # text belongs to this box
            if a < px + 0.03 or c > px + pw - 0.03 or b < py + 0.02 or d > py + ph - 0.02:
                problems.append(("overflow", t.get_text()[:30].replace("\n", " ")))
for i in range(len(texts)):
    for j in range(i + 1, len(texts)):
        a1, b1, c1, d1 = bb(texts[i]); a2, b2, c2, d2 = bb(texts[j])
        if min(c1, c2) - max(a1, a2) > 0.02 and min(d1, d2) - max(b1, b2) > 0.02:
            problems.append(("text-overlap", texts[i].get_text()[:20].replace("\n", " "), texts[j].get_text()[:20].replace("\n", " ")))
sizes = sorted({t.get_fontsize() for t in texts})
print("font sizes (pt):", sizes, "| min >= 6:", min(sizes) >= 6)
print("layout problems:", problems if problems else "none")

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "figures")
os.makedirs(OUT, exist_ok=True)
for ext, kw in (("svg", {}), ("png", {"dpi": 1200}), ("pdf", {})):
    fig.savefig(os.path.join(OUT, f"fig1_architecture.{ext}"), **kw)
print("ok")
