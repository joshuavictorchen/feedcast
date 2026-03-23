# nara-silas Rewrite Plan

## Goal

Simplify this repo into a clean, portfolio-ready tool that predicts Silas's
next 24 hours of bottle feeds from Nara Baby CSV exports. Predictions come
from scripted models, LLM agents, and a consensus blend. A Jinja2-templated
Markdown report is the primary output.

## Phase Checklist

- [x] Phase 1: Data layer (`data.py`)
- [x] Phase 2: Model infrastructure + scripted models (`models/`)
- [x] Phase 3: Backtest harness (`backtest.py`)
- [ ] Phase 4: Report generation (`report.py` + `templates/`)
- [ ] Phase 5: Retrospective tracker (`tracker.py`)
- [ ] Phase 6: LLM agent infrastructure (`agents/`)
- [ ] Phase 7: CLI, README, cleanup

Each phase gets a review and checkpoint commit before proceeding.

---

## Design Decisions

| Decision | Choice |
|----------|--------|
| Scripted models | Recent Cadence, Phase Nowcast Hybrid, Gap-Conditional |
| Ensemble | Consensus Blend over the 3 base models |
| LLM agents | Claude (Opus 4.6 max) + Codex (GPT-5.4 xhigh) via CLI |
| Featured forecast | Consensus blend if available; else best scripted by backtest rank; agents never auto-featured |
| Report format | Single `summary.md` via Jinja2; flat (no per-model subdirs) |
| Report tracking | `report/` tracked in git; old reports archived to `.report-archive/` |
| Exports | Untracked raw drops; reproducibility via run manifests in `tracker.json` |
| Evaluation (dense) | Current-export temporal backtests for scripted models + consensus |
| Evaluation (sparse) | Prior-run retrospective for all models including agents |
| Failure mode | Partial success — agent or model failure doesn't block the report |
| Model registration | Explicit lists, no auto-registration magic |

## Transactional Invariant

The report write and tracker update are a single logical transaction:

1. Render the new report into a temp directory.
2. Validate the staged output (summary.md and spaghetti plot exist).
3. Archive existing `report/` to `.report-archive/<run_id>/`.
4. Move the staged temp directory to `report/`.
5. **Only after step 4 succeeds:** append the run entry to `tracker.json`.

If any step fails, the repo remains in a consistent state with the previous
report and tracker intact.

---

## File Structure

```
nara-silas/
├── analyze.py                  # CLI entrypoint
├── data.py                     # CSV parsing, domain types, export snapshot, dataset fingerprint
├── models/
│   ├── __init__.py             # explicit model list, run_all_models(), consensus blend
│   ├── shared.py               # model-specific fitting helpers
│   ├── recent_cadence.py
│   ├── phase_nowcast.py
│   └── gap_conditional.py
├── agents/
│   ├── __init__.py             # explicit agent list, run_agent_forecast() base logic
│   ├── base_prompt.md          # shared prompt body (data format, output schema)
│   ├── claude_forecast/
│   │   ├── prompt.md           # claude-specific framing + includes base prompt
│   │   └── run.sh              # CLI invocation wrapper
│   └── codex_forecast/
│       ├── prompt.md           # codex-specific framing + includes base prompt
│       └── run.sh              # CLI invocation wrapper
├── backtest.py                 # current-export temporal backtesting
├── tracker.py                  # run manifest I/O + retrospective comparison
├── report.py                   # Jinja2 rendering + matplotlib plots
├── templates/
│   └── summary.md.j2           # report template
├── exports/                    # raw CSV drops (gitignored, untracked)
├── report/                     # latest report only (tracked)
├── .report-archive/            # old reports (gitignored)
├── tracker.json                # run history (tracked)
├── requirements.txt
├── plan.md                     # this file
└── README.md
```

---

## Phase 1: Data Layer (`data.py`)

### Domain Types

