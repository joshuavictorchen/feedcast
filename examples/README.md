# Examples

Tracked synthetic demo inputs for Feedcast.

`nara_baby_sample.csv` is a small bottle-feed export shaped like a real Nara Baby CSV, but generated from synthetic data for repo demos.

Run the scripted pipeline on it with:

```bash
.venv/bin/python scripts/run_forecast.py --export-path examples/nara_baby_sample.csv --no-agents
```

The pipeline will create a new run branch, commit report artifacts there, and leave the branch for review.
