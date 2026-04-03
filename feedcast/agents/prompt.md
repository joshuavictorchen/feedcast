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

Your workspace contains `methodology.md` -- a persistent document that
describes your forecasting approach. Its content is rendered directly into
the forecast report. Update it when your approach changes materially. Keep
it concise and descriptive: what you do, why, and how it connects to the
data. Long-term strategy notes belong in separate workspace files.