```python
@dataclass(frozen=True)
class Activity:
    kind: str           # "bottle" | "breastfeed"
    start: datetime
    end: datetime
    volume_oz: float    # interpreted volume (used by models)
    raw_fields: dict    # preserved raw CSV fields for fingerprinting

@dataclass(frozen=True)
class FeedEvent:
    time: datetime
    volume_oz: float
    bottle_volume_oz: float
    breastfeeding_volume_oz: float

@dataclass(frozen=True)
class ForecastPoint:
    time: datetime
    volume_oz: float
    gap_hours: float

@dataclass
class Forecast:
    name: str           # human-readable title
    slug: str           # machine identifier
    points: list[ForecastPoint]
    methodology: str    # brief description for the report
    diagnostics: dict   # model-specific key-value pairs
    available: bool = True
    error_message: str | None = None

@dataclass(frozen=True)
class ExportSnapshot:
    export_path: Path
    activities: list[Activity]
    latest_activity_time: datetime
    dataset_id: str
    source_hash: str
```

### CSV Parsing

- `find_latest_export(exports_dir)` — relaxed regex
  `export_narababy_silas_(\d{8}).*\.csv$` to handle `(1)` suffixes. Sort by
  embedded date, then file mtime as tiebreaker.

- `load_activities(path)` — uses `csv.DictReader` for column-order
  independence. Returns `list[Activity]` with `raw_fields` preserved per row.
  Filters to bottles and breastfeeds at or after `DATA_FLOOR`.

- `load_export_snapshot(exports_dir, export_path)` — convenience loader that
  selects the effective export, parses activities, computes `dataset_id`,
  computes `source_hash`, and derives `latest_activity_time` once so later
  phases do not duplicate that bookkeeping.

- `build_feed_events(activities, merge_window_minutes)` — same breastfeed
  merge logic as current codebase. Breastfeed volume is added to the next
  bottle if that bottle starts within the merge window after the breastfeed
  ends. All event times are anchored on the bottle start time.

- `parse_bottle_volume_oz(row)` — handles breast milk + formula sub-volumes,
  ML-to-FLOZ conversion, and the `[Bottle Feed] Volume` fallback field.

### Dataset Fingerprint

Uses `raw_fields` from each Activity, NOT interpreted `volume_oz`. This
ensures the same raw export produces the same `dataset_id` even if modeling
assumptions (e.g., breastfeed volume heuristic) change.

Fields included in fingerprint:
- `Type`
- `Start Date/time (Epoch)`
- Raw bottle volume columns + units
- Raw breastfeed durations

Implementation: SHA-256 of sorted canonical JSON of these field tuples.

Separate `source_hash`: SHA-256 of raw file bytes (for exact file identity).

### Generic Helpers

Only truly generic helpers live here (not model-specific math):
- `hour_of_day(timestamp) -> float`
- `daily_feed_counts(events) -> dict[date, int]`

### Phase 1 Notes

- Implemented `ExportSnapshot` even though the earlier draft only implied it.
  Later phases clearly need one object for export path, activities, hashes,
  dataset identity, and latest activity time.
- `load_activities()` reads with `encoding="utf-8-sig"` to tolerate BOM-
  prefixed CSVs without leaking that concern into callers.
- `find_latest_export()` sorts by embedded filename date, then file mtime, then
  filename for deterministic selection across same-day re-exports.

### Constants

```python
DATA_FLOOR = datetime(2026, 3, 15)
BIRTH_DATE = datetime(2026, 2, 27)
HORIZON_HOURS = 24
ML_TO_FLOZ = 0.033814

SNACK_THRESHOLD_OZ = 1.5
MIN_INTERVAL_HOURS = 1.5
MAX_INTERVAL_HOURS = 6.0
MIN_POINT_GAP_MINUTES = 45

DEFAULT_BREASTFEED_OZ_PER_30_MIN = 0.5
DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES = 45
```

---

## Phase 2: Model Infrastructure + Scripted Models

### `models/shared.py`

Model-specific math used by multiple models. Ported from current
`forecasting.py` helpers:

- `ForecastUnavailable` lives here instead of `models/__init__.py` so model
  modules can import it without package-init circularity.
