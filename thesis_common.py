"""
thesis_common.py — single source of truth for NB04–NB09b
=========================================================

Every notebook from NB04 onward imports this module, so the model parameters, the
train/test firewall, the GLS solver, the outer loop, the routing/assembly code and the
figure style exist in ONE place, so the routing and assembly logic stays identical across
every notebook that imports it.

Put this file in the project root, next to the notebooks (the folder you start Jupyter
from). Nothing here reads count data until a notebook asks it to.

Sections
  1. Paths + model configuration (with literature sources)
  2. Figure style (shared colours, fonts, save helpers)
  3. Metrics (GEH, mean GEH, RMSE, R^2)
  4. Train / held-out firewall (split, CV folds, logged held-out access)
  5. Count dispersion W, constrained GLS, share search, outer loop, CV
  6. Network costs, k-shortest path stores (cached, optional parallel), assembly
  7. Station names
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import lsq_linear
from scipy.stats import pearsonr

# ════════════════════════════════════════════════════════════════════════════
# 1. PATHS + CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════
PROJECT_ROOT   = Path(os.getcwd())
DATA_RAW       = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
PATH_CACHE     = DATA_PROCESSED / "paths"          # k-shortest path stores (NB05/07/09b)
FIGURES        = PROJECT_ROOT / "figures"          # diagnostic figures (per notebook)
FIG_REPORT     = FIGURES / "report"                # thesis figures (pdf + png)
REPORTS        = PROJECT_ROOT / "reports"
for _p in (DATA_PROCESSED, PATH_CACHE, FIGURES, FIG_REPORT, REPORTS):
    _p.mkdir(parents=True, exist_ok=True)

PROJECTED_CRS = "EPSG:28356"    # GDA94 / MGA Zone 56 (metres)
LONLAT_CRS    = "EPSG:4326"
STUDY_AREA_CORDON = DATA_RAW / "study_area_cordon.gpkg"   # written by NB01 A0
NETWORK_GRAPHML   = DATA_PROCESSED / "network_lts_grade.graphml"

# --- Grade-adjusted speed (Parkin & Rotheram 2010) ---------------------------
VFLAT, KUP, KDOWN = 21.6, 1.44, 0.86    # km/h; km/h per % grade (up / down)
VMIN = 6.0                             

# --- Two-class routing ------------------------------------------------------
LOW_STRESS_MAX_LTS = 1    # E_low = {LTS <= 1}
STRESS_PENALTY     = 2.4  # lambda_stress = 1 + 1.40 (Broach et al. 2012, commute,
                          # no bike lane, 20-30k AADT, distance-equivalent +140%)
K_PATHS = 3               # Yen k-shortest, even split (modelling choice; swept in NB09b)
K_STORE = 5               # paths stored per pair, so k = 1..5 are assembled without re-routing

# --- Zone-spreading in Stage 2 (Option 1) -----------------------------------
# Stage 1 already spreads each zone's demand over ALL its graph nodes (m x n AON,
# NB04 Section G). Stage 2 now mirrors that: each zone contributes up to N_REP
# sampled representative nodes and k-shortest paths are routed on every non-degenerate
# cross-product sub-pair, then averaged back to the O-D pair. This is faithful to the
# m x n disaggregation the methodology describes (and makes the flow maps spread over
# the real street grid instead of firing through one centroid node). It does NOT change
# the low/high cost structure, so it is not a fix for pi identifiability.
# The sampled node set is frozen to ZONE_REP_JSON by NB04 and is the ONLY source read
# downstream; nothing re-samples. N_REP = 1 reproduces the old single-representative-node
# behaviour exactly (the sample always includes the centroid-nearest rep node).
N_REP        = 5          # representative nodes sampled per zone (incl. the centroid rep node)
REP_SEED     = 99         # seed for the (reproducible) per-zone node sample
ZONE_REP_JSON = DATA_PROCESSED / "zone_rep_nodes.json"   # {zone_code: [node_id, ...]}, written by NB04

# --- Share model ------------------------------------------------------------
PI_LOW0   = 0.81          # 56/69, Dill & McNeil (2013) renormalised over willing riders
PI_GRID   = 101           # grid on [0, 1], spacing 0.01
PI_TOL    = 0.01
MAX_OUTER = 25

# --- GLS (Bell 1991) --------------------------------------------------------
SIGMA2   = 10.0           # Q = sigma0^2 I  (no literature value; NB09a reports a
                          # training-only cross-validation check of this choice)
W_YEARS  = (2022, 2025)
W_MONTHS = ["March", "October"]
PEAK_HOURS = ["Time_0600", "Time_0700", "Time_0800"]

# --- Validation -------------------------------------------------------------
GEH_TARGET   = 5.0        # GEH < 5 ...
GEH_GUIDE    = 85.0       # ... on >= 85 % of sites (TfNSW modelling guideline)
SPLIT_FRAC, SPLIT_SEED, N_STRATA = 0.70, 42, 4
CV_FOLDS = 5              # training-only cross-validation (robustness decisions)

# Persisted artefacts shared between notebooks
TRAIN_MASK_NPY  = DATA_PROCESSED / "train_mask.npy"
FINAL_JSON      = DATA_PROCESSED / "stage3_final.json"     # written by NB06 at pi*
TEST_ACCESS_LOG = REPORTS / "heldout_access_log.csv"       # every held-out evaluation

RUN_STAMP = datetime.now().strftime("%Y%m%d_%H%M")


def config_table() -> pd.DataFrame:
    """All model parameters with their source — print this at the top of a notebook."""
    rows = [
        ("v_flat (km/h)", VFLAT, "Parkin & Rotheram (2010)"),
        ("kappa_up (km/h per %)", KUP, "Parkin & Rotheram (2010)"),
        ("kappa_down (km/h per %)", KDOWN, "Parkin & Rotheram (2010)"),
        ("v_min (km/h)", VMIN, "Parkin & Rotheram (2010)"),
        ("E_low threshold (LTS <=)", LOW_STRESS_MAX_LTS, "Mekuria et al. (2012); conservative choice"),
        ("lambda_stress", STRESS_PENALTY, "Broach et al. (2012) commute, 20-30k AADT (+140%)"),
        ("k paths", K_PATHS, "modelling choice; swept 1-5 (NB09b)"),
        ("pi_low0", PI_LOW0, "Dill & McNeil (2013): 56/69"),
        ("pi grid / tol / max outer", f"{PI_GRID} / {PI_TOL} / {MAX_OUTER}", "method choice"),
        ("sigma0^2", SIGMA2, "no literature value; train-only CV check in NB09a"),
        ("W window", f"{W_YEARS} {W_MONTHS}", "CoS count surveys, post-COVID rounds"),
        ("GEH target", f"<{GEH_TARGET} on >={GEH_GUIDE}%", "TfNSW Transport Modelling Guidelines"),
        ("split", f"{SPLIT_FRAC:.0%} train, seed {SPLIT_SEED}, {N_STRATA} v_obs strata", "method choice"),
        ("CV folds (train only)", CV_FOLDS, "method choice"),
    ]
    return pd.DataFrame(rows, columns=["parameter", "value", "source"])


# ════════════════════════════════════════════════════════════════════════════
# 2. FIGURE STYLE
# ════════════════════════════════════════════════════════════════════════════
COL = {
    "low":      "#1a9850",   # low-stress class (green)
    "high":     "#d7301f",   # stress-tolerant class (red)
    "train":    "#1f5fa8",   # training stations (blue)
    "test":     "#f28e2b",   # held-out stations (orange)
    "obs":      "#1b2a49",   # observed values (navy)
    "neutral":  "#7f7f7f",   # neutral / seed / reference
    "grid":     "#d9d9d9",   # street underlay
    "elow":     "#a6dba0",   # faint LTS 1 underlay
    "cordon":   "#333333",
}
STAGE_COL = {"seed": "#bdbdbd", "stage1": "#969696", "stage2": "#525252", "stage3": "#000000"}
FLOW_CMAP  = "viridis_r"     # the ONE sequential colormap (flow)
RESID_CMAP = "RdBu_r"        # the ONE diverging colormap (residual; blue = under-predicted)
LTS_COL = {0: "#424143", 1: "#007E3B", 2: "#F5D000", 3: "#E36820", 4: "#E64626"}


def set_style():
    """Times-like serif fonts (matches \\usepackage{times}) with graceful fallback."""
    import matplotlib as mpl
    from matplotlib.font_manager import findfont, FontProperties
    serif = None
    for f in ["Times New Roman", "Nimbus Roman", "Nimbus Roman No9 L",
              "Liberation Serif", "STIXGeneral"]:
        try:
            p = findfont(FontProperties(family=f), fallback_to_default=False).lower()
            if any(t in p for t in ("times", "nimbusrom", "liberationserif", "stixgeneral")):
                serif = f; break
        except Exception:
            pass
    mpl.rcParams.update({
        "font.family": "serif",
        "font.serif": ([serif] if serif else []) + ["STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",          # Times-like math, not Computer Modern
        "axes.unicode_minus": False,
        "axes.formatter.use_mathtext": True,
        "font.size": 10.5, "axes.titlesize": 11.5, "axes.labelsize": 10.5,
        "legend.fontsize": 9, "xtick.labelsize": 9.5, "ytick.labelsize": 9.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": False, "figure.dpi": 110, "savefig.dpi": 300,
        "legend.frameon": False,
    })
    return serif


def save_report(fig, name):
    """Thesis figure -> figures/report/<name>.pdf + .png (overwrites; no stamps)."""
    import matplotlib.pyplot as plt
    for ext in ("pdf", "png"):
        fig.savefig(FIG_REPORT / f"{name}.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  saved report figure  {name}.pdf/.png")


def save_diag(fig, nb, name):
    """Diagnostic figure -> figures/<nb>_<name>.png."""
    import matplotlib.pyplot as plt
    out = FIGURES / f"{nb}_{name}.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white", dpi=200)
    plt.close(fig)
    print(f"  saved diagnostic     {out.name}")


def metric_box(ax, m, loc="upper left", title=None, color="black"):
    """Small text box: mean GEH, GEH<5 %, R^2 (and n)."""
    lines = ([title] if title else []) + [
        f"mean GEH {m['mean_geh']:.2f}",
        f"GEH$<$5  {m['geh_pass']:.0f}%",
        f"$R^2$ {m['r2']:.2f}   (n={m['n']})",
    ]
    xy = {"upper left": (0.03, 0.97, "left", "top"),
          "lower right": (0.97, 0.03, "right", "bottom"),
          "upper right": (0.97, 0.97, "right", "top")}[loc]
    ax.text(xy[0], xy[1], "\n".join(lines), transform=ax.transAxes, ha=xy[2], va=xy[3],
            fontsize=8.5, color=color, zorder=20,
            bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#bbbbbb", alpha=0.95))




# ════════════════════════════════════════════════════════════════════════════
# 3. METRICS
# ════════════════════════════════════════════════════════════════════════════
def geh(f, o):
    f = np.asarray(f, float); o = np.asarray(o, float)
    den = f + o
    return np.where(den > 0, np.sqrt(2.0 * (f - o) ** 2 / np.where(den > 0, den, 1.0)), 0.0)


def metrics(f, o):
    """mean GEH (identification objective), GEH<5 pass %, RMSE, R^2.

    r2      = squared Pearson correlation (pattern agreement; ignores bias) — the R^2
              the thesis reports. State it as such in the text.
    r2_cod  = 1 - SS_res/SS_tot (coefficient of determination; penalises bias).
    bias_pct = 100 (sum f - sum o) / sum o.
    """
    f = np.asarray(f, float); o = np.asarray(o, float)
    g = geh(f, o)
    ss_res = float(np.sum((o - f) ** 2)); ss_tot = float(np.sum((o - o.mean()) ** 2))
    return dict(
        n=int(len(o)),
        mean_geh=float(np.mean(g)) if len(o) else np.nan,
        median_geh=float(np.median(g)) if len(o) else np.nan,
        geh_pass=float(100 * np.mean(g < GEH_TARGET)) if len(o) else np.nan,
        rmse=float(np.sqrt(np.mean((f - o) ** 2))) if len(o) else np.nan,
        r2=float(pearsonr(o, f)[0] ** 2) if len(o) > 2 and np.std(f) > 0 and np.std(o) > 0 else 0.0,
        r2_cod=float(1 - ss_res / ss_tot) if ss_tot > 0 else np.nan,
        bias_pct=float(100 * (f.sum() - o.sum()) / o.sum()) if o.sum() > 0 else np.nan,
    )


def fmt_m(m):
    return (f"meanGEH {m['mean_geh']:.2f} | GEH<5 {m['geh_pass']:.1f}% | "
            f"RMSE {m['rmse']:.1f} | R2 {m['r2']:.3f} (CoD {m['r2_cod']:.3f}) | "
            f"bias {m['bias_pct']:+.0f}% | n={m['n']}")


# ════════════════════════════════════════════════════════════════════════════
# 4. TRAIN / HELD-OUT FIREWALL
# ════════════════════════════════════════════════════════════════════════════
def make_split(v_obs, frac=SPLIT_FRAC, seed=SPLIT_SEED, n_strata=N_STRATA):
    """70/30 split stratified on v_obs quartiles (identical recipe to the old NB04).

    Uses only the observed counts to *place* stations in strata; no model output is
    involved, so stratifying does not leak test information into the fit.
    """
    v_obs = np.asarray(v_obs, float)
    rng = np.random.default_rng(seed)
    strata = pd.qcut(v_obs, q=n_strata, labels=False, duplicates="drop")
    train = np.zeros(len(v_obs), dtype=bool)
    for s in np.unique(strata):
        idx = np.where(strata == s)[0]
        rng.shuffle(idx)
        train[idx[: int(round(frac * len(idx)))]] = True
    return train


def load_train_mask(L):
    assert TRAIN_MASK_NPY.exists(), "train_mask.npy missing - run NB04 first."
    m = np.load(TRAIN_MASK_NPY).astype(bool)
    assert m.shape == (L,), f"train_mask length {m.shape} != L={L} - re-run NB04."
    return m


def write_final(pi_star, t_star, extra=None):
    """Notebook 06 calls this the moment pi* is fixed, BEFORE touching the held-out stations."""
    rec = dict(pi_star=float(pi_star),
               t_star_sha1=hashlib.sha1(np.round(np.asarray(t_star, float), 9).tobytes()).hexdigest(),
               t_star_sum=float(np.sum(t_star)), frozen_at=datetime.now().isoformat(timespec="seconds"))
    rec.update(extra or {})
    FINAL_JSON.write_text(json.dumps(rec, indent=2))
    return rec


def read_final():
    assert FINAL_JSON.exists(), "stage3_final.json missing - run NB07 first."
    return json.loads(FINAL_JSON.read_text())


def load_test_mask(L, purpose, notebook):
    """The ONLY way to obtain the held-out mask. Requires that pi* has been frozen
    (stage3_final.json written by Notebook 06) and logs who used it and why."""
    assert FINAL_JSON.exists(), ("Held-out stations are locked until NB07 has frozen pi* "
                                 "(stage3_final.json). Run NB07 first.")
    test = ~load_train_mask(L)
    _log_access(notebook, purpose, int(test.sum()))
    return test


def _log_access(notebook, purpose, n):
    row = pd.DataFrame([dict(time=datetime.now().isoformat(timespec="seconds"),
                             notebook=notebook, purpose=purpose, n_heldout=n)])
    row.to_csv(TEST_ACCESS_LOG, mode="a", index=False, header=not TEST_ACCESS_LOG.exists())


def make_cv_folds(train_mask, v_obs, k=CV_FOLDS, seed=SPLIT_SEED):
    """Stratified k folds over the TRAINING stations only. Returns list of boolean
    'validation' masks (length L, all False on held-out stations)."""
    idx = np.where(train_mask)[0]
    v = np.asarray(v_obs, float)[idx]
    strata = pd.qcut(v, q=min(N_STRATA, len(idx) // k), labels=False, duplicates="drop")
    rng = np.random.default_rng(seed)
    order = []
    for s in np.unique(strata):                  # shuffle within strata, then deal round-robin
        j = np.where(strata == s)[0]; rng.shuffle(j); order.extend(j)
    fold_of = np.empty(len(idx), int)
    fold_of[np.asarray(order)] = np.arange(len(order)) % k   # balanced folds, each spans all strata
    folds = []
    for f in range(k):
        m = np.zeros(len(train_mask), bool); m[idx[fold_of == f]] = True
        folds.append(m)
    return folds


# ════════════════════════════════════════════════════════════════════════════
# 5. W, GLS, SHARE SEARCH, OUTER LOOP, CV
# ════════════════════════════════════════════════════════════════════════════
def load_survey(path=None):
    path = path or (DATA_RAW / "cos_bicycle_counts_2016_2026.csv")
    s = pd.read_csv(path)
    s.columns = [c.strip() for c in s.columns]
    s["Month"] = s["Month"].astype(str).str.strip()
    pk = [c for c in PEAK_HOURS if c in s.columns]
    s["peak"] = s[pk].apply(pd.to_numeric, errors="coerce").mean(axis=1)
    return s


def site_to_link(snap, links):
    sl = snap.merge(links[["link_idx", "u", "v", "k"]], on=["u", "v", "k"], how="left")
    return sl.dropna(subset=["link_idx"]).set_index("SiteID")["link_idx"].astype(int)


def build_W(survey, site2link, L, years=W_YEARS, months=W_MONTHS):
    """W_ll = sample variance (ddof=1) of round-mean peak counts over the window.
    Links without >=2 rounds take the median variance. Returns (W, n_with_own_variance)."""
    ww = survey[survey["Year"].between(*years) & survey["Month"].isin(months)].copy()
    ww["link_idx"] = ww["SiteID"].map(site2link)
    ww = ww.dropna(subset=["link_idx"]); ww["link_idx"] = ww["link_idx"].astype(int)
    per = ww.groupby(["link_idx", "Year", "Month"])["peak"].mean().reset_index()
    var = per.groupby("link_idx")["peak"].var(ddof=1)
    W = np.full(L, np.nan)
    for li, vv in var.items():
        W[int(li)] = vv
    good = np.isfinite(W) & (W > 0)
    return np.where(good, W, np.nanmedian(W[good])), int(good.sum())


def solve_gls(A, n, v_obs, W, mask, sigma2=SIGMA2):
    """Bell (1991) constrained GLS, count term on `mask` rows ONLY:
        min (t-n)'Q^-1(t-n) + (v-At)'W^-1(v-At),  Q = sigma2*I,  t >= 0
    (the 1/2 factors in the thesis equation do not change the minimiser)."""
    A = sparse.csr_matrix(A)
    s0 = float(np.sqrt(sigma2)); p = A.shape[1]
    A_m = A[mask]; v_m = np.asarray(v_obs, float)[mask]; w_m = np.asarray(W, float)[mask]
    wis = 1.0 / np.sqrt(w_m)
    C = sparse.vstack([sparse.identity(p, format="csr") / s0, sparse.diags(wis) @ A_m]).tocsr()
    d = np.concatenate([np.asarray(n, float) / s0, wis * v_m])
    res = lsq_linear(C, d, bounds=(0, np.inf), tol=1e-10, max_iter=500)
    if not res.success:
        warnings.warn(f"lsq_linear status {res.status}: {res.message}")
    return res.x


def composite(A_low, A_high, pi):
    return pi * A_low + (1.0 - pi) * A_high


def flow(A_low, A_high, pi, t):
    return np.asarray(composite(A_low, A_high, pi).dot(t)).ravel()


GRID = np.linspace(0.0, 1.0, PI_GRID)


def objective_curves(A_low, A_high, t, v_obs, mask, grid=GRID):
    """Mean GEH / RMSE / GEH-pass over pi_low, on `mask` stations only.
    f(pi) is linear in pi, so the two class flows are computed once."""
    fl = np.asarray(A_low.dot(t)).ravel()[mask]; fh = np.asarray(A_high.dot(t)).ravel()[mask]
    o = np.asarray(v_obs, float)[mask]
    mg, rm, gp = [], [], []
    for p in grid:
        f = p * fl + (1 - p) * fh; g = geh(f, o)
        mg.append(g.mean()); rm.append(np.sqrt(np.mean((f - o) ** 2))); gp.append(100 * np.mean(g < GEH_TARGET))
    mg, rm, gp = map(np.asarray, (mg, rm, gp))
    return dict(grid=grid, mean_geh=mg, rmse=rm, geh_pass=gp,
                pi_meangeh=float(grid[np.argmin(mg)]), pi_rmse=float(grid[np.argmin(rm)]),
                pi_gehpass=float(grid[np.lexsort((rm, -gp))[0]]))   # max pass, RMSE tie-break


def outer_loop(A_low, A_high, t0, n, v_obs, W, mask, sigma2=SIGMA2, pi0=PI_LOW0,
               tol=PI_TOL, max_outer=MAX_OUTER, verbose=False):
    """Stage 3 (thesis §convergence): (i) pi^(j) = argmin train mean GEH at t^(j-1);
    (ii) t^(j) = train-only GLS at A(pi^(j)). Stop when |dpi| < tol, or when the last
    four iterates sit on <= 2 adjacent grid values (grid spacing == tol); the tie is
    broken by the lower train mean GEH. Uses `mask` stations only."""
    pi, t = float(pi0), np.asarray(t0, float).copy()
    trace, cache = [], {}
    reason = None
    for it in range(1, max_outer + 1):
        pl = objective_curves(A_low, A_high, t, v_obs, mask)["pi_meangeh"]
        t_new = solve_gls(composite(A_low, A_high, pl), n, v_obs, W, mask, sigma2)
        cache[round(pl, 4)] = t_new
        m = metrics(flow(A_low, A_high, pl, t_new)[mask], np.asarray(v_obs)[mask])
        dpi = abs(pl - pi)
        trace.append(dict(outer=it, pi_low=pl, dpi=dpi, t_sum=float(t_new.sum()),
                          train_mean_geh=m["mean_geh"], train_geh_pass=m["geh_pass"],
                          train_rmse=m["rmse"], train_r2=m["r2"]))
        if verbose:
            print(f"  outer {it:>2}: pi={pl:.2f}  |dpi|={dpi:.3f}  train meanGEH {m['mean_geh']:.3f}  "
                  f"GEH<5 {m['geh_pass']:.1f}%  sum t {t_new.sum():,.0f}")
        pi, t = pl, t_new
        if round(dpi, 9) < tol:      # rounding removes float noise: on a 0.01 grid
            reason = f"|dpi| < {tol}"; break   # this means "pi did not move"
        if it >= 4 and len({round(r["pi_low"], 4) for r in trace[-4:]}) <= 2:
            reason = "2-value oscillation (grid spacing = tolerance)"; break
    trace = pd.DataFrame(trace)
    if reason and "oscillation" in reason:
        best = None
        for pv in sorted({round(r, 4) for r in trace.tail(4)["pi_low"]}):
            mg = metrics(flow(A_low, A_high, pv, cache[pv])[mask], np.asarray(v_obs)[mask])["mean_geh"]
            if best is None or mg < best[1]:
                best = (pv, mg)
        pi, t = best[0], cache[best[0]]
        reason += f"; tie-break -> pi={pi:.2f} (lower train mean GEH)"
    return dict(pi_star=float(pi), t_star=t, trace=trace,
                reason=reason or f"MAX_OUTER={max_outer} reached", n_iter=len(trace))


def cv_score(A_low, A_high, t0, n, v_obs, W, train_mask, folds, sigma2=SIGMA2):
    """Training-only K-fold CV: refit (pi, t) on train minus fold, score mean GEH on the
    fold. Held-out stations are never touched. Returns (mean, per-fold list, pis)."""
    scores, pis = [], []
    for val in folds:
        fit = train_mask & ~val
        r = outer_loop(A_low, A_high, t0, n, v_obs, W, fit, sigma2)
        f = flow(A_low, A_high, r["pi_star"], r["t_star"])
        scores.append(metrics(f[val], np.asarray(v_obs)[val])["mean_geh"]); pis.append(r["pi_star"])
    return float(np.mean(scores)), scores, pis


# ════════════════════════════════════════════════════════════════════════════
# 6. NETWORK, COSTS, PATH STORES
# ════════════════════════════════════════════════════════════════════════════
def _attr(d, key, default=0.0):
    v = d.get(key, default); v = v[0] if isinstance(v, list) else v
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _name(d):
    n = d.get("name"); n = n[0] if isinstance(n, list) else n
    return str(n) if n is not None and str(n) not in ("", "nan", "None") else None


def load_network():
    """network_lts_grade.graphml -> projected largest strongly-connected component.
    Adds lts (int), len_m, grade (%), time (min, grade-adjusted), time_flat, rise_m."""
    import networkx as nx, osmnx as ox
    G = ox.load_graphml(str(NETWORK_GRAPHML))
    Gp = ox.project_graph(G, to_crs=PROJECTED_CRS)
    Gr = Gp.subgraph(max(nx.strongly_connected_components(Gp), key=len)).copy()
    for u, v, k, d in Gr.edges(keys=True, data=True):
        g = _attr(d, "grade", 0.0)
        try:
            lts = int(float(d["lts"][0] if isinstance(d.get("lts"), list) else d.get("lts", 4)))
        except (TypeError, ValueError):
            lts = 4
        L = _attr(d, "length", 1.0)
        spd = max(VMIN, VFLAT - KUP * max(g, 0.0) + KDOWN * max(-g, 0.0))
        d["lts"] = lts; d["len_m"] = L; d["grade"] = g
        d["time"] = (L / 1000.0) * (60.0 / spd)
        d["time_flat"] = (L / 1000.0) * (60.0 / VFLAT)
        d["rise_m"] = max(g, 0.0) / 100.0 * L
    return Gr


def set_costs(Gr, lam=STRESS_PENALTY, beta=0.0, flat=False):
    """c_high = tau (+ beta term); c_low = c_high x lam on LTS > threshold.
    beta = metres of flat-equivalent distance per metre of climb (uphill only), converted to
    minutes at v_flat: beta * rise_m/1000 km * 60/v_flat. (Broach commute 2-4% -> ~12;
    Hood et al. -> 59.) The thesis equation must be written in this form."""
    for u, v, k, d in Gr.edges(keys=True, data=True):
        base = d["time_flat"] if flat else d["time"]
        c = base + beta * (d["rise_m"] / 1000.0) * (60.0 / VFLAT)
        d["cost_high"] = c
        d["cost_low"] = c * (lam if d["lts"] > LOW_STRESS_MAX_LTS else 1.0)
    return Gr


def store_tag(cls, lam=STRESS_PENALTY, beta=0.0, flat=False, k=K_STORE, spread=True):
    """Cache name for a class path store. The stress-tolerant class does not depend on
    lambda, and lambda = 1 makes the low-stress class identical to it.

    `spread` appends an `_sprdN<N_REP>` marker so the zone-spread stores (routed on the
    expanded cross-product sub-pairs) never collide on disk with a single-node store that
    shares the same cost signature. Pass spread=False only for a genuine single-rep-node
    store. The pair-set signature in route_store() is the real guard; this keeps the
    *filenames* distinct and readable."""
    spd = "flat" if flat else "grade"
    sp = f"_sprd{N_REP}" if spread else ""
    if cls == "high" or abs(lam - 1.0) < 1e-12:
        return f"high_beta{beta:g}_{spd}_k{k}{sp}"
    return f"low_lam{lam:g}_beta{beta:g}_{spd}_k{k}{sp}"


def to_simple(Gr, weight):
    """Collapse the multigraph to a light DiGraph (lowest-`weight` parallel edge)."""
    import networkx as nx
    Gs = nx.DiGraph()
    Gs.add_nodes_from(Gr.nodes)
    for u, v, k, d in Gr.edges(keys=True, data=True):
        w = d[weight]
        if not Gs.has_edge(u, v) or w < Gs[u][v][weight]:
            Gs.add_edge(u, v, **{weight: w, "key": k, "time": d["time"], "len_m": d["len_m"],
                                 "lts": d["lts"]})
    return Gs


# ---- k-shortest paths (Yen via networkx), optional process parallelism ----------
_WORKER_G = None


def _worker_init(G):
    global _WORKER_G
    _WORKER_G = G


def _ksp(G, o, d, k, weight):
    import networkx as nx
    if o == d:
        return []
    out = []
    try:
        for p in nx.shortest_simple_paths(G, o, d, weight=weight):
            out.append(np.asarray(p, dtype=np.int64))
            if len(out) >= k:
                break
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    return out


def _ksp_worker(args):
    o, d, k, weight = args
    return _ksp(_WORKER_G, o, d, k, weight)


def _pairs_sig(pairs):
    a = np.asarray([[-1 if pd.isna(o) else int(o), -1 if pd.isna(d) else int(d)] for o, d in pairs], np.int64)
    return hashlib.sha1(a.tobytes()).hexdigest()[:12]


def _graph_sig(Gs, weight):
    w = np.array([d[weight] for _, _, d in Gs.edges(data=True)], float)
    return f"{Gs.number_of_nodes()}n_{Gs.number_of_edges()}e_{np.round(w.sum(), 4)}"


def route_store(Gs, pairs, k, weight, tag, n_jobs=1, every=200, use_cache=True):
    """k-shortest loopless paths (Yen) for every (o, d); cached on disk by a signature of
    the graph costs + pairs + k. Returns a list (one per pair) of lists of node arrays."""
    meta = dict(tag=tag, k=int(k), graph=_graph_sig(Gs, weight), pairs=_pairs_sig(pairs))
    fp = PATH_CACHE / f"{tag}.pkl"
    if use_cache and fp.exists():
        with open(fp, "rb") as fh:
            cached = pickle.load(fh)
        if cached.get("meta") == meta:
            print(f"  [{tag}] loaded cached path store ({len(cached['store']):,} pairs, k={k})")
            return cached["store"]
        print(f"  [{tag}] cache signature differs -> re-routing")
    jobs = [(int(o), int(d), int(k), weight) if not (pd.isna(o) or pd.isna(d)) else None for o, d in pairs]
    t0 = time.time(); store = [None] * len(jobs)
    live = [i for i, j in enumerate(jobs) if j is not None]
    if n_jobs and n_jobs > 1:
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=n_jobs, initializer=_worker_init, initargs=(Gs,)) as ex:
                for c, (i, res) in enumerate(zip(live, ex.map(_ksp_worker, [jobs[i] for i in live], chunksize=4)), 1):
                    store[i] = res
                    if c % every == 0 or c == len(live):
                        el = time.time() - t0
                        print(f"    [{tag}] {c:,}/{len(live):,}  {el/60:5.1f} min  (~{el/c*(len(live)-c)/60:.0f} min left)")
        except Exception as e:                       # fall back to serial, never silently
            print(f"  [{tag}] parallel routing failed ({type(e).__name__}: {e}) -> serial")
            store = [None] * len(jobs)
    if any(store[i] is None for i in live):
        for c, i in enumerate(live, 1):
            if store[i] is None:
                o, d, kk, w = jobs[i]; store[i] = _ksp(Gs, o, d, kk, w)
            if c % every == 0 or c == len(live):
                el = time.time() - t0
                print(f"    [{tag}] {c:,}/{len(live):,}  {el/60:5.1f} min  (~{el/c*(len(live)-c)/60:.0f} min left)")
    store = [s if s is not None else [] for s in store]
    with open(fp, "wb") as fh:
        pickle.dump(dict(meta=meta, store=store), fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"  [{tag}] routed {len(live):,} pairs in {(time.time()-t0)/60:.1f} min; "
          f"no path: {sum(1 for i in live if not store[i])}; cached -> {fp.name}")
    return store


def load_members(L=None):
    """count_link_members.csv (NB04 corridor matching) -> {(u, v): link_idx}.
    Keyed on (u, v): parallel keys of one (u, v) share a midpoint and bearing in NB04's
    matching, so they always belong to the same station."""
    m = pd.read_csv(DATA_PROCESSED / "count_link_members.csv")
    mon = {}
    for r in m.itertuples():
        key = (int(r.u), int(r.v))
        if key in mon and mon[key] != int(r.link_idx):
            warnings.warn(f"edge {key} maps to two stations ({mon[key]}, {r.link_idx}); keeping first")
            continue
        mon[key] = int(r.link_idx)
    if L is not None:
        assert set(mon.values()) <= set(range(L)), "members reference unknown link_idx"
    return mon


def assemble_A(store, k, mon, L):
    """Incidence on monitored links from a path store: each pair splits evenly over its
    first min(k, available) paths; a path counts ONCE per station (screenline)."""
    rows, cols, vals = [], [], []
    for pid, paths in enumerate(store):
        use = paths[:k]
        if not use:
            continue
        share = 1.0 / len(use)
        for p in use:
            hit = {mon[(int(a), int(b))] for a, b in zip(p[:-1], p[1:]) if (int(a), int(b)) in mon}
            for li in hit:
                rows.append(li); cols.append(pid); vals.append(share)
    return sparse.coo_matrix((vals, (rows, cols)), shape=(L, len(store))).tocsr()


def edge_flows(store, k, weights):
    """Full-network directed edge flows {(u, v): flow} for per-pair demand `weights`."""
    fl = defaultdict(float)
    for pid, paths in enumerate(store):
        use = paths[:k]; w = float(weights[pid])
        if not use or w == 0:
            continue
        s = w / len(use)
        for p in use:
            for a, b in zip(p[:-1], p[1:]):
                fl[(int(a), int(b))] += s
    return fl


# ---- Zone-spreading in Stage 2 (Option 1): expand each O-D pair over sampled -----
#      representative nodes, route the cross-product sub-pairs, assemble back. --------
def load_zone_reps():
    """Load zone_rep_nodes.json -> {zone_code: [node_id, ...]} (NB04 writes it).
    This is the ONLY source of the sampled nodes; NB05/NB07/NB09b never re-sample, so
    their expanded matrices are identical by construction."""
    assert ZONE_REP_JSON.exists(), (
        "zone_rep_nodes.json missing - run NB04 (it samples and saves the per-zone "
        "representative nodes). Zone-spread Stage 2 cannot be built without it.")
    raw = json.loads(ZONE_REP_JSON.read_text())
    return {str(code): [int(n) for n in nodes] for code, nodes in raw.items()}


def sample_zone_reps(code2nodes, rep_node, n_rep=N_REP, seed=REP_SEED):
    """Sample up to `n_rep` representative graph nodes per zone code, uniform and seeded.
    The centroid-nearest `rep_node` is ALWAYS included, so the single-representative-node
    sub-pair survives (illustrations stay well-defined) and n_rep=1 reproduces the old
    Stage-2 behaviour exactly. Nodes within a zone are sorted before sampling and the zone
    order is sorted, so the draw is reproducible given the same graph. NB04 saves the
    return value to ZONE_REP_JSON."""
    rng = np.random.default_rng(seed)
    reps = {}
    for code in sorted(code2nodes):                     # deterministic zone order
        allnodes = sorted(int(n) for n in code2nodes[code])
        if not allnodes:
            reps[str(code)] = []
            continue
        r = rep_node.get(code)
        rep = int(r) if (r is not None and int(r) in set(allnodes)) else allnodes[0]
        if len(allnodes) <= n_rep:
            chosen = set(allnodes)
        else:
            pool = [n for n in allnodes if n != rep]
            draw = rng.choice(pool, size=n_rep - 1, replace=False).tolist()
            chosen = set(draw) | {rep}
        reps[str(code)] = sorted(int(n) for n in chosen)
    return reps


def expand_pairs(cal, reps=None):
    """Zone-spread expansion of the O-D pairs for Stage 2.

    For every O-D pair (processed in pair_idx order) take its origin- and destination-zone
    representative node lists and form every NON-DEGENERATE cross-product sub-pair (a != b),
    deduplicated across all pairs so each unique (o_node, d_node) is routed once. This
    mirrors NB04 Section G's m x n neutral assignment: uniform weight over the surviving
    sub-pairs, renormalised to sum to 1 per pair, so demand is conserved exactly the way
    Stage 1 conserves it (degenerate self-pairs dropped, not left in the denominator).

    Returns
      expanded_pairs : list[(o_node, d_node)]  unique sub-pairs to route, stable order
      assembly       : list of length P (pair_idx order); each entry is a list of
                       (expanded_idx, weight) with the weights summing to 1 (or [] if the
                       pair has no routable sub-pair)
      rep_idx        : int64 array length P; the expanded index of the pair's single
                       representative sub-pair (its saved o_node/d_node), for the R2/S6/D5
                       illustrations, or -1 if unavailable.
    """
    cal = (cal.sort_values("pair_idx") if "pair_idx" in cal.columns else cal).reset_index(drop=True)
    reps = reps if reps is not None else load_zone_reps()
    P = len(cal)
    index, expanded_pairs = {}, []

    def _idx(o, d):
        key = (int(o), int(d))
        j = index.get(key)
        if j is None:
            j = len(expanded_pairs)
            index[key] = j
            expanded_pairs.append(key)
        return j

    assembly = [[] for _ in range(P)]
    rep_idx = np.full(P, -1, dtype=np.int64)
    o_codes = cal["o"].astype(str).to_numpy()
    d_codes = cal["d"].astype(str).to_numpy()
    o_rep = cal["o_node"].to_numpy()
    d_rep = cal["d_node"].to_numpy()
    for p in range(P):
        onodes = reps.get(o_codes[p], [])
        dnodes = reps.get(d_codes[p], [])
        sub = [(a, b) for a in onodes for b in dnodes if a != b]
        if not sub:
            continue
        w = 1.0 / len(sub)                              # uniform over non-degenerate sub-pairs
        for a, b in sub:
            assembly[p].append((_idx(a, b), w))
        if not (pd.isna(o_rep[p]) or pd.isna(d_rep[p])):
            key = (int(o_rep[p]), int(d_rep[p]))
            if key in index:                            # the rep sub-pair is among this pair's subs
                rep_idx[p] = index[key]
    return expanded_pairs, assembly, rep_idx


def rep_assembly(rep_idx, P):
    """Degenerate assembly that selects ONLY each pair's representative sub-pair with
    weight 1 - i.e. the old single-representative-node Stage 2. Feed to assemble_A_spread
    to build the single-node comparison matrix in NB05 D3 from the same expanded store."""
    return [[(int(rep_idx[p]), 1.0)] if rep_idx[p] >= 0 else [] for p in range(P)]


def assemble_A_spread(store, k, mon, L, assembly, P):
    """Monitored-link incidence for zone-spread Stage 2. `store` is indexed by expanded
    sub-pair (expand_pairs order). Each O-D pair's column is the assembly-weighted sum
    over its sub-pairs of that sub-pair's even-split incidence (share 1/len(paths)), a
    path counted ONCE per station - assemble_A's screenline rule applied inside each
    sub-pair, then combined with weights that sum to 1. With N_REP=1 this equals assemble_A
    on the representative-node store."""
    rows, cols, vals = [], [], []
    for pid, subs in enumerate(assembly):
        for eidx, w in subs:
            paths = store[eidx][:k]
            if not paths:
                continue
            share = w / len(paths)
            for p in paths:
                hit = {mon[(int(a), int(b))] for a, b in zip(p[:-1], p[1:]) if (int(a), int(b)) in mon}
                for li in hit:
                    rows.append(li); cols.append(pid); vals.append(share)
    return sparse.coo_matrix((vals, (rows, cols)), shape=(L, P)).tocsr()


def edge_flows_spread(store, k, weights, assembly):
    """Full-network directed edge flows {(u, v): flow} for zone-spread Stage 2. `weights`
    is per-ORIGINAL-pair demand (e.g. pi * t); each pair's demand splits across its
    sub-pairs by the assembly weights, then evenly across each sub-pair's k paths. Mirrors
    edge_flows but maps the expanded sub-pair store back to O-D pairs - this is what the
    R5/S9 flow maps draw, so the flow fans over the street grid instead of single nodes."""
    fl = defaultdict(float)
    for pid, subs in enumerate(assembly):
        wp = float(weights[pid])
        if wp == 0:
            continue
        for eidx, w in subs:
            paths = store[eidx][:k]
            if not paths:
                continue
            s = wp * w / len(paths)
            for p in paths:
                for a, b in zip(p[:-1], p[1:]):
                    fl[(int(a), int(b))] += s
    return fl


def path_stats(Gs_time, p):
    """time (min), length (m), LTS<=threshold length (m) of a node path."""
    T = Lm = Llow = 0.0
    for a, b in zip(p[:-1], p[1:]):
        d = Gs_time[int(a)][int(b)]
        T += d["time"]; Lm += d["len_m"]
        if d["lts"] <= LOW_STRESS_MAX_LTS:
            Llow += d["len_m"]
    return T, Lm, Llow


def a_diff(A_low, A_high):
    return float(abs(A_low - A_high).sum() / max(abs(A_high).sum(), 1e-9))


# ════════════════════════════════════════════════════════════════════════════
# 7. STATION NAMES
# ════════════════════════════════════════════════════════════════════════════
_ABBR = [(" Street", " St"), (" Road", " Rd"), (" Avenue", " Ave"), (" Parade", " Pde"),
         (" Drive", " Dr"), (" Lane", " La"), (" Place", " Pl"), (" Highway", " Hwy"),
         (" Boulevard", " Blvd"), (" Terrace", " Tce"), (" Crescent", " Cres"),
         (" Square", " Sq"), (" Bridge", " Br")]
NAME_COLS = ["intersection", "location", "site_name", "sitename", "site name", "name",
             "description", "site_description", "address", "street", "road", "label"]


def abbreviate(s):
    s = str(s)
    for a, b in _ABBR:
        s = s.replace(a, b)
    return s


def station_names():
    """{SiteID: label}. Written by NB04 (station_names.csv); falls back to 'Site N'."""
    fp = DATA_PROCESSED / "station_names.csv"
    if not fp.exists():
        warnings.warn("station_names.csv missing - run NB04 Section L. Falling back to site ids.")
        return defaultdict(lambda: None)
    d = pd.read_csv(fp)
    return dict(zip(d["SiteID"], d["label"]))


def link_names(links, snap):
    """{link_idx: (label, SiteID)} using station_names.csv."""
    names = station_names()
    site = snap.merge(links[["link_idx", "u", "v", "k"]], on=["u", "v", "k"], how="inner")
    site = site.sort_values("SiteID").drop_duplicates("link_idx")
    out = {}
    for r in site.itertuples():
        lab = names.get(r.SiteID) if hasattr(names, "get") else None
        out[int(r.link_idx)] = (lab if isinstance(lab, str) and lab else f"Site {r.SiteID}", r.SiteID)
    return out
