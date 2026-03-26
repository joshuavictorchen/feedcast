"""Derive a simple episode-boundary rule from labeled feed boundaries.

Run from the repo root:
    .venv/bin/python -m feedcast.research.feed_clustering.analysis

This analysis reads the boundary labels curated in ``labels.yaml``, validates
them against the current latest export, and ranks a small set of interpretable
rule families. The current search intentionally stays simple:

- gap-only thresholds
- gap + second-feed-volume thresholds
- a piecewise rule with a short-gap default window and a small-second-feed
  extension window

The ranking is conservative by design. False collapses (predicting
``same_episode`` when the label says ``new_episode``) sort ahead of total
mistakes because the user explicitly prefers missing a cluster over collapsing
two genuinely separate feeds.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import yaml

from feedcast.data import (
    ExportSnapshot,
    build_feed_events,
    hour_of_day,
    load_export_snapshot,
)

LABELS_PATH = Path(__file__).parent / "labels.yaml"
OUTPUT_DIR = Path(__file__).parent
ARTIFACTS_DIR = OUTPUT_DIR / "artifacts"

LABEL_SAME_EPISODE = "same_episode"
LABEL_NEW_EPISODE = "new_episode"
LABEL_AMBIGUOUS = "ambiguous"

MIN_GAP_THRESHOLD_MINUTES = 45
MAX_GAP_THRESHOLD_MINUTES = 90

ADOPTED_BASE_GAP_MINUTES = 73
ADOPTED_EXTENSION_GAP_MINUTES = 80
ADOPTED_SECOND_FEED_MAX_OZ = 1.50


@dataclass(frozen=True)
class BoundaryRecord:
    """One labeled boundary between consecutive bottle feeds.

    Attributes:
        boundary_index: One-based ordinal of the boundary in the labeled series.
        feed_a_time: Timestamp of the earlier feed.
        feed_b_time: Timestamp of the later feed.
        gap_minutes: Elapsed minutes between feeds.
        volume_a_oz: Earlier feed volume.
        volume_b_oz: Later feed volume.
        label: ``same_episode``, ``new_episode``, or ``ambiguous``.
        previous_gap_minutes: Minutes from the previous boundary, if any.
        next_gap_minutes: Minutes to the next boundary, if any.
        feed_a_hour_of_day: Decimal hour of the earlier feed.
    """

    boundary_index: int
    feed_a_time: datetime
    feed_b_time: datetime
    gap_minutes: float
    volume_a_oz: float
    volume_b_oz: float
    label: str
    previous_gap_minutes: float | None
    next_gap_minutes: float | None
    feed_a_hour_of_day: float

    @property
    def is_same_episode(self) -> bool:
        """Return whether this boundary continues the prior episode."""
        return self.label == LABEL_SAME_EPISODE

    @property
    def is_ambiguous(self) -> bool:
        """Return whether this boundary should be excluded from fitting."""
        return self.label == LABEL_AMBIGUOUS

    def display_name(self) -> str:
        """Return a compact human-readable identifier for this boundary."""
        return (
            f"{self.boundary_index}->{self.boundary_index + 1} "
            f"{self.feed_a_time:%m/%d %H:%M} {self.volume_a_oz:.2f} -> "
            f"{self.feed_b_time:%m/%d %H:%M} {self.volume_b_oz:.2f} "
            f"({self.gap_minutes:.1f}m)"
        )


@dataclass(frozen=True)
class CandidateRule:
    """One interpretable candidate episode-boundary rule."""

    family: str
    description: str
    complexity_rank: int
    simplicity_penalty: float
    predictor: Callable[[BoundaryRecord], bool]


@dataclass(frozen=True)
class CandidateResult:
    """Evaluation summary for one candidate rule."""

    family: str
    description: str
    fit_boundary_count: int
    predicted_same_episode_count: int
    same_episode_true_positive_count: int
    false_collapse_count: int
    missed_cluster_count: int
    total_error_count: int
    precision: float
    recall: float
    false_collapse_examples: tuple[str, ...]
    missed_cluster_examples: tuple[str, ...]
    complexity_rank: int
    simplicity_penalty: float


def main() -> None:
    """Run the rule search and write committed research artifacts."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    snapshot = load_export_snapshot()
    boundaries = load_labeled_boundaries()
    _validate_labels_against_export(boundaries, snapshot)

    candidate_results = evaluate_candidate_rules(boundaries)
    overall_shortlist = shortlist_results(candidate_results, limit=8)
    family_best = best_result_by_family(candidate_results)
    conservative_gap_only = next(
        result
        for result in candidate_results
        if result.family == "gap_only"
    )
    adopted_rule = evaluate_rule(
        rule=adopted_piecewise_rule(),
        boundaries=[boundary for boundary in boundaries if not boundary.is_ambiguous],
    )

    summary_payload = {
        "export_path": str(snapshot.export_path),
        "dataset_id": snapshot.dataset_id,
        "label_counts": summarize_label_counts(boundaries),
        "fit_boundary_count": sum(
            1 for boundary in boundaries if not boundary.is_ambiguous
        ),
        "adopted_rule": serialize_candidate_result(adopted_rule),
        "best_gap_only_rule": serialize_candidate_result(conservative_gap_only),
        "best_by_family": {
            family: serialize_candidate_result(result)
            for family, result in family_best.items()
        },
        "overall_shortlist": [
            serialize_candidate_result(result) for result in overall_shortlist
        ],
    }
    (ARTIFACTS_DIR / "summary.json").write_text(
        json.dumps(summary_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    write_boundaries_csv(ARTIFACTS_DIR / "labeled_boundaries.csv", boundaries)
    write_candidate_rules_csv(
        ARTIFACTS_DIR / "candidate_rules.csv",
        candidate_results,
    )
    results_text = render_results_text(
        snapshot=snapshot,
        boundaries=boundaries,
        adopted_rule=adopted_rule,
        conservative_gap_only=conservative_gap_only,
        family_best=family_best,
        overall_shortlist=overall_shortlist,
    )
    (ARTIFACTS_DIR / "research_results.txt").write_text(
        results_text,
        encoding="utf-8",
    )
    print(results_text, end="")


def load_labeled_boundaries(path: Path = LABELS_PATH) -> list[BoundaryRecord]:
    """Load labeled boundaries from ``labels.yaml``.

    Args:
        path: YAML file containing labeled consecutive-feed boundaries.

    Returns:
        The ordered labeled boundary series.
    """
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw_boundaries = payload.get("boundaries", [])
    if not isinstance(raw_boundaries, list):
        raise ValueError(f"{path} does not contain a 'boundaries' list.")

    records: list[BoundaryRecord] = []
    previous_gap: float | None = None
    for index, raw_boundary in enumerate(raw_boundaries, start=1):
        if not isinstance(raw_boundary, dict):
            raise ValueError(f"Boundary #{index} is not a mapping.")
        volumes = raw_boundary.get("volumes")
        if not isinstance(volumes, list) or len(volumes) != 2:
            raise ValueError(f"Boundary #{index} must contain two volumes.")

        feed_a_time = datetime.fromisoformat(raw_boundary["feed_a"])
        feed_b_time = datetime.fromisoformat(raw_boundary["feed_b"])
        gap_minutes = float(raw_boundary["gap_minutes"])
        label = str(raw_boundary["label"])
        if label not in {
            LABEL_SAME_EPISODE,
            LABEL_NEW_EPISODE,
            LABEL_AMBIGUOUS,
        }:
            raise ValueError(f"Unsupported label {label!r} for boundary #{index}.")

        records.append(
            BoundaryRecord(
                boundary_index=index,
                feed_a_time=feed_a_time,
                feed_b_time=feed_b_time,
                gap_minutes=gap_minutes,
                volume_a_oz=float(volumes[0]),
                volume_b_oz=float(volumes[1]),
                label=label,
                previous_gap_minutes=previous_gap,
                next_gap_minutes=None,
                feed_a_hour_of_day=hour_of_day(feed_a_time),
            )
        )
        previous_gap = gap_minutes

    for index in range(len(records) - 1):
        current = records[index]
        next_gap = records[index + 1].gap_minutes
        records[index] = BoundaryRecord(
            boundary_index=current.boundary_index,
            feed_a_time=current.feed_a_time,
            feed_b_time=current.feed_b_time,
            gap_minutes=current.gap_minutes,
            volume_a_oz=current.volume_a_oz,
            volume_b_oz=current.volume_b_oz,
            label=current.label,
            previous_gap_minutes=current.previous_gap_minutes,
            next_gap_minutes=next_gap,
            feed_a_hour_of_day=current.feed_a_hour_of_day,
        )

    return records


def summarize_label_counts(boundaries: Iterable[BoundaryRecord]) -> dict[str, int]:
    """Return counts by label."""
    counts = {
        LABEL_SAME_EPISODE: 0,
        LABEL_NEW_EPISODE: 0,
        LABEL_AMBIGUOUS: 0,
    }
    for boundary in boundaries:
        counts[boundary.label] += 1
    return counts


def _validate_labels_against_export(
    boundaries: list[BoundaryRecord],
    snapshot: ExportSnapshot,
) -> None:
    """Fail fast if labels drift away from the current latest export.

    Args:
        boundaries: Labeled consecutive-feed boundaries.
        snapshot: Parsed latest export snapshot used for validation.
    """
    events = build_feed_events(snapshot.activities, merge_window_minutes=None)
    expected_boundary_count = max(len(events) - 1, 0)
    if len(boundaries) != expected_boundary_count:
        raise ValueError(
            "Boundary count mismatch. "
            f"labels.yaml has {len(boundaries)} boundaries but the latest export "
            f"has {expected_boundary_count}."
        )

    for boundary, event_a, event_b in zip(boundaries, events[:-1], events[1:]):
        actual_gap_minutes = (event_b.time - event_a.time).total_seconds() / 60.0
        if boundary.feed_a_time.strftime("%Y-%m-%dT%H:%M") != event_a.time.strftime(
            "%Y-%m-%dT%H:%M"
        ) or boundary.feed_b_time.strftime("%Y-%m-%dT%H:%M") != event_b.time.strftime(
            "%Y-%m-%dT%H:%M"
        ):
            raise ValueError(
                "Boundary timestamps no longer match the latest export at "
                f"{boundary.display_name()}."
            )
        if abs(boundary.volume_a_oz - event_a.volume_oz) > 0.011:
            raise ValueError(
                "Boundary volume_a no longer matches the latest export at "
                f"{boundary.display_name()}."
            )
        if abs(boundary.volume_b_oz - event_b.volume_oz) > 0.011:
            raise ValueError(
                "Boundary volume_b no longer matches the latest export at "
                f"{boundary.display_name()}."
            )
        if abs(boundary.gap_minutes - actual_gap_minutes) > 0.11:
            raise ValueError(
                "Boundary gap_minutes no longer matches the latest export at "
                f"{boundary.display_name()}."
            )


def evaluate_candidate_rules(boundaries: list[BoundaryRecord]) -> list[CandidateResult]:
    """Evaluate the configured rule families against labeled boundaries.

    Args:
        boundaries: Ordered labeled boundaries.

    Returns:
        Candidate results sorted by the project's preferred tradeoff.
    """
    fit_boundaries = [boundary for boundary in boundaries if not boundary.is_ambiguous]
    if not fit_boundaries:
        raise ValueError("No non-ambiguous boundaries available for fitting.")

    candidate_rules: list[CandidateRule] = [baseline_always_new_episode_rule()]
    candidate_rules.extend(generate_gap_only_rules())
    candidate_rules.extend(generate_gap_and_small_second_rules(boundaries))
    candidate_rules.extend(generate_piecewise_extension_rules(boundaries))

    results = [evaluate_rule(rule, fit_boundaries) for rule in candidate_rules]
    return sorted(
        results,
        key=lambda result: (
            result.false_collapse_count,
            result.total_error_count,
            result.complexity_rank,
            result.simplicity_penalty,
            -result.same_episode_true_positive_count,
            result.description,
        ),
    )


def baseline_always_new_episode_rule() -> CandidateRule:
    """Return the conservative always-new-episode baseline."""
    return CandidateRule(
        family="always_new_episode",
        description="Always treat the next feed as a new episode.",
        complexity_rank=0,
        simplicity_penalty=0.0,
        predictor=lambda boundary: False,
    )


def generate_gap_only_rules() -> list[CandidateRule]:
    """Return the gap-only threshold family."""
    rules: list[CandidateRule] = []
    for gap_minutes in range(MIN_GAP_THRESHOLD_MINUTES, MAX_GAP_THRESHOLD_MINUTES + 1):
        rules.append(
            CandidateRule(
                family="gap_only",
                description=f"same_episode if gap <= {gap_minutes} minutes",
                complexity_rank=1,
                simplicity_penalty=float(gap_minutes),
                predictor=lambda boundary, gap_minutes=gap_minutes: (
                    boundary.gap_minutes <= gap_minutes
                ),
            )
        )
    return rules


def generate_gap_and_small_second_rules(
    boundaries: list[BoundaryRecord],
) -> list[CandidateRule]:
    """Return the conjunction family using gap and second-feed volume."""
    rules: list[CandidateRule] = []
    second_volume_thresholds = sorted({boundary.volume_b_oz for boundary in boundaries})
    for gap_minutes in range(MIN_GAP_THRESHOLD_MINUTES, MAX_GAP_THRESHOLD_MINUTES + 1):
        for max_second_volume in second_volume_thresholds:
            rules.append(
                CandidateRule(
                    family="gap_and_small_second",
                    description=(
                        "same_episode if gap <= "
                        f"{gap_minutes} minutes and second feed <= "
                        f"{max_second_volume:.2f} oz"
                    ),
                    complexity_rank=2,
                    simplicity_penalty=(
                        float(gap_minutes)
                        + threshold_simplicity_penalty(max_second_volume)
                    ),
                    predictor=lambda boundary, gap_minutes=gap_minutes, max_second_volume=max_second_volume: (
                        boundary.gap_minutes <= gap_minutes
                        and boundary.volume_b_oz <= max_second_volume
                    ),
                )
            )
    return rules


def generate_piecewise_extension_rules(
    boundaries: list[BoundaryRecord],
) -> list[CandidateRule]:
    """Return the short-gap default + small-second-feed extension family."""
    rules: list[CandidateRule] = []
    second_volume_thresholds = sorted({boundary.volume_b_oz for boundary in boundaries})
    for short_gap_minutes in range(
        MIN_GAP_THRESHOLD_MINUTES,
        MAX_GAP_THRESHOLD_MINUTES + 1,
    ):
        for extended_gap_minutes in range(short_gap_minutes, MAX_GAP_THRESHOLD_MINUTES + 1):
            for max_second_volume in second_volume_thresholds:
                rules.append(
                    CandidateRule(
                        family="piecewise_small_second_extension",
                        description=(
                            "same_episode if gap <= "
                            f"{short_gap_minutes} minutes, or if gap <= "
                            f"{extended_gap_minutes} minutes and second feed <= "
                            f"{max_second_volume:.2f} oz"
                        ),
                        complexity_rank=3,
                        simplicity_penalty=(
                            float(short_gap_minutes + extended_gap_minutes)
                            + threshold_simplicity_penalty(max_second_volume)
                        ),
                        predictor=lambda boundary, short_gap_minutes=short_gap_minutes, extended_gap_minutes=extended_gap_minutes, max_second_volume=max_second_volume: (
                            boundary.gap_minutes <= short_gap_minutes
                            or (
                                boundary.gap_minutes <= extended_gap_minutes
                                and boundary.volume_b_oz <= max_second_volume
                            )
                        ),
                    )
                )
    return rules


def adopted_piecewise_rule() -> CandidateRule:
    """Return the adopted Phase 1 rule, distinct from best-fit search output."""
    return CandidateRule(
        family="adopted_rule",
        description=(
            "same_episode if gap <= "
            f"{ADOPTED_BASE_GAP_MINUTES} minutes, or if gap <= "
            f"{ADOPTED_EXTENSION_GAP_MINUTES} minutes and second feed <= "
            f"{ADOPTED_SECOND_FEED_MAX_OZ:.2f} oz"
        ),
        complexity_rank=3,
        simplicity_penalty=(
            float(ADOPTED_BASE_GAP_MINUTES + ADOPTED_EXTENSION_GAP_MINUTES)
            + threshold_simplicity_penalty(ADOPTED_SECOND_FEED_MAX_OZ)
        ),
        predictor=lambda boundary: (
            boundary.gap_minutes <= ADOPTED_BASE_GAP_MINUTES
            or (
                boundary.gap_minutes <= ADOPTED_EXTENSION_GAP_MINUTES
                and boundary.volume_b_oz <= ADOPTED_SECOND_FEED_MAX_OZ
            )
        ),
    )


def evaluate_rule(
    rule: CandidateRule,
    boundaries: list[BoundaryRecord],
) -> CandidateResult:
    """Evaluate one candidate rule.

    Args:
        rule: Candidate rule to score.
        boundaries: Non-ambiguous labeled boundaries.

    Returns:
        Summary counts and a few concrete error examples.
    """
    false_collapse_examples: list[str] = []
    missed_cluster_examples: list[str] = []
    true_positive_count = 0
    false_collapse_count = 0
    missed_cluster_count = 0
    predicted_same_episode_count = 0

    for boundary in boundaries:
        predicted_same_episode = rule.predictor(boundary)
        if predicted_same_episode:
            predicted_same_episode_count += 1

        if predicted_same_episode and boundary.is_same_episode:
            true_positive_count += 1
            continue

        if predicted_same_episode and not boundary.is_same_episode:
            false_collapse_count += 1
            false_collapse_examples.append(boundary.display_name())
            continue

        if (not predicted_same_episode) and boundary.is_same_episode:
            missed_cluster_count += 1
            missed_cluster_examples.append(boundary.display_name())

    precision = (
        true_positive_count / predicted_same_episode_count
        if predicted_same_episode_count > 0
        else 1.0
    )
    actual_same_episode_count = sum(boundary.is_same_episode for boundary in boundaries)
    recall = (
        true_positive_count / actual_same_episode_count
        if actual_same_episode_count > 0
        else 1.0
    )

    return CandidateResult(
        family=rule.family,
        description=rule.description,
        fit_boundary_count=len(boundaries),
        predicted_same_episode_count=predicted_same_episode_count,
        same_episode_true_positive_count=true_positive_count,
        false_collapse_count=false_collapse_count,
        missed_cluster_count=missed_cluster_count,
        total_error_count=false_collapse_count + missed_cluster_count,
        precision=precision,
        recall=recall,
        false_collapse_examples=tuple(false_collapse_examples[:5]),
        missed_cluster_examples=tuple(missed_cluster_examples[:5]),
        complexity_rank=rule.complexity_rank,
        simplicity_penalty=rule.simplicity_penalty,
    )


def best_result_by_family(
    candidate_results: list[CandidateResult],
) -> dict[str, CandidateResult]:
    """Return the top-ranked candidate from each family."""
    best: dict[str, CandidateResult] = {}
    for result in candidate_results:
        if result.family not in best:
            best[result.family] = result
    return best


def shortlist_results(
    candidate_results: list[CandidateResult],
    limit: int,
) -> list[CandidateResult]:
    """Return a de-duplicated shortlist for human review.

    Keeps at least the best rule from each family, then fills remaining
    slots with the next-best overall candidates.
    """
    shortlist: list[CandidateResult] = []
    seen_descriptions: set[str] = set()
    seen_behaviors: set[
        tuple[
            int,
            int,
            int,
            tuple[str, ...],
            tuple[str, ...],
        ]
    ] = set()

    def behavior_key(result: CandidateResult) -> tuple[
        int,
        int,
        int,
        tuple[str, ...],
        tuple[str, ...],
    ]:
        return (
            result.false_collapse_count,
            result.missed_cluster_count,
            result.predicted_same_episode_count,
            result.false_collapse_examples,
            result.missed_cluster_examples,
        )

    for result in best_result_by_family(candidate_results).values():
        shortlist.append(result)
        seen_descriptions.add(result.description)
        seen_behaviors.add(behavior_key(result))

    for result in candidate_results:
        if len(shortlist) >= limit:
            break
        if result.description in seen_descriptions:
            continue
        if behavior_key(result) in seen_behaviors:
            continue
        shortlist.append(result)
        seen_descriptions.add(result.description)
        seen_behaviors.add(behavior_key(result))

    return sorted(
        shortlist,
        key=lambda result: (
            result.false_collapse_count,
            result.total_error_count,
            result.complexity_rank,
            result.simplicity_penalty,
            -result.same_episode_true_positive_count,
            result.description,
        ),
    )[:limit]


def serialize_candidate_result(result: CandidateResult) -> dict[str, object]:
    """Return a JSON-serializable candidate summary."""
    payload = asdict(result)
    payload["precision"] = round(result.precision, 6)
    payload["recall"] = round(result.recall, 6)
    return payload


def write_boundaries_csv(output_path: Path, boundaries: list[BoundaryRecord]) -> None:
    """Write labeled boundaries and derived features to CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "boundary_index",
                "feed_a_time",
                "feed_b_time",
                "gap_minutes",
                "volume_a_oz",
                "volume_b_oz",
                "label",
                "previous_gap_minutes",
                "next_gap_minutes",
                "feed_a_hour_of_day",
            ]
        )
        for boundary in boundaries:
            writer.writerow(
                [
                    boundary.boundary_index,
                    boundary.feed_a_time.isoformat(timespec="minutes"),
                    boundary.feed_b_time.isoformat(timespec="minutes"),
                    f"{boundary.gap_minutes:.1f}",
                    f"{boundary.volume_a_oz:.2f}",
                    f"{boundary.volume_b_oz:.2f}",
                    boundary.label,
                    (
                        f"{boundary.previous_gap_minutes:.1f}"
                        if boundary.previous_gap_minutes is not None
                        else ""
                    ),
                    (
                        f"{boundary.next_gap_minutes:.1f}"
                        if boundary.next_gap_minutes is not None
                        else ""
                    ),
                    f"{boundary.feed_a_hour_of_day:.2f}",
                ]
            )


def write_candidate_rules_csv(
    output_path: Path,
    candidate_results: list[CandidateResult],
) -> None:
    """Write candidate-rule summaries to CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "family",
                "description",
                "fit_boundary_count",
                "predicted_same_episode_count",
                "same_episode_true_positive_count",
                "false_collapse_count",
                "missed_cluster_count",
                "total_error_count",
                "precision",
                "recall",
            ]
        )
        for result in candidate_results:
            writer.writerow(
                [
                    result.family,
                    result.description,
                    result.fit_boundary_count,
                    result.predicted_same_episode_count,
                    result.same_episode_true_positive_count,
                    result.false_collapse_count,
                    result.missed_cluster_count,
                    result.total_error_count,
                    f"{result.precision:.6f}",
                    f"{result.recall:.6f}",
                ]
            )


