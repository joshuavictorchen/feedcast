"""Command-line entrypoint for latest-24h replay scoring and tuning."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from feedcast.models import CONSENSUS_BLEND_SLUG, MODELS
from feedcast.replay import score_model, tune_model


def main() -> None:
    """Run the replay CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Replay the latest observed 24 hours for one scripted model or the "
            "consensus blend."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- score subcommand ---
    score_parser = subparsers.add_parser(
        "score",
        help="Score one model against the latest observed 24 hours.",
    )
    _add_common_arguments(score_parser, include_consensus=True)

    # --- tune subcommand ---
    tune_parser = subparsers.add_parser(
        "tune",
        help=(
            "Evaluate candidate parameter values against the latest 24 hours. "
            "Requires at least one --param flag."
        ),
    )
    _add_common_arguments(tune_parser, include_consensus=False)

    args = parser.parse_args()
    try:
        if args.command == "score":
            overrides = _parse_overrides(args.param) if args.param else None
            payload = score_model(
                model_slug=args.model,
                overrides=overrides,
                export_path=args.export_path,
                output_dir=args.output_dir,
            )
        else:
            candidates = _parse_candidates(args.param)
            payload = tune_model(
                model_slug=args.model,
                candidates_by_name=candidates,
                export_path=args.export_path,
                output_dir=args.output_dir,
            )
    except ValueError as error:
        parser.exit(status=2, message=f"error: {error}\n")

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    if args.command == "score":
        _print_score_summary(payload)
    else:
        _print_tune_summary(payload)


# ---------------------------------------------------------------------------
# Argument setup
# ---------------------------------------------------------------------------


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_consensus: bool,
) -> None:
    """Add the shared replay CLI arguments."""
    model_choices = [spec.slug for spec in MODELS]
    if include_consensus:
        model_choices.append(CONSENSUS_BLEND_SLUG)

    parser.add_argument(
        "--model",
        required=True,
        choices=model_choices,
        help="Target model slug.",
    )
    parser.add_argument(
        "--param",
        action="append",
        metavar="KEY=VALUE",
        help=(
            "Parameter override as KEY=VALUE. "
            "For score: one value per key. "
            "For tune: repeat the same key with different values to define "
            "candidates (cross-product is evaluated)."
        ),
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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full replay artifact as JSON.",
    )


# ---------------------------------------------------------------------------
# Param parsing
# ---------------------------------------------------------------------------


def _parse_value(raw: str) -> int | float | str | list | dict:
    """Parse a parameter value from a --param flag.

    Tries int, then float, then JSON (for lists/dicts), then falls back
    to a plain string.
    """
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    return raw


def _split_param(raw: str) -> tuple[str, Any]:
    """Split a KEY=VALUE string and parse the value."""
    key, separator, raw_value = raw.partition("=")
    if not separator or not key:
        raise ValueError(f"Invalid --param format: {raw!r}. Expected KEY=VALUE.")
    return key, _parse_value(raw_value)


def _parse_overrides(raw_params: list[str]) -> dict[str, Any]:
    """Parse --param flags into a single override dict for score."""
    overrides: dict[str, Any] = {}
    for raw in raw_params:
        key, value = _split_param(raw)
        if key in overrides:
            raise ValueError(
                f"Duplicate --param {key!r}. For score, each parameter appears "
                "once. Use tune for multi-value sweeps."
            )
        overrides[key] = value
    return overrides


def _parse_candidates(raw_params: list[str] | None) -> dict[str, list[Any]]:
    """Parse repeated --param flags into candidate lists for tune."""
    if not raw_params:
        raise ValueError(
            "tune requires at least one --param flag. Example:\n"
            "  --param LOOKBACK_DAYS=5 --param LOOKBACK_DAYS=7"
        )
    candidates: dict[str, list[Any]] = {}
    for raw in raw_params:
        key, value = _split_param(raw)
        candidates.setdefault(key, []).append(value)
    return candidates


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
        print(f"Overrides: {_format_params(result['overrides'])}")
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