- `exp_weights(timestamps, now, half_life_hours) -> ndarray`
- `day_weights(dates, reference_date, half_life_days) -> ndarray`
- `weighted_linregress(x, y, weights) -> (slope, intercept)`
- `_weighted_multi_linregress(features, targets, weights) -> ndarray`
- `weighted_std(values, weights) -> float`
- `build_volume_profile(events, cutoff, lookback_days, half_life_hours) -> dict`
- `lookup_volume_profile(profile, target_time) -> (mean, std)`
- `normalize_forecast_points(points, cutoff, horizon_hours) -> list[ForecastPoint]`
- `roll_forward_constant_interval(history, cutoff, ...) -> list[ForecastPoint]`
- `effective_timing_volume(history) -> float`
- `estimate_target_interval(events, cutoff) -> float`
- `fit_state_gap_regression(history, cutoff, lookback_days) -> (coefficients, recent, n)`
- `state_gap_feature_vector(events, index) -> ndarray`
- `predict_state_gap_hours(events, coefficients) -> float`
- `rolling_gap_hours(events, index, window=3) -> float`

### `models/__init__.py`

No auto-registration. Explicit model list:

```python
@dataclass(frozen=True)
class ModelSpec:
    name: str
    slug: str
    methodology: str
    merge_window_minutes: int | None
    forecast_fn: ModelFn

MODELS = [
    ModelSpec(..., merge_window_minutes=None, forecast_fn=forecast_recent_cadence),
    ModelSpec(..., merge_window_minutes=45, forecast_fn=forecast_phase_nowcast_hybrid),
    ModelSpec(..., merge_window_minutes=45, forecast_fn=forecast_gap_conditional),
]
```

- `build_event_cache(activities) -> dict[merge_window, list[FeedEvent]]` —
  builds only the event streams the scripted lineup actually needs. This
  prevents bottle-only and breastfeed-aware models from being forced onto the
  same history representation.

- `run_all_models(activities, cutoff, horizon_hours) -> list[Forecast]` —
  iterates `MODELS`, builds the correct history for each model from raw
  activities, catches `ForecastUnavailable`, and returns all results
  (including unavailable ones with `available=False`).

- `run_consensus_blend(base_forecasts, history, cutoff, horizon_hours) -> Forecast`
  — time-proximity clustering over pre-computed scripted base forecasts.
  Requires ≥2 available scripted forecasts. It explicitly excludes any future
  agent forecasts from consensus inputs. Ported from current
  `blend_consensus_points_by_time`.

- `select_featured_forecast(base_forecasts, consensus_forecast, ranked_slugs=None) -> str`
  — returns `consensus_blend` when available; otherwise prefers the best
  scripted forecast by backtest ranking when supplied, then falls back to the
  static tiebreaker `phase_nowcast > gap_conditional > recent_cadence`.

**Featured forecast selection:**

1. `consensus_blend` if available (≥2 base forecasts succeeded).
2. Else: best available scripted model by current-export backtest ranking
   (same ranking logic as current headliner selection).
3. If backtest signal is unavailable: static tiebreaker
   `phase_nowcast > gap_conditional > recent_cadence`.
4. Agents never become the auto-featured forecast.

### `models/recent_cadence.py`

Port of `forecast_recent_cadence` (current lines 672-733). ~60 lines.

Algorithm: filters history to last 3 days of full feeds (≥1.5 oz), computes
inter-feed intervals with exponential weighting (half-life 36h), blends 70%
weighted interval with 30% target interval derived from daily feed counts,
rolls forward at constant interval using a time-of-day volume profile.

Depends on: `exp_weights`, `daily_feed_counts`, `day_weights`,
`build_volume_profile`, `roll_forward_constant_interval`.

### `models/phase_nowcast.py`

Port of `forecast_phase_locked_oscillator` (lines 831-929) as internal
helper `_forecast_phase_locked_oscillator`.

Port of `forecast_phase_nowcast_hybrid` (lines 932-1020) as the exported
function.

Algorithm: PLO models feeding schedule as a noisy oscillator with a slowly
varying period. Processes recent events sequentially, adjusting period based
on volume-driven phase shifts. The nowcast hybrid uses PLO as the full-horizon
backbone and blends the first-feed gap with a local state-gap regression when
both models agree within 30 minutes.

