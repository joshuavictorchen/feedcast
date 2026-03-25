"""Command-line entrypoint for latest-24h replay scoring and tuning.

Usage:
    # Baseline score
    .venv/bin/python scripts/run_replay.py slot_drift

    # Score with overrides
    .venv/bin/python scripts/run_replay.py slot_drift LOOKBACK_DAYS=5

    # Tune from a YAML file (preferred)
    .venv/bin/python scripts/run_replay.py slot_drift sweep.yaml

    # Tune with inline candidates (quick experiments)
    .venv/bin/python scripts/run_replay.py slot_drift LOOKBACK_DAYS=5,7,9

    # JSON output for agents
    .venv/bin/python scripts/run_replay.py slot_drift --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from feedcast.models import CONSENSUS_BLEND_SLUG, MODELS
from feedcast.replay import score_model, tune_model

VALID_SLUGS = [spec.slug for spec in MODELS] + [CONSENSUS_BLEND_SLUG]
TUNABLE_SLUGS = [spec.slug for spec in MODELS]


def main() -> None:
    """Run the replay CLI."""
    parser = argparse.ArgumentParser(
        description="Replay the latest 24 hours to score or tune a model.",
        usage=(
            "%(prog)s MODEL [PARAM=VALUES | FILE.yaml ...] [--json] "
            "[--export-path PATH] [--output-dir DIR]"
        ),
    )
    parser.add_argument(
        "model",
        choices=VALID_SLUGS,
        metavar="MODEL",
        help=f"Model slug: {', '.join(VALID_SLUGS)}",
    )
    parser.add_argument(
        "params",
        nargs="*",
        metavar="PARAM=VALUES | FILE.yaml",
        help=(
            "Params as KEY=VALUE, or a YAML file for tuning sweeps "
            "(preferred). Comma-separated values trigger inline sweeps; "
            "list values in YAML trigger a sweep."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full replay artifact as JSON.",
    )
    parser.add_argument(
        "--export-path",
        type=Path,
        default=None,
        help="Explicit export CSV. Defaults to the latest matching file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".replay-results"),
        help="Where local replay artifacts are written.",
    )

    args = parser.parse_args()

    try:
        parsed = _parse_params(args.params)
        is_tune = any(len(values) > 1 for values in parsed.values())

        if is_tune:
            if args.model not in TUNABLE_SLUGS:
                raise ValueError(
                    f"Only scripted models can be tuned; got {args.model!r}."
                )
            payload = tune_model(
                model_slug=args.model,
                candidates_by_name=parsed,
                export_path=args.export_path,
                output_dir=args.output_dir,
            )
        else:
            # Single value per key → score with overrides
            overrides = (
                {k: v[0] for k, v in parsed.items()} if parsed else None
            )
            payload = score_model(
                model_slug=args.model,
                overrides=overrides,
                export_path=args.export_path,
                output_dir=args.output_dir,
            )
    except ValueError as error:
        parser.exit(status=2, message=f"error: {error}\n")

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    if is_tune:
        _print_tune_summary(payload)
    else:
        _print_score_summary(payload)


# ---------------------------------------------------------------------------
# Param parsing
# ---------------------------------------------------------------------------


def _parse_value(raw: str) -> int | float | str:
    """Parse a single scalar value: int, float, or string."""
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_params(raw_params: list[str]) -> dict[str, list[Any]]:
    """Parse positional args into candidate value lists.

    Accepts a mix of inline KEY=VALUE args and YAML file paths. Values
    from all sources are merged.

    Inline examples:
        LOOKBACK_DAYS=5         → {"LOOKBACK_DAYS": [5]}
        LOOKBACK_DAYS=5,7,9     → {"LOOKBACK_DAYS": [5, 7, 9]}
        WEIGHTS=[1,1,2,2]       → {"WEIGHTS": [[1, 1, 2, 2]]}

    YAML example (sweep.yaml):
        LOOKBACK_DAYS: [5, 7, 9]
        DRIFT_WEIGHT_HALF_LIFE_DAYS: 3.0
    """
    candidates: dict[str, list[Any]] = {}

    for raw in raw_params:
        # YAML file: load and merge
        if raw.endswith((".yaml", ".yml")):
            _merge_yaml(Path(raw), candidates)
            continue

        # Inline KEY=VALUE
        key, separator, raw_value = raw.partition("=")
        if not separator or not key:
            raise ValueError(
                f"Invalid param format: {raw!r}. Expected KEY=VALUE or a .yaml file."
            )

        # If the whole RHS is valid JSON, treat it as one candidate value.
        # This lets arrays like [1,1,2,2] pass through without being split.
        try:
            parsed = json.loads(raw_value)
            candidates.setdefault(key, []).append(parsed)
            continue
        except (json.JSONDecodeError, ValueError):
            pass

        # Otherwise, split on commas and parse each piece as a scalar.
        for piece in raw_value.split(","):
            piece = piece.strip()
            if piece:
                candidates.setdefault(key, []).append(_parse_value(piece))

    return candidates


def _merge_yaml(path: Path, candidates: dict[str, list[Any]]) -> None:
    """Load a YAML param file and merge into the candidates dict.

    Scalar values become a single candidate. List values become multiple
    candidates for that key.
    """
    if not path.exists():
        raise ValueError(f"Param file not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a YAML mapping of param names to values in {path}, "
            f"got {type(data).__name__}."
        )

    for key, value in data.items():
        if isinstance(value, list):
            candidates.setdefault(str(key), []).extend(value)
        else:
            candidates.setdefault(str(key), []).append(value)


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


def _format_params(params: dict[str, Any]) -> str:
    """Format a parameter dict as a compact inline string."""
    return "  ".join(f"{k}={v}" for k, v in sorted(params.items()))


def _print_score_summary(payload: dict[str, object]) -> None:
    """Print a compact human-readable score summary."""
    model = payload["model"]
    window = payload["replay_window"]
    result = payload["result"]
    print(f"Model:    {model['name']} ({model['slug']})")
    print(f"Replay:   {window['cutoff']} → {window['observed_until']}")
    print(f"Status:   {result['status']}")
    if result.get("overrides"):
        print(f"Params:   {_format_params(result['overrides'])}")
    if result["score"] is not None:
        score = result["score"]
        print(f"Headline: {score['headline']}")
        print(f"Count:    {score['count']}")
        print(f"Timing:   {score['timing']}")
        print(
            f"Feeds:    predicted={score['predicted_count']} "
            f"actual={score['actual_count']} "
            f"matched={score['matched_count']}"
        )
    elif result.get("error_message"):
        print(f"Error:    {result['error_message']}")
    print(f"Saved:    {payload['results_path']}")


def _print_tune_summary(payload: dict[str, object]) -> None:
    """Print a compact human-readable tuning summary."""
    model = payload["model"]
    window = payload["replay_window"]
    baseline = payload["baseline"]
    best = payload["best"]
    search = payload["search"]
    print(f"Model:    {model['name']} ({model['slug']})")
    print(f"Replay:   {window['cutoff']} → {window['observed_until']}")
    print(f"Evaluated: {search['evaluated']} candidates")
    print(f"Baseline: {baseline['effective_score']}  {_format_params(baseline['params'])}")
    print(f"Best:     {best['effective_score']}  {_format_params(best['params'])}")
    improvement = best["improvement_vs_baseline"]
    sign = "+" if improvement >= 0 else ""
    print(f"Delta:    {sign}{improvement}")
    print(f"Saved:    {payload['results_path']}")


if __name__ == "__main__":
    main()
