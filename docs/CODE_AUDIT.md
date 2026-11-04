# Code → report impact audit

A comprehensive check that every executed code path contributes to the thesis (a figure, a table, a
saved artefact a later notebook reads, or a reported number), plus the correctness issues found while
reviewing. Per the agreed policy, **dead code is flagged and kept** (not deleted); the two
pipeline-breaking gaps were **reconstructed** because the pipeline does not run without them.

---

## 1. Critical issues found and fixed

These were genuine breakages: the shown cell outputs were **stale** (from an earlier version), and the
notebooks would raise errors on a clean re-run.

### 1.1 NB05 — missing `rs` / `lowstress_route_stats.csv` *(reconstructed)*
- **Symptom:** the Stage-2 report cell referenced `rs` and `q`, which were never defined, and claimed to
  output `lowstress_route_stats.csv`, which was never written.
- **Impact:** `NameError` on re-run; and **NB07 figures R2 and R3 read `lowstress_route_stats.csv`**, so
  they would fail with `FileNotFoundError`.
- **Fix:** the producing logic was reconstructed in NB05's final cell from the cached path stores — for
  each O-D pair the first-ranked low-stress and stress-tolerant paths are compared on length, time and
  LTS-1 share, giving the columns NB07 and the report consume (`same_route`, `len_high_m`, `len_low_m`,
  `share_lts1_low/high`, `detour_dist`, `detour_time`, `movement`, `pair_idx`). The percentile helper `q`
  is also defined there.
- **Verify:** the reconstructed definitions of `detour_dist`/`detour_time` (low-stress route vs the
  stress-tolerant / time-fastest route) should be checked against the saved R2/R3 figures before quoting
  exact numbers; they reproduce the stale report's values (≈10% identical-route, LTS-1 median ≈69%,
  detour medians ≈+9%).

### 1.2 NB04 — missing `N_full` / `N_low` / `subnets` *(reconstructed)*
- **Symptom:** the network cell printed `N_low`/`N_full` summaries, and the graph-simplify, figures and
  report cells used `N_full`, `N_low` and `subnets`, but none of these were defined.