Depends on: `estimate_target_interval`, `build_volume_profile`,
`lookup_volume_profile`, `normalize_forecast_points`,
`fit_state_gap_regression`, `predict_state_gap_hours`.

### `models/gap_conditional.py`

Port of `forecast_gap_conditional` (lines 1199-1264).

Algorithm: fits a weighted multivariate linear regression predicting gap-to-
next-feed from event state features (volume, previous gap, rolling gap,
cyclical hour encoding). During projection, each predicted feed is appended
as a synthetic event and the model re-evaluates.

Depends on: `fit_state_gap_regression`, `predict_state_gap_hours`,
`build_volume_profile`, `lookup_volume_profile`, `normalize_forecast_points`,
`exp_weights`, `_weighted_multi_linregress`.

### Phase 2 Notes

- The original plan understated a real modeling invariant: the scripted models
  do not all share the same event history. `Recent Cadence` is bottle-only,
  while `Phase Nowcast` and `Gap-Conditional` are breastfeed-aware. Phase 2
  therefore introduced `ModelSpec.merge_window_minutes` and `build_event_cache()`
  so later phases can reuse the correct event stream per model.
- The three ported scripted models were validated for exact parity against the
  existing `forecasting.py` implementation on the latest export.
- The new three-model consensus blend was also validated against the legacy
  `blend_consensus_points_by_time()` logic restricted to those same three base
  models.

---

## Phase 3: Backtest Harness (`backtest.py`)

Ported from current `backtest_model` + `summarize_backtests` +
`align_forecast_to_actual`. Same temporal-split logic, cleaner types.

### Types

```python
@dataclass(frozen=True)
class BacktestCase:
    cutoff: datetime
    observed_horizon_hours: float
    predicted_count: int
    actual_count: int
    first_predicted_time: datetime | None
    first_actual_time: datetime | None
    first_feed_error_minutes: float | None
    timing_mae_minutes: float | None

@dataclass(frozen=True)
class BacktestSummary:
    potential_cutoffs: int
    total_cutoffs: int
    cutoff_coverage_ratio: float
    mean_first_feed_error_minutes: float | None
    recent_first_feed_error_minutes: float | None
    mean_timing_mae_minutes: float | None

@dataclass(frozen=True)
class ModelBacktest:
    name: str
    slug: str
    cases: list[BacktestCase]
    summary: BacktestSummary
```

### Functions

- `run_backtests(activities, analysis_time, horizon_hours) -> list[ModelBacktest]`
  — builds the per-merge-window event cache once, backtests each scripted
  model on its own event representation, then backtests the scripted
  consensus blend separately.

- `backtest_model(events, forecast_fn, analysis_time, horizon_hours) -> list[BacktestCase]`
  — runs model at every historical feed as a potential cutoff, compares
  forecast to future actuals within the horizon.

- `backtest_consensus(event_cache, events, analysis_time, horizon_hours) -> list[BacktestCase]`
  — reuses `run_all_models_from_cache()` so consensus backtesting does not
  rebuild event histories at every cutoff.

- `summarize_backtests(cases, analysis_time, potential_cutoffs) -> BacktestSummary`
  — aggregates cases into report-friendly metrics.

- `align_forecast_to_actual(predicted, actual) -> (timing_mae, unmatched_predicted, unmatched_actual)`
  — order-preserving DP alignment. Unmatched penalty = 180 minutes.

- `availability_adjusted_first_feed_error(summary) -> float`
  — ranking metric carried forward from the legacy code.

- `rank_backtests(backtests) -> list[str]`
  — best-to-worst scripted ranking used by featured-forecast fallback.

**Scope:** scripted models + consensus only. Agents are NOT backtested (each
invocation requires an LLM call). Agent evaluation comes exclusively from the
prior-run retrospective.

The backtest harness should iterate `MODELS` and use `build_event_cache()` so
each `ModelSpec` is evaluated against the same event representation it uses at
forecast time.

### Phase 3 Notes

- The implementation introduced `ModelBacktest` as the practical return shape
  for later report rendering and featured-forecast ranking. This avoids
  parallel arrays of summaries and cases in later phases.
