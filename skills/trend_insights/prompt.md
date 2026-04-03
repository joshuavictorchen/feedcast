# Trend Insights

Export CSV: `{{export_path}}`
Baby age: {{baby_age_days}} days
Cutoff time: {{cutoff_time}}

---

Analyze the last 7-14 days of feeding history from the export CSV and
write a concise summary of recent trends. This summary will appear near
the top of the forecast report for a parent audience.

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
- **Anything interesting**: Patterns, anomalies, or shifts that a parent
  would find useful or noteworthy. Use your judgment.

## How to analyze

Read the export CSV directly. You may also inspect:

- `feedcast/clustering.py` for the episode boundary rule
- `feedcast/research/feed_clustering/` for episode derivation
- `tracker.json` for prior run predictions and retrospective scores
- `report/report.md` for the most recent forecast report

## Output

Write your summary to `{{output_path}}`.

Format: 1-2 paragraphs of prose, with an optional summary table if the
data supports it. Address the trend categories above, but focus on what
is actually interesting — skip categories where nothing notable is
happening rather than padding with "no change observed."

Tone: concise, informative, interesting to a parent. Avoid jargon.
Speak in terms of feeds, hours, and ounces — not model parameters or
statistical measures.

Do not write any other files. This is a read-and-report task.