def render_results_text(
    *,
    snapshot: ExportSnapshot,
    boundaries: list[BoundaryRecord],
    adopted_rule: CandidateResult,
    conservative_gap_only: CandidateResult,
    family_best: dict[str, CandidateResult],
    overall_shortlist: list[CandidateResult],
) -> str:
    """Render the human-readable research log."""
    same_episode_boundaries = [
        boundary for boundary in boundaries if boundary.label == LABEL_SAME_EPISODE
    ]
    same_episode_gaps = sorted(boundary.gap_minutes for boundary in same_episode_boundaries)
    lines = [
        f"Export: {snapshot.export_path}",
        f"Dataset: {snapshot.dataset_id}",
        f"Source hash: {snapshot.source_hash}",
        f"Cutoff: {snapshot.latest_activity_time.isoformat(timespec='seconds')}",
        "",
        "Label counts:",
    ]
    for label, count in summarize_label_counts(boundaries).items():
        lines.append(f"- {label}: {count}")
    if same_episode_gaps:
        below_conservative_cutoff = sum(gap <= 73.0 for gap in same_episode_gaps)
        median_gap = same_episode_gaps[len(same_episode_gaps) // 2]
        lines.extend(
            [
                (
                    "same_episode gap summary: "
                    f"min={same_episode_gaps[0]:.1f}m "
                    f"median={median_gap:.1f}m "
                    f"max={same_episode_gaps[-1]:.1f}m "
                    f"({below_conservative_cutoff} of {len(same_episode_gaps)} "
                    "at or below 73.0m)"
                )
            ]
        )
    lines.extend(
        [
            "",
            "Adopted rule:",
            format_candidate_result(adopted_rule),
            "",
            "Best conservative gap-only rule:",
            format_candidate_result(conservative_gap_only),
            "",
            "Best rule by family:",
        ]
    )
    for family, result in family_best.items():
        lines.append(f"- {family}: {format_candidate_result(result)}")

    lines.extend(["", "Overall shortlist:"])
    for index, result in enumerate(overall_shortlist, start=1):
        lines.append(f"{index}. {format_candidate_result(result)}")

    return "\n".join(lines) + "\n"


def format_candidate_result(result: CandidateResult) -> str:
    """Return a compact one-line result summary."""
    return (
        f"{result.description} | fp={result.false_collapse_count} "
        f"fn={result.missed_cluster_count} total={result.total_error_count} "
        f"precision={result.precision:.3f} recall={result.recall:.3f}"
    )


def threshold_simplicity_penalty(value: float) -> float:
    """Return a tie-break penalty for less-readable numeric thresholds.

    Integer and half-step thresholds are preferred when fit quality is the
    same. This nudges the analysis toward cleaner human-facing rules without
    affecting the primary accuracy ranking.

    Args:
        value: Numeric threshold under consideration.

    Returns:
        Non-negative penalty where lower means simpler.
    """
    return abs((value * 2.0) - round(value * 2.0))


if __name__ == "__main__":
    main()
