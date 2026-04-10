# Agent Inference

Five-bucket cadence model that forecasts feeding episodes by projecting
forward from recency-weighted gap medians, each estimated from a narrow
time-of-day window. This replaces the baseline model's two-bucket
(overnight/daytime) split with five buckets that capture distinct
phases of the daily feeding cycle.

## Mechanism

The model collapses nearby bottle feeds into feeding episodes using the
shared clustering rule, then looks back over the most recent 7 days of
episode-level history.

For each consecutive pair of episodes, it computes the inter-episode gap
and tags it by the hour of the episode that started the gap. Gaps are
sorted into five buckets based on the start hour:

| Bucket | Hours | What it captures |
|--------|-------|------------------|
| Evening | 17:00-19:00 | Gap from late-afternoon feed to the evening feed |
| Pre-sleep | 19:00-22:00 | Gap from evening feed to the first night wake |
| Deep night | 22:00-04:00 | Gaps between overnight feeds |
| Early morning | 04:00-07:00 | Gap from last night feed to first morning feed |
| Daytime | 07:00-17:00 | Gaps between daytime feeds |

Each gap is weighted by recency (48-hour exponential half-life), and the
weighted median is computed per bucket. This gives five characteristic
gap durations instead of two.

The pre-sleep gap receives additional refinement: when the predicted
evening feed falls in the 20:00 hour, the model computes a narrower
weighted median from gaps that also started in the 20:00 hour. The
final pre-sleep gap is a blend of 40% narrow estimate and 60% broad
bucket estimate. This accounts for the pattern that later evening feeds
tend to produce shorter first-sleep stretches.

Starting from the last observed episode, the model steps forward by
applying the bucket-appropriate gap for each predicted feed's start
time. The first gap uses the evening bucket (since the cutoff falls in
the 17:00-19:00 window); each subsequent gap uses whichever bucket
matches the hour of the newly placed feed.

## Volume and Count

Predicted volume is the recency-weighted median of recent episode
volumes, held at 3.5 oz for all feeds. The total feed count (8) matches
the recency-weighted mean of daily episode counts from recent complete
days, providing a consistency check that the bucket-level gaps produce a
plausible 24-hour schedule.
