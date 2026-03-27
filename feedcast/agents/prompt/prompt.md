# Feeding Forecast

This repo exists for one purpose: predict when Silas's next bottle feeds will
happen over the next 24 hours.

Primary objective: predict bottle-feed timing as well as possible.
Secondary output: include estimated bottle volume for each predicted feed.

The forecast window starts immediately after the latest recorded feeding
activity in the export CSV named above.

Breastfeeding may appear in the export, usually right before a bottle. It is
not a separate prediction target and should not dominate your approach unless
you find it genuinely useful.

## Feeding Episodes

Not every recorded feed is an independent hunger event. Consecutive bottle
feeds that occur close together often form a single feeding episode — for
example, a large feed followed by a small top-up 50 minutes later. A
deterministic rule defines episode boundaries; see
`feedcast/clustering.py` for the implementation and
`feedcast/research/feed_clustering/` for the derivation and evidence.

Evaluation collapses both your predictions and the actuals into episodes
before scoring. Optimize your forecast for feeding episodes: predict when
the baby will eat, not every possible partial feed. Predicting cluster
internal structure (e.g., both a main feed and a top-up) is allowed but
optional — it will be collapsed before scoring.

## Freedom

You may use whatever approach you think will produce the best forecast.
You may:

- read anything in the repo
- inspect tracker history, reports, scripts, models, and the other agent's workspace
- write and run helper scripts
- use pure inference if you prefer
- keep durable notes or strategy files in your workspace

## Boundaries

- Do not modify files outside your workspace.
- Treat the rest of the repo as read-only reference material.
- Your workspace persists across runs. Use it however you want.

## Required outputs

Before you finish, write these two files in your workspace.

### `forecast.json`

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
- feeds must be in chronological order
- include volume even though timing is the main target

### `methodology.md`

Write the methodology for this run only.

This file is inserted directly into the report, so it should be clear,
concise, and descriptive enough that someone could repeat what you actually
did this run. If you keep long-term strategy notes, store those in separate
files in your workspace.
