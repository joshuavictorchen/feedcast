# Changelog

Tracks behavior-level changes to the Latent Hunger model. Add newest entries first.

## Weight recent history more aggressively in growth-rate fitting | 2026-03-25

### Problem

Latest-24h replay on `exports/export_narababy_silas_20260323.csv` showed the
current `RECENCY_HALF_LIFE_HOURS=72` setting was reacting too slowly to recent
feeding pace changes. Baseline replay scored 70.024, while the best candidate
in the tested sweep reached 73.121.

### Solution

Reduce `RECENCY_HALF_LIFE_HOURS` from `72` to `48` so the growth-rate estimate
leans harder on the most recent events. On the latest 24h replay, that kept the
same predicted feed count but improved timing accuracy enough to raise the
headline score by 3.097 points.

## One-line summary of change | YYYY-MM-DD

### Problem

Describe what was wrong, missing, or worth changing.

### Solution

Describe the behavior change and why it fixes the problem.
