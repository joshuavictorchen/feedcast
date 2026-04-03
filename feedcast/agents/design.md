# Agent Inference Design

<!-- This is a starting-point placeholder. The agent is expected to evolve
this document as its approach matures. -->

## Freeform Workspace

The agent inference model has no prescribed algorithm. It receives the
export CSV, a forecast horizon, and a persistent workspace. It can read
anything in the repo -- scripted models, research, tracker history,
reports -- and use whatever approach it judges will produce the best
feeding forecast.

The workspace persists across runs. The agent may create helper scripts,
model code, strategy notes, or any other artifacts it finds useful.

## Relationship to Scripted Models

The agent is a model peer to the scripted forecasters: it produces a
`forecast.json` with the same schema and is scored by the same
retrospective evaluation. It is currently excluded from the consensus
blend until retrospective data demonstrates consistent value.

## Single Shared Workspace

Both Claude and Codex write to the same workspace directory. Only one
agent runs per pipeline invocation (selected via CLI arg), so there are
no concurrent write conflicts. Workspace artifacts reflect whichever
agent ran most recently.

## Review Gate

Every pipeline run creates an isolated branch. The agent's forecast and
any workspace changes are committed to that branch. Nothing merges
without human review.
