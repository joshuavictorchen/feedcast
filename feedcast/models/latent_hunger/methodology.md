# Latent Hunger State

Mechanistic model that treats hunger as a hidden variable rising over
time and partially reset by each feed. A larger feed drives hunger
lower, so the next feed takes longer. The model simulates this process
forward to produce a 24-hour schedule.

The satiety reset is multiplicative: after a feed of V ounces, hunger
drops to threshold × exp(−rate × V). This guarantees partial resets —
no feed fully zeroes hunger — so volume always influences the predicted
gap. The growth rate (how fast hunger rebuilds) is estimated from
recent events using a recency-weighted average, allowing the model to
track the baby's changing metabolic pace.

At forecast time the model computes the current hunger level from the
last observed feed and elapsed time, then simulates forward: hunger
grows until it crosses the threshold, a feed fires at the simulation
median volume, hunger resets, and the cycle repeats.

Uses breastfeed-merged events (45-minute merge window). Currently
affects only 3 of 81 events with negligible volume additions.
Infrastructure is in place for smooth circadian modulation of the
growth rate, but research found no benefit over the multiplicative
model's inherent volume-driven day/night sensitivity — larger
overnight feeds already produce longer predicted gaps.
