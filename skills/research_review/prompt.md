# Research Review

Assess scripted models against the latest findings in the research hub
(`feedcast/research/`) and propose changes where evidence warrants it.

The model tuning skill (`skills/model_tuning/`) tunes based on
retrospective scores and replay evidence. Research review starts from a
different question — are the model's assumptions grounded in current
cross-cutting evidence? — but the answer may include tuning constants
or logic when the research warrants it.

---

## Step 1: Read the research hub

Start with [`feedcast/research/README.md`](feedcast/research/README.md).
For each research article listed there, read the article's `research.md`
to understand current conclusions and evidence. Note findings that have
implications for specific models — especially conclusions that have
changed since the models were last reviewed.

## Step 2: Review each model

Assess each scripted model registered in `feedcast/models/__init__.py`
against the research findings. Skip `consensus_blend` — it is a
selector, not a model with tunable assumptions.

When reviewing multiple models, consider spawning a sub-agent per model
for parallel review. Each sub-agent should receive the relevant research
findings as context alongside the instructions below. Sequential review
is fine when parallelism is unavailable or unnecessary.

### Per-model assessment

**a. Read model documentation:**

- `design.md` — the model's design rationale and core assumptions
- `research.md` — the model's evidence base, canonical evaluation, and
  open questions
- `methodology.md` — report-facing approach description
- `model.py` — implementation and current constants
- `CHANGELOG.md` — recent behavior changes

**b. Compare against research findings:**

- Are the model's core assumptions still supported by current
  cross-cutting research?
- Do any research conclusions contradict the model's design choices?
- Has the model's own `research.md` diverged from the research hub —
  citing outdated findings or missing recent conclusions?
- Are there research findings the model could incorporate to better
  reflect how the baby's feeding patterns are evolving?

The baby is growing fast. Research findings shift as new data arrives.
The goal is not static alignment with a fixed set of conclusions — it
is ensuring the model's approach stays grounded in the best current
understanding of how feeding behavior works and where it is heading.

**c. Decide whether to propose changes:**

If the model is well-aligned with current research, say so briefly.
Not every model needs changes every review. Declining is a valid and
often correct outcome.

If you see evidence that the model's assumptions or constants should
change, proceed to step d.

**d. Modify model files (if appropriate):**

Follow the documented research workflow (`feedcast/research/README.md`):

1. Run the model's analysis script to establish baseline evidence:
   ```bash
   .venv/bin/python -m feedcast.models.<slug>.analysis
   ```
2. Decide: **Keep** (current constants are best), **Change** (update
   `model.py`), or **Unresolved** (ambiguous evidence).
3. If the decision is **Change**, update `model.py` first, then rerun
   analysis to verify the change against post-edit state:
   ```bash
   .venv/bin/python -m feedcast.models.<slug>.analysis
   ```
4. Update documentation from the final model state:
   - Update `research.md` with current evidence and conclusions
   - Add a `CHANGELOG.md` entry: what changed, why, and the research
     evidence that supports the change
   - Update `design.md` if core assumptions have shifted
   - Update `methodology.md` if the report-facing approach description
     has changed

## Step 3: Flag cross-cutting issues

If a model's evidence conflicts with the research hub, or if you
discover findings that affect multiple models or should update a
research article — **stop and flag the issue to the user**. Do not
modify files under `feedcast/research/` directly.

When flagging, include:

- What the discrepancy or finding is
- Which model(s) and research article(s) are involved
- A proposed resolution

The user will decide whether and how to update the research hub.

## Step 4: Summarize

After all models are assessed, report:

- Which models were reviewed
- Which models had changes proposed (and a brief description)
- Which models are well-aligned (no changes needed)
- Any cross-cutting issues flagged for the user

## Write scope

Each model review may modify tracked files inside its assigned model
directory (`feedcast/models/<slug>/`) only. Do not modify files in
`feedcast/research/`, `feedcast/agents/`, or other model directories.