- Phase 3 also promoted `run_all_models_from_cache()` from an optimization
  detail to an explicit runner contract. Later phases should use it whenever
  they already have an event cache, especially for repeated cutoff evaluation.
- The Phase 3 backtest harness was validated for exact parity against the
  current `forecasting.py` behavior for `recent_cadence`, `phase_nowcast`,
  `gap_conditional`, and the three-model consensus blend restricted to those
  same components.

---

## Phase 4: Report Generation

### `report.py`

- `generate_report(snapshot, forecasts, blend, featured_slug, backtest_results, events, cutoff, retrospective, tracker_meta, output_dir) -> Path`

- Renders to a temp directory first (atomic write pattern):
  1. Render `summary.md` from Jinja2 template
  2. Generate spaghetti plot PNG
  3. Validate: assert summary.md and plot exist in temp dir
  4. If `report/` has content, move to `.report-archive/<run_id>/`
  5. Move temp dir contents to `report/`

- Spaghetti plot: all forecast trajectories on shared time axis, featured
  forecast emphasized with larger markers and time labels, recent actuals
  shown as solid dots. Similar to current `plot_spaghetti_hero`.

### `templates/summary.md.j2`

```markdown
# Silas Feeding Forecast
**{{ date_display }}** · {{ age_days }} days old · Cutoff: {{ cutoff_display }}

## Forecast

**{{ featured.name }}**

| Feed | Time | Volume | Gap |
|------|------|--------|-----|
{% for point in featured.points %}
| {{ loop.index }} | **{{ point.time_display }}** | {{ point.volume_display }} | {{ point.gap_display }} |
{% endfor %}

> Projected total: **{{ featured_total_oz }}** across **{{ featured.points|length }} feeds**

![Forecast Trajectories](spaghetti.png)

## Models

{% for forecast in all_forecasts %}
### {{ forecast.name }}{% if not forecast.available %} (unavailable){% endif %}

{% if forecast.available %}
| Feed | Time | Volume | Gap |
|------|------|--------|-----|
{% for point in forecast.points %}
| {{ loop.index }} | **{{ point.time_display }}** | {{ point.volume_display }} | {{ point.gap_display }} |
{% endfor %}

{{ forecast.methodology }}

{% for key, value in forecast.diagnostics.items() %}
- `{{ key }}`: {{ value }}
{% endfor %}
{% else %}
{{ forecast.error_message }}
{% endif %}

{% endfor %}

## Backtest (Current Export)

Temporal backtests using the current export as both history and future truth.
Only scripted models and consensus are backtested.

| Model | Recent 1st-Feed MAE | Coverage | Overall 1st-Feed MAE | Full-24h MAE |
|-------|---------------------|----------|----------------------|--------------|
{% for slug, summary in backtest_results.items() %}
| {{ summary.name }} | {{ summary.recent_first_feed_display }} | {{ summary.coverage_display }} | {{ summary.overall_first_feed_display }} | {{ summary.timing_mae_display }} |
{% endfor %}

## Retrospective

{% if retrospective.same_dataset %}
No new actuals since prior run (same dataset: `{{ retrospective.dataset_id_short }}`).
{% elif retrospective.available %}
Comparing prior run `{{ retrospective.prior_run_id }}` predictions to actuals
(observed horizon: {{ retrospective.observed_horizon_hours }}h).

| Model | 1st-Feed Error | Full-24h MAE | Status |
|-------|----------------|--------------|--------|
{% for slug, result in retrospective.results.items() %}
| {{ result.name }} | {{ result.first_feed_display }} | {{ result.timing_mae_display }} | {{ result.status }} |
{% endfor %}

{% if retrospective.observed_horizon_hours < 24 %}
> Partial horizon: only {{ retrospective.observed_horizon_hours }}h of actuals
> observed. Full-24h MAE not reported.
{% endif %}
{% else %}
No prior run available.
{% endif %}

## Limitations

- **Limited data:** {{ history_days }} days of usable history since {{ data_floor_display }}.
  Model comparison at this scale is noisy.
- **Non-stationarity:** Feeding patterns change rapidly (intervals lengthening,
  volumes increasing). Models fitted to recent data may not generalize.
- **Breastfeeding volumes are estimated:** The {{ bf_heuristic }} heuristic is
  not measured intake.

---
*Export: `{{ source_file }}` · Dataset: `{{ dataset_id_short }}` ·
Commit: `{{ git_commit }}` · Generated: {{ generated_at }}*
```