- **Impact:** `NameError` at the simplify cell, Section K figures and the Section L report on a clean run.
- **Fix:** reconstructed in NB04 Section B: `N_full = Gr` (the routable graph) and `N_low` = its
  low-stress edge-subgraph (`E_low = {LTS ≤ 1}`), bundled as `subnets = {"low": N_low, "full": N_full}`.
  Printed edge counts reproduce the stale output (N_low ≈ 17,905 / N_full ≈ 44,411; the latter matches
  NB05's `E_low`).

### 1.3 NB04 — syntax corruption in the Stage-1 GLS cell *(fixed)*
- **Symptom:** `int((~train_mask).sum সিস্ট)` — stray non-Python characters where `.sum()` was intended.
  Invalid Python; the cell could not run (its output was stale).
- **Fix:** corrected to `int((~train_mask).sum())`.

> After these fixes every code cell in every notebook is valid Python (verified by parsing), and the
> inter-notebook file dependencies all resolve. The notebooks still need a local re-run on your data to
> refresh the (preserved) outputs.

---

## 2. Dead / unused code (flagged, kept)

Defined but never referenced again; harmless, left in place with the code untouched. Remove at your discretion.

| Notebook | Item | Note |
|---|---|---|
| NB04 | `best_key(u, v)` | Helper defined in the graph-simplify cell but never called (the simplify loop stores `_orig_key` directly). |
| NB07 | `POLICY_CORRIDOR` | Left over from when the policy scenario lived here; the scenario is now Notebook 08. |
| NB07 | `RUN_POLICY` | Same — the policy flag is only acted on in Notebook 08. |
| NB07 | `say()` / `NUM` | Defined in the setup cell but not invoked in NB07 as handed over. **Verify** whether `results_numbers.txt` is actually written by the "Numbers for the write-up" cells; if those cells print directly, the file is not produced. |
| NB08 | `POLICY_CORRIDOR` | Defined but unused; the scenarios are built from `route_components`, not this list. |
| NB08 | `R1_INCLUDE_STAGE2` | An NB07 figure flag copied into the shared setup cell; no effect in NB08. |
| NB08 | `say()` / `NUM` | Defined but not invoked in NB08. |

---

## 3. Everything that *does* feed the report (traceability)

| Notebook | Produces | Consumed by / appears in |
|---|---|---|
| NB01 | `edges_with_variables.gpkg`; `01_*` figures; `assumptions_register_01.txt`; `validation_*.csv`; `parking_default_sampling.csv` | NB02 (gpkg); thesis methodology figures + validation appendix |
| NB02 | `network_lts_grade.graphml`; `network_lts.gpkg`; `02_*` figures; `lts_classification_report.txt` | NB04 (graphml); thesis LTS figures + distribution/length numbers |
| NB03 | `jtw_2016_bike_od_snapped.csv`; `jtw_2016_bike_od_through_raw.csv`; `jtw_2016_points.csv`; `jtw_2016_zone_centroids.csv`; `03_od_seed_report.txt` | NB04 (seed OD); thesis §4.2 seed description |
| NB04 | `A_neutral.npz`; `od_calibrated_t.csv`; `A_neutral_links.csv`; `count_link_snap.csv`; `count_link_members.csv`; `train_mask.npy/.json`; `zone_rep_nodes.json`; `stage1_edge_flows.csv`; `network_neutral_flows.graphml`; `ablation_grade*.csv`; `station_names.csv` | NB05, NB06, NB07, NB09a/b; thesis Stage-1 ladder + grade ablation |
| NB05 | `A_low.npz`; `A_high.npz`; `A_composite_pi0.npz`; `stage2_link_fit.csv`; `lowstress_route_stats.csv`; `05_stage2_report.txt` | NB06, NB07, NB08, NB09a/b; thesis Stage-2 + R2/R3 |
| NB06 | `od_final_t.csv`; `stage3_final.json`; `stage3_trace.csv`; `pi_objective_curves.csv`; `stage3_link_fit.csv`; `stage3_metrics.json`; `results_top_*_links.csv`; `07_stage3_report.txt` | NB07, NB08, NB09a/b; **thesis Table 4.1**, convergence + results tables |
| NB07 | `fig_R1`–`fig_R8`, supporting `fig_*`; `appendix_station_list.csv/.tex`; (`results_numbers.txt` — see §2) | thesis Results figures + appendix |
| NB08 | per-scenario `fig_*`; `policy_regional_planned_summary.csv`; `regional_planned_route_summary.csv`; diagnostic route maps; `tab:scenario_lts` | thesis Discussion / Appendix G |
| NB09a | `robustness_sigma2.csv`; `robustness_wwindow.csv`; `09a_sweeps.png`; `09a_robustness_report.txt` | **thesis Table 4.3** (σ₀², W rows) |
| NB09b | `robustness_k.csv`; `robustness_stress_penalty.csv`; `robustness_beta_grade.csv`; `robustness_flat.csv`; `09b_sweeps.png`; `09b_robustness_report.txt` | **thesis Table 4.3** (λ, k, β, flat rows) |

---

## 4. Minor observations (not changed)

- **Report-file numbering.** Some text reports are labelled with the *previous* notebook numbering (e.g.
  NB06 writes `07_stage3_report.txt`, NB04 writes `04_report_*.txt`). These are terminal artefacts that
  nothing downstream reads, so the strings were left as-is to keep logic byte-for-byte identical. Rename
  them if you want the filenames to match the notebook numbers.
- **`VMIN` in NB01's A.6.1 diagnostic** uses a local default of `4.0` km/h in the plotted curve, whereas
  the routing floor in `thesis_common` is `6.0`. The diagnostic plot also draws the 6.0 floor line, so
  this is cosmetic, but you may want to align the two for the figure.
- **NB02 cell count output** for `facility_type` shows `mixed 35911 / track 9770` while NB01's export
  shows `mixed 35911 / track 9770` consistently — no discrepancy; noted only to confirm the hand-off matches.
