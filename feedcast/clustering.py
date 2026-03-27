"""Episode grouping for close-together feeds.

Consecutive bottle feeds that occur close in time often represent a single
feeding episode (e.g., a large feed followed by a small top-up). This module
provides a deterministic rule for collapsing such feeds into episodes.

The rule was derived from hand-labeled boundary data:
    feedcast/research/feed_clustering/findings.md

Two consecutive feeds belong to the same episode if:
    gap <= 73 minutes, OR
    gap <= 80 minutes AND the later feed <= 1.50 oz

Consumers: evaluation (collapse both actuals and predictions before scoring),
consensus blend (collapse before voting), reports (default to episode view),
and models that opt into episode-level history (e.g., Slot Drift, Latent
Hunger). Models receive raw events and decide how to handle episodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from feedcast.data import FeedEvent, ForecastPoint

# Adopted episode-boundary rule (see feedcast/research/feed_clustering/).
# Two feeds are part of the same episode if the gap satisfies either arm.
BASE_GAP_MINUTES = 73
EXTENSION_GAP_MINUTES = 80
SECOND_FEED_MAX_OZ = 1.50


@dataclass(frozen=True)
class FeedEpisode:
    """A group of one or more feeds that form a single feeding episode.

    Attributes:
        time: Timestamp of the first constituent feed.
        volume_oz: Sum of constituent feed volumes.
        feed_count: Number of constituent feeds.
        constituents: The raw feed objects that were grouped.
    """

    time: datetime
    volume_oz: float
    feed_count: int
    constituents: tuple[FeedEvent | ForecastPoint, ...]


def group_into_episodes(
    feeds: Sequence[FeedEvent] | Sequence[ForecastPoint],
) -> list[FeedEpisode]:
    """Group consecutive feeds into episodes using the adopted boundary rule.

    Feeds that are close in time are collapsed into a single episode. The rule
    is applied transitively: if A->B and B->C both satisfy the boundary rule,
    all three feeds belong to one episode even if A->C exceeds the extension
    window.

    Each new feed is compared to the last feed in the current episode (not the
    anchor) to decide whether it continues the episode.

    Args:
        feeds: Feed events or forecast points, must be in strictly
            increasing chronological order (no duplicates).

    Returns:
        Episodes in chronological order. A feed that doesn't cluster with
        its neighbors becomes a single-feed episode.

    Raises:
        ValueError: If timestamps are not strictly increasing.
    """
    if not feeds:
        return []

    # Validate chronological order.
    for i in range(1, len(feeds)):
        if feeds[i].time <= feeds[i - 1].time:
            raise ValueError(
                f"Feeds are not in chronological order: "
                f"feed at index {i} ({feeds[i].time.isoformat()}) precedes "
                f"feed at index {i - 1} ({feeds[i - 1].time.isoformat()})"
            )

    episodes: list[FeedEpisode] = []
    # Accumulate constituents for the current episode.
    current: list[FeedEvent | ForecastPoint] = [feeds[0]]

    for i in range(1, len(feeds)):
        feed = feeds[i]
        last = current[-1]
        gap_minutes = (feed.time - last.time).total_seconds() / 60

        if _is_same_episode(gap_minutes, feed.volume_oz):
            current.append(feed)
        else:
            episodes.append(_build_episode(current))
            current = [feed]

    # Flush the final episode.
    episodes.append(_build_episode(current))
    return episodes


def episodes_as_events(history: list[FeedEvent]) -> list[FeedEvent]:
    """Collapse raw feed history into episode-level synthetic FeedEvents.

    Each episode becomes a single FeedEvent with the episode's canonical
    timestamp (first constituent) and summed volume. This removes
    cluster-internal feeds so downstream consumers operate on real feeding
    episodes rather than inflated raw counts.

    The synthetic events use bottle_volume_oz = volume_oz and
    breastfeeding_volume_oz = 0.0. No model uses the breastfeed split
    field for forecast logic; the total volume_oz is correct regardless.
    """
    episodes = group_into_episodes(history)
    return [
        FeedEvent(
            time=episode.time,
            volume_oz=episode.volume_oz,
            bottle_volume_oz=episode.volume_oz,
            breastfeeding_volume_oz=0.0,
        )
        for episode in episodes
    ]


def _is_same_episode(gap_minutes: float, second_feed_volume_oz: float) -> bool:
    """Return True if a boundary continues the current episode."""
    if gap_minutes <= BASE_GAP_MINUTES:
        return True
    if gap_minutes <= EXTENSION_GAP_MINUTES and second_feed_volume_oz <= SECOND_FEED_MAX_OZ:
        return True
    return False


def _build_episode(
    constituents: list[FeedEvent | ForecastPoint],
) -> FeedEpisode:
    """Construct a FeedEpisode from its accumulated constituents."""
    return FeedEpisode(
        time=constituents[0].time,
        volume_oz=sum(feed.volume_oz for feed in constituents),
        feed_count=len(constituents),
        constituents=tuple(constituents),
    )