---

## Phase 5: Retrospective Tracker (`tracker.py`)

### `tracker.json` Schema

```json
{
  "runs": [
    {
      "run_id": "20260322-213829",
      "timestamp": "2026-03-22T21:38:29",
      "git_commit": "ad9f3c6",
      "git_dirty": false,
      "dataset_id": "sha256:...",
      "source_file": "export_narababy_silas_20260322(1).csv",
      "source_hash": "sha256:...",
      "cutoff": "2026-03-22T21:08:00",
      "model_slugs": [
        "recent_cadence", "phase_nowcast", "gap_conditional",
        "consensus_blend", "claude_forecast", "codex_forecast"
      ],
      "prompt_hashes": {
        "claude_forecast": "sha256:...",
        "codex_forecast": "sha256:..."
      },
      "predictions": {
        "consensus_blend": [
          {"time": "2026-03-22T23:30:00", "volume_oz": 3.5},
          ...
        ],
        "recent_cadence": [...],
        ...
      }
    }
  ]
}
```

No deduplication on same dataset. Reruns on the same export while iterating
models are valuable history and must be preserved.

### Functions

- `load_tracker(path) -> dict` — read tracker.json, return empty structure if
  missing.

- `save_run(path, run_entry)` — append to runs array and write. **Only called
  after the staged report has been validated and swapped into `report/`.**

- `build_run_entry(run_id, dataset_id, source_file, source_hash, cutoff, forecasts, prompt_hashes) -> dict`
  — constructs the run entry including `git_commit` and `git_dirty` from
  `git rev-parse HEAD` and `git status --porcelain`.

- `compute_retrospective(tracker_path, current_snapshot) -> Retrospective`
  — finds the most recent prior run. Logic:
  - If prior run's `dataset_id` matches `current_snapshot.dataset_id` → return
    "same dataset"
  - Otherwise: for each model in prior predictions, compare predicted times to
    actual feed times from `current_snapshot.activities` after the prior cutoff
  - Track `observed_horizon_hours = min(24, span of new actuals after prior cutoff)`
  - Report first-feed error when at least one actual exists
  - Report full-24h MAE **only** when `observed_horizon_hours >= 24`
  - Reuse `align_forecast_to_actual` from `backtest.py` for alignment

---

## Phase 6: LLM Agent Infrastructure

### `agents/__init__.py`

Explicit agent list (no auto-registration):

```python
AGENTS = [
    ("Claude Forecast", "claude_forecast", "agents/claude_forecast"),
    ("Codex Forecast", "codex_forecast", "agents/codex_forecast"),
]
```

- `run_agent_forecast(agent_name, slug, agent_dir, events, cutoff, horizon_hours) -> Forecast`
  - Reads `prompt.md` from `agent_dir`
  - Injects formatted feeding data: last ~5 days of events as a readable table,
    baby's age, cutoff time, breastfeeding context
  - Calls `run.sh` from `agent_dir` with the assembled prompt on stdin
  - Parses JSON output: `{"feeds": [{"time": "ISO", "volume_oz": float}, ...]}`
  - Constructs `Forecast` with computed `gap_hours`
  - On any failure (CLI not found, timeout, parse error, bad JSON):
    returns `Forecast(available=False, error_message=...)`

- `run_all_agents(events, cutoff, horizon_hours) -> list[Forecast]` — iterates
  `AGENTS`, calls `run_agent_forecast` for each.

- `prompt_hash(agent_dir) -> str` — SHA-256 of the assembled prompt template
  (for tracker metadata).

### `agents/base_prompt.md`

Shared prompt body:
- Context: baby's name (Silas), age in days, breastfeeding note (~once/day,
  always immediately before a bottle)
- Data: recent feeding history as a table (time, volume, gap since previous)
- Task: predict the next 24 hours of bottle feeds from the cutoff time
- Output: strict JSON matching the schema
- Guidance: reason about patterns (day/night cadence, volume-gap relationship,
  recent trends), then provide predictions

