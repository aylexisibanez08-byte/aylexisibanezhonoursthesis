# Where Cyclists Ride and Why — a stress-weighted bicycle demand model for the Sydney CBD

Code for an honours thesis that estimates peak-hour bicycle demand across the City of Sydney by combining
a Level of Traffic Stress (LTS) network, a 2016 Journey-to-Work seed matrix, a two-class
(stress-tolerant / low-stress) route-choice assignment, and GLS calibration against observed bicycle
counts. The headline parameter is the low-stress rider share $\pi_{low}$, estimated from the counts.

The pipeline is ten Jupyter notebooks sharing one module, `thesis_common.py`. They run **top to bottom, in
order**; each writes artefacts into `data/processed/` (and figures/reports) that later notebooks read.

```
NB01 variables ─▶ NB02 LTS ─▶ NB03 OD seed ─▶ NB04 Stage 1 (GLS) ─▶ NB05 Stage 2 (routing)
      │                                                                     │
      └──────────────────────────── thesis_common.py ──────────────────────┘
                                                                            ▼
            NB09a / NB09b robustness ◀─ NB07 figures ◀─ NB06 Stage 3 (π* + held-out)
                                             │
                                             ▼
                                   NB08 policy scenarios
```

## Notebooks

| Notebook | Stage | What it does | Key output |
|---|---|---|---|
| `01_variables.ipynb` | Inputs | Builds every Furth LTS input variable per edge (speed, lanes, facility, parking, ADT, grade) | `edges_with_variables.gpkg` |
| `02_classify_lts.ipynb` | LTS | Furth-inspired v2.2 classification → routing graph | `network_lts_grade.graphml` |
| `03_od_seed.ipynb` | Seed | 2016 ABS JTW bicycle seed matrix at zone level | `jtw_2016_bike_od_snapped.csv` |
| `04_assignment.ipynb` | Stage 1 | Neutral AON assignment + Bell (1991) GLS demand `t`; the 70/30 train/held-out split | `A_neutral.npz`, `od_calibrated_t.csv`, `train_mask.npy` |
| `05_stage2_assignment.ipynb` | Stage 2 | Two-class $k$-shortest routing → class incidence matrices | `A_low.npz`, `A_high.npz`, `lowstress_route_stats.csv` |
| `06_calibration.ipynb` | Stage 3 | Low-stress share search + outer loop; freezes $\pi^*$, then the single held-out evaluation | `stage3_final.json`, `stage3_metrics.json` (Table 4.1) |
| `07_report_figures.ipynb` | Figures | Every thesis figure/number from the saved outputs | `fig_R1`–`fig_R8`, appendix tables |
| `08_policy_testing.ipynb` | Policy | Upgrades planned CoS corridors to LTS 1 and re-routes | `policy_regional_planned_summary.csv`, `tab:scenario_lts` |
| `09a_robustness_cheap.ipynb` | Robustness | Cheap sweeps: prior dispersion $\sigma_0^2$, count window $W$ | Table 4.3 (rows) |
| `09b_robustness_rerouting.ipynb` | Robustness | Re-routing sweeps: $\lambda_{stress}$, $k$, $\beta_{grade}$, flat speed | Table 4.3 (rows) |

Each notebook opens with a summary of its inputs/outputs and **ends with a diagnostic printout of its key
results** (a text report or summary cell).

## The leakage firewall

Decisions are made on training stations only. The 70/30 stratified split is frozen in NB04
(`train_mask.npy`); the held-out stations stay locked (`tc.load_test_mask` refuses to load them until
`stage3_final.json` exists) and are scored once, in NB06 Section G, at the frozen $(\pi^*, t^*)$.
Robustness notebooks report held-out numbers but never choose on them.

## Running it

1. Put the raw inputs in `data/raw/` — see [`docs/RAW_DATASETS.md`](docs/RAW_DATASETS.md) for the full list.
2. Start Jupyter from the project root (so `data/`, `figures/`, `reports/` resolve) with `thesis_common.py`
   on the path (it sits next to the notebooks).
3. Run the notebooks in order, 01 → 09b. Routing (NB05, NB09b, NB08) is the slow step and is **cached** on
   disk (`data/processed/paths/`), so re-runs reuse the stored paths.

### Reproducibility toggles
- **Compass GPS speed layer** (`USE_COMPASS_SPEED`, top of NB01 §A.2): `True` uses the Compass IoT GPS p85
  probe speeds as the top of the speed hierarchy; `False` disables that layer entirely and falls back to
  TfNSW signposted → OSM `maxspeed` → NSW statutory default.
- **Speed bands** (`USE_KMH_BANDS`, NB02): Furth-native mph edges (default) vs km/h-rounded sensitivity.
- **OSM snapshot** (`OSM_SNAPSHOT` / `FORCE_REFRESH`, NB01): pinned vintage for reproducibility.
- Sweep toggles (`RUN_CV`, `RUN_STRESS`, `RUN_K`, …) in NB09a/09b.

## Dependencies

Python 3.11+ with `numpy`, `pandas`, `geopandas`, `shapely`, `osmnx`, `networkx`, `scipy`, `rasterio`,
`matplotlib`. A DEM step (NB01 §A.6) needs `rasterio`; routing uses `networkx`.

## Folder layout

```
.
├── 01_variables.ipynb … 09b_robustness_rerouting.ipynb
├── thesis_common.py          # shared config, metrics, GLS, routing, firewall
├── data/
│   ├── raw/                  # inputs you supply (see docs/RAW_DATASETS.md)
│   └── processed/            # artefacts the notebooks write + path cache
├── figures/                  # diagnostic PNGs (per notebook) + report/ (thesis figs)
├── reports/                  # text reports + validation samples
└── docs/                     # this documentation set
```

## Notes

- Cell outputs are preserved from the last local run; re-run locally to refresh them.
- `docs/CODE_AUDIT.md` records the code→report traceability and the issues found during the cleanup
  (including two reconstructed cells and one syntax fix — see the changelog).
- `docs/REWRITE_CHANGELOG.md` lists exactly what changed in this cleanup pass.
