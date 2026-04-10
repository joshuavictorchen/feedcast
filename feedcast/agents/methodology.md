# Agent Inference

Empirical Cadence Projection. The agent runs a non-parametric forecasting
model that projects forward from recent inter-episode gap patterns. Gaps
are split by day-part (overnight vs. daytime) and weighted toward the
most recent 2–3 days. The first predicted feed uses a conditional
survival estimate based on elapsed time since the last episode; subsequent
feeds step forward at the day-part-appropriate gap median. A count
calibration step adjusts overall spacing if the projected feed count
diverges significantly from recent daily episode counts. The model
and its constants are maintained in a persistent workspace and may be
evolved by agents across runs.