### Per-Agent Folders

**`agents/claude_forecast/prompt.md`** — Claude-specific framing (e.g., system
prompt style, any model-specific instructions) plus inclusion of
`base_prompt.md` content.

**`agents/claude_forecast/run.sh`** — thin wrapper script. Takes prompt on
stdin, calls the `claude` CLI, outputs the response to stdout. The user fills
in exact flags. Example placeholder:

```bash
#!/usr/bin/env bash
# Adjust flags as needed for your claude CLI installation
claude --model claude-opus-4-6-max -p "$(cat -)"
```

**`agents/codex_forecast/`** — same structure, Codex-specific.

```bash
#!/usr/bin/env bash
# Adjust flags as needed for your codex CLI installation
codex -q --model gpt-5.4-xhigh "$(cat -)"
```

The `run.sh` scripts are deliberately thin and user-editable. No CLI syntax
assumptions are hardcoded in Python.

---

## Phase 7: CLI, README, Cleanup

### `analyze.py`

Pipeline flow:

1. Parse args (`--export-path`, `--analysis-time`, `--skip-agents`)
2. Load `ExportSnapshot`
3. Run scripted models (`run_all_models(snapshot.activities, ...)`)
4. Build the breastfeed-aware merged history used by consensus/reporting
5. Run consensus blend (`run_consensus_blend`)
6. Run current-export backtests (scripted + consensus)
7. Determine featured forecast (`select_featured_forecast`)
8. Run agents (`run_all_agents`) unless `--skip-agents`
9. Compute retrospective from tracker
10. Generate report atomically (render → validate → archive old → swap)
11. **Only after successful swap:** save run to tracker
12. Print summary to stdout

### Cleanup Tasks

- `git rm --cached exports/export_narababy_silas_20260322.csv` — untrack the
  already-committed export
- Delete `forecasting.py` and `reporting.py`
- Move existing `reports/` contents to `.report-archive/`
- Remove empty `reports/` directory

### `.gitignore`

```
.venv/
__pycache__/
*.pyc
.mpl-cache/
exports/
.report-archive/
```

Note: `report/` and `tracker.json` are NOT gitignored (tracked in git).

### `requirements.txt`

```
numpy
scipy
matplotlib
jinja2
```

`scikit-learn` is dropped (no GBM model).

### Tooling Note

Black is now installed in the repo's `.venv` and should be run on changed
Python files at each phase checkpoint.

### `README.md`

Clean portfolio-ready rewrite covering:
- Project description and motivation
- Setup instructions
- Usage (`python analyze.py`)
- Architecture overview (file structure, data flow)
- Model descriptions (one paragraph each)
- Agent descriptions
- How to add a new model or agent
- Limitations and future directions

---

## Implementation Notes

### What Gets Ported vs Rewritten

- **Ported faithfully:** model algorithms (recent_cadence, PLO, phase_nowcast,
  gap_conditional), consensus blend clustering, backtest DP alignment, CSV
  parsing, all shared math helpers.

- **Rewritten:** report generation (Jinja2 replaces hard-coded strings), CLI
  entrypoint (adds agents and tracker steps), export discovery (relaxed regex),
  file structure (monolith split into modules).

- **Dropped:** 8 models (trend_hybrid, template_match, daily_shift,
  survival_weibull, gradient_boosted, satiety_decay, phase_locked_oscillator
  as standalone, consensus_blend calling models internally), volume ranges on
  ForecastPoint, volume MAE tracking, per-model report subdirectories.

### Constants Preserved

All model hyperparameters are preserved at their current values. The models
are being moved, not retuned. Tuning happens in future iterations with more
data.

### Breastfeeding Heuristic

Still relevant. Happens ~once/day, always immediately before a bottle.
Current assumption: 0.5 oz per 30 min breastfeeding, merged into the next
bottle if it starts within 45 min. Models that use breastfeeding volume
(`phase_nowcast`, `gap_conditional`) opt in via `merge_window_minutes`.
`recent_cadence` is bottle-only.
