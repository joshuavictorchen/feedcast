# Examples

A tracked synthetic demo input so you can try Feedcast without your own Nara Baby export.

`nara_baby_sample.csv` is a synthetic bottle-feed export shaped like a real Nara Baby CSV: 32 feeds spanning 2026-03-20 to 2026-03-23.

From a clean working tree:

```bash
.venv/bin/python scripts/run_forecast.py --export-path examples/nara_baby_sample.csv --no-agents
```

`--no-agents` skips the agent steps so the demo runs without a configured `claude` or `codex` CLI. The pipeline creates a run branch `feedcast/YYYYMMDD-HHMMSS`, commits the forecast artifacts, and leaves it checked out — start at [`report/report.md`](../report/report.md).

Note: the report will be anchored to 2026-03-23 (the CSV's last feed), not today.
