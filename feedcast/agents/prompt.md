# Feeding Forecast

Export CSV: `{{export_path}}`
Your workspace: `{{workspace_path}}`
Forecast cutoff: {{cutoff_time}}
Forecast horizon: {{horizon_hours}} hours

---

Predict when Silas's next bottle feeds will happen over the next
{{horizon_hours}} hours, starting from the cutoff time above (the latest
recorded feeding activity in the export CSV).

Primary objective: predict bottle-feed timing as well as possible.
Secondary output: include estimated bottle volume for each predicted feed.

Breastfeeding may appear in the export, usually right before a bottle. It is
not a separate prediction target and should not dominate your approach unless
you find it genuinely useful.

## Feeding Episodes

Not every recorded feed is an independent hunger event. Consecutive bottle
feeds that occur close together often form a single feeding episode -- for
example, a large feed followed by a small top-up 50 minutes later. A
deterministic rule defines episode boundaries; see
`feedcast/clustering.py` for the implementation and
`feedcast/research/feed_clustering/` for the derivation and evidence.

Evaluation collapses both your predictions and the actuals into episodes
before scoring. Optimize your forecast for feeding episodes: predict when
the baby will eat, not every possible partial feed. Predicting cluster
internal structure (e.g., both a main feed and a top-up) is allowed but
optional -- it will be collapsed before scoring.

## Workspace

Your workspace may contain artifacts from prior runs, including strategy notes,
model code, or helper scripts. If `strategy.md` exists, read it first:
it documents the current approach, performance data, and guidance from
prior agents. You are free to follow, modify, or discard prior work.

## Freedom

You may use whatever approach you think will produce the best forecast.
You may:

- Read anything in the repo
- Inspect tracker history, reports, scripts, models, and research
- Write and run helper scripts in your workspace
- Create or modify `model.py` or any other workspace artifacts
- Use pure inference if you prefer
- Keep durable notes or strategy files in your workspace

## Boundaries

- Do not modify files outside your workspace.
- Treat the rest of the repo as read-only reference material.
- Your workspace persists across runs. Use it however you want.

## Runtime Budget

You are running under an external hard timeout.

- Hard timeout: {{hard_timeout_minutes}} minutes ({{hard_timeout_seconds}} seconds)
- Conservative target: finish within {{target_runtime_minutes}} minutes ({{target_runtime_seconds}} seconds)
- Started at: {{runtime_start_time}}
- Hard deadline: {{runtime_deadline}}

This prompt does not provide a live timer. If you need to check elapsed
wall-clock time, do it explicitly.

Prefer the fastest path to a valid forecast:

- If the existing workspace model is usable, run it first.
- Write `forecast.json` as soon as you have a defensible forecast.
- Only spend extra time on deeper repo exploration if it is likely to
  materially improve the forecast before the deadline.

## Required Output

Write `forecast.json` to your workspace before you finish.

```json
{
  "feeds": [
    {"time": "2026-03-23T01:30:00", "volume_oz": 3.5},
    {"time": "2026-03-23T04:45:00", "volume_oz": 3.5}
  ]
}
```

Requirements:

- `time` must be ISO 8601
- Feeds must be in chronological order, all after the cutoff time
- Include volume even though timing is the main target

## methodology.md

Your workspace contains `methodology.md`, a persistent document whose
contents are rendered directly into the forecast report for this run.
Update it when your approach changes materially so it describes the
method you actually used for the current forecast.

Write it from first principles for a fresh reader:

- Describe the current method in full. The document must stand on its
  own: a reader who has never seen a prior version must be able to
  understand the mechanism from this file alone.
- Explain what data is used, how gaps or slots or states are computed,
  and how those turn into predicted feed times.
- Mention how volume and overall feed count are handled when they
  matter.

Do not write `methodology.md` as a delta from a previous approach.
Concretely, do not use phrasings like "the baseline", "the two-bucket
baseline", "the baseline algorithm", "builds on", "refines", "as
before", "same as before", "an improvement over X", or "addresses the
documented weakness of Y". Any justification by contrast with an
earlier approach belongs in `CHANGELOG.md` or `strategy.md`, not here.

Use the scripted model `methodology.md` files as the style bar
(e.g. `feedcast/models/slot_drift/methodology.md`): concise,
current-state, and mechanism-first.

- Keep it to a single section. A leading `# Agent Inference` title is
  fine, but do not add `##` or deeper sub-headers.

## CHANGELOG.md

Your workspace contains `CHANGELOG.md`. Whenever this run changes
behavior materially, append a new entry at the top of the file.
Material changes include a new approach, a different bucket or slot
scheme, a reworked projection step, or a tuned constant backed by new
evidence.

Use the same `Problem / Research / Solution` format as the scripted
models (e.g. `feedcast/models/slot_drift/CHANGELOG.md`):

```
## <Short imperative title> | YYYY-MM-DD

### Problem
<What observed behavior or recent-run outcome motivated the change?>

### Research
<Evidence behind the change: observed sub-period medians, recent
retrospective scores, count/timing trade-offs, before/after projection
shape. Include concrete numbers where available.>

### Solution
<What changed, stated concretely. Include a Before/After table for
constants or projection shape when applicable.>
```

Cadence: one entry per material change, newest first. Cosmetic
rewording of `methodology.md` that describes the same mechanism does
not need an entry; approach changes and constant changes always do.
