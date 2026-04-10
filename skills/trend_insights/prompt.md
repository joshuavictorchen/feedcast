# Trend Insights

Export CSV: `{{export_path}}`
Baby age: {{baby_age_days}} days
Cutoff time: {{cutoff_time}}

---

Analyze the past 7 days of feeding history as the baseline context, then
zoom in on the newest data near the cutoff and explain what additional
insight it provides. This summary will appear near the top of the
forecast report for a parent audience.

## What to look for

- **Feed spacing**: Are feeds getting closer together or further apart?
  Is the gap between feeds stabilizing or still shifting?
- **Episode clustering**: Are multi-feed episodes (e.g., a main feed
  followed by a top-up) becoming more or less common? Is the baby
  consolidating into cleaner single feeds?
- **Volume trends**: Are bottle volumes increasing, decreasing, or
  holding steady? Any notable shifts in total daily intake?
- **Day/night patterns**: Is a day/night rhythm emerging or
  strengthening? Are overnight gaps lengthening? Is there a consistent
  daytime feeding cadence?
- **Newest-data delta**: What did the newest observations add? What do
  they confirm, weaken, or newly reveal relative to the 7-day baseline?
- **Anything interesting**: Patterns, anomalies, or shifts that a parent
  would find useful or noteworthy. Use your judgment.

## How to analyze

Read the export CSV directly. You may also inspect:

- `feedcast/clustering.py` for the episode boundary rule
- `feedcast/research/feed_clustering/` for episode derivation
- `tracker.json` for prior run predictions and retrospective scores
- `report/report.md` for the most recent forecast report

Use a 7-day baseline window ending at the cutoff. Then identify the
newest-data window:

- Prefer the data added since the prior run when that is easy to infer
  from `tracker.json` or the latest report.
- If that delta window is too sparse to support useful conclusions, use
  the most recent 24 hours instead.
- If even that is too sparse, say so plainly and avoid strong claims.

## Output

Write your summary to `{{output_path}}`.

Format: 1-2 paragraphs of prose, with an optional summary table if the
data supports it. Lead with what the newest data adds, then place it in
the 7-day context. Address the trend categories above, but focus on what
is actually interesting. Skip categories where nothing notable is
happening rather than padding with "no change observed."

Tone: concise, informative, interesting to a parent. Avoid jargon.
Speak in terms of feeds, hours, and ounces, not model parameters or
statistical measures.

Do not write any other files. This is a read-and-report task.
