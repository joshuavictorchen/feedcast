"""Check research docs and committed artifacts for drift."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import Iterable

TUNABLE_MODEL_CONSTANTS = {
    "analog_trajectory": (
        "ALIGNMENT",
        "FEATURE_WEIGHTS",
        "HISTORY_MODE",
        "K_NEIGHBORS",
        "LOOKBACK_HOURS",
        "RECENCY_HALF_LIFE_HOURS",
        "TRAJECTORY_LENGTH_METHOD",
    ),
    "latent_hunger": ("SATIETY_RATE",),
    "slot_drift": (
        "DRIFT_WEIGHT_HALF_LIFE_DAYS",
        "LOOKBACK_DAYS",
        "MATCH_COST_THRESHOLD_HOURS",
    ),
    "survival_hazard": ("DAYTIME_SHAPE", "OVERNIGHT_SHAPE"),
}

TEXT_ARTIFACT_SUFFIXES = {".json", ".md", ".txt", ".yaml", ".yml"}


def main(argv: list[str] | None = None) -> int:
    """Run the consistency checker as a CLI."""
    parser = argparse.ArgumentParser(
        description="Check research docs and committed artifacts for drift.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Research directories to check. Defaults to all model and research directories.",
    )
    args = parser.parse_args(argv)

    repo_root = _git_repo_root(Path.cwd())
    targets = _resolve_targets(repo_root, [Path(value) for value in args.paths])
    issues = find_consistency_issues(targets, repo_root=repo_root)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1

    print("Research consistency: ok")
    return 0


def find_consistency_issues(
    paths: Iterable[Path],
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Return human-readable consistency failures for the given paths."""
    resolved_repo_root = (
        repo_root.resolve() if repo_root is not None else _git_repo_root(Path.cwd())
    )
    targets = _resolve_targets(resolved_repo_root, list(paths))
    changed_paths = _git_changed_paths(resolved_repo_root)

    issues: list[str] = []
    for research_dir in targets:
        issues.extend(
            _check_research_dir(
                research_dir=research_dir,
                repo_root=resolved_repo_root,
                changed_paths=changed_paths,
            )
        )

    agents_dir = resolved_repo_root / "feedcast" / "agents"
    if agents_dir.is_dir():
        issues.extend(
            _check_agents_workspace(
                agents_dir=agents_dir,
                repo_root=resolved_repo_root,
                changed_paths=changed_paths,
            )
        )
    return issues


def _check_research_dir(
    *,
    research_dir: Path,
    repo_root: Path,
    changed_paths: set[Path],
) -> list[str]:
    """Check one research directory."""
    issues: list[str] = []
    relative_dir = research_dir.relative_to(repo_root)
    research_path = research_dir / "research.md"
    research_fields = _parse_last_run_fields(research_path.read_text(encoding="utf-8"))
    export_path = research_fields.get("Export")
    dataset_id = research_fields.get("Dataset")

    if export_path is None:
        issues.append(f"{relative_dir / 'research.md'}: missing Export in ## Last run")
    if dataset_id is None:
        issues.append(f"{relative_dir / 'research.md'}: missing Dataset in ## Last run")

    artifacts_dir = research_dir / "artifacts"
    results_path = artifacts_dir / "research_results.txt"
    results_text = ""
    if results_path.exists():
        results_text = results_path.read_text(encoding="utf-8")
        artifact_fields = _parse_results_fields(results_text)
        artifact_export = artifact_fields.get("Export")
        artifact_dataset = artifact_fields.get("Dataset")
        if export_path is not None and artifact_export != export_path:
            issues.append(
                f"{relative_dir}: research.md Export ({export_path}) does not match "
                f"artifacts/research_results.txt ({artifact_export})"
            )
        if dataset_id is not None and artifact_dataset != dataset_id:
            issues.append(
                f"{relative_dir}: research.md Dataset ({dataset_id}) does not match "
                f"artifacts/research_results.txt ({artifact_dataset})"
            )

    issues.extend(_find_volatile_artifact_issues(artifacts_dir, repo_root))

    changed_here = {
        path.relative_to(relative_dir)
        for path in changed_paths
        if _is_relative_to(path, relative_dir)
    }

    if _is_model_research_dir(relative_dir):
        if Path("model.py") in changed_here and Path("CHANGELOG.md") not in changed_here:
            issues.append(
                f"{relative_dir}: model.py changed without a matching CHANGELOG.md update"
            )
        if Path("model.py") in changed_here and Path("research.md") not in changed_here:
            issues.append(
                f"{relative_dir}: model.py changed without a matching research.md update"
            )
        issues.extend(_check_model_baseline(research_dir, repo_root, results_text))

    return issues


def _check_agents_workspace(
    *,
    agents_dir: Path,
    repo_root: Path,
    changed_paths: set[Path],
) -> list[str]:
    """Check the agent inference workspace: `model.py` holds the
    canonical forecast, so any change to it must ship with a
    `CHANGELOG.md` update. Other `.py` files (research, helpers,
    analysis) are allowed and unchecked.
    """
    relative_dir = agents_dir.relative_to(repo_root)
    changed_here = {
        path.relative_to(relative_dir)
        for path in changed_paths
        if _is_relative_to(path, relative_dir)
    }

    issues: list[str] = []

    if Path("model.py") in changed_here and Path("CHANGELOG.md") not in changed_here:
        issues.append(
            f"{relative_dir}: model.py changed without a matching CHANGELOG.md update"
        )

    return issues


def _check_model_baseline(
    research_dir: Path,
    repo_root: Path,
    results_text: str,
) -> list[str]:
    """Check that the research artifact matches the current model constants."""
    slug = research_dir.name
    constant_names = TUNABLE_MODEL_CONSTANTS.get(slug)
    if constant_names is None or not results_text:
        return []

    baseline_params = _parse_baseline_params(results_text)
    if baseline_params is None:
        return [
            f"{research_dir.relative_to(repo_root)}: artifacts/research_results.txt "
            "is missing a baseline parameter block"
        ]

    current_params = _read_model_constants(research_dir / "model.py", constant_names)
    if baseline_params == current_params:
        return []

    return [
        f"{research_dir.relative_to(repo_root)}: artifacts/research_results.txt baseline "
        f"{baseline_params} does not match model.py {current_params}"
    ]


def _resolve_targets(repo_root: Path, paths: list[Path]) -> list[Path]:
    """Resolve CLI paths into research directories."""
    if not paths:
        return _discover_research_dirs(repo_root)

    targets: list[Path] = []
    for raw_path in paths:
        path = (repo_root / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
        if path.is_file() and path.name == "research.md":
            targets.append(path.parent)
            continue
        if path.is_dir() and (path / "research.md").exists():
            targets.append(path)
            continue
        if path.is_dir():
            targets.extend(sorted(candidate.parent for candidate in path.rglob("research.md")))
            continue
        raise FileNotFoundError(f"No research directory found for {raw_path}")

    deduped: list[Path] = []
    seen: set[Path] = set()
    for target in sorted(targets):
        if target not in seen:
            deduped.append(target)
            seen.add(target)
    return deduped


def _discover_research_dirs(repo_root: Path) -> list[Path]:
    """Return every tracked research directory in the repo."""
    targets: list[Path] = []
    for base_dir in (repo_root / "feedcast" / "models", repo_root / "feedcast" / "research"):
        if not base_dir.exists():
            continue
        for candidate in sorted(base_dir.iterdir()):
            if candidate.is_dir() and (candidate / "research.md").exists():
                targets.append(candidate)
    return targets


def _parse_last_run_fields(markdown: str) -> dict[str, str]:
    """Parse the two-column markdown table under ``## Last run``."""
    in_last_run = False
    fields: dict[str, str] = {}
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line == "## Last run":
            in_last_run = True
            continue
        if in_last_run and line.startswith("## "):
            break
        if not in_last_run or not line.startswith("|"):
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) != 2:
            continue
        field, value = parts
        if field == "Field" or set(field) == {"-"}:
            continue
        fields[field] = _strip_markdown_wrappers(value)
    return fields


def _parse_results_fields(text: str) -> dict[str, str]:
    """Parse the leading metadata lines from ``research_results.txt``."""
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        if ":" not in raw_line:
            if fields:
                break
            continue
        field, value = raw_line.split(":", 1)
        if field in {"Export", "Dataset"}:
            fields[field] = value.strip()
        elif fields:
            break
    return fields


def _parse_baseline_params(results_text: str) -> dict[str, object] | None:
    """Return the baseline parameter dict embedded in a model artifact."""
    for raw_line in results_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("Baseline"):
            continue
        if ": {" not in line:
            continue
        _, payload = line.split(":", 1)
        parsed = ast.literal_eval(payload.strip())
        if isinstance(parsed, dict):
            return parsed
    return None


def _read_model_constants(model_path: Path, constant_names: tuple[str, ...]) -> dict[str, object]:
    """Read selected top-level constants from ``model.py``."""
    tree = ast.parse(model_path.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    wanted = set(constant_names)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    values[target.id] = _literal_from_expr(node.value)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id in wanted:
                values[target.id] = _literal_from_expr(node.value)

    missing = wanted - values.keys()
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"{model_path}: missing constants {missing_names}")
    return values


def _literal_from_expr(node: ast.AST | None) -> object:
    """Convert a restricted AST expression to a Python literal."""
    if node is None:
        raise ValueError("Expected an expression")
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal_from_expr(element) for element in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_literal_from_expr(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _literal_from_expr(key): _literal_from_expr(value)
            for key, value in zip(node.keys, node.values)
        }
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_from_expr(node.operand)
        if not isinstance(value, (int, float)):
            raise ValueError(f"Unsupported unary operand: {ast.dump(node)}")
        return -value
    if isinstance(node, ast.Call):
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "np"
            and function.attr == "array"
            and len(node.args) == 1
        ):
            return _literal_from_expr(node.args[0])
    raise ValueError(f"Unsupported constant expression: {ast.dump(node)}")


def _find_volatile_artifact_issues(artifacts_dir: Path, repo_root: Path) -> list[str]:
    """Return issues for volatile fields in committed artifacts."""
    if not artifacts_dir.exists():
        return []

    issues: list[str] = []
    for artifact_path in sorted(artifacts_dir.rglob("*")):
        if not artifact_path.is_file() or artifact_path.suffix not in TEXT_ARTIFACT_SUFFIXES:
            continue
        text = artifact_path.read_text(encoding="utf-8")
        if any(line.startswith("Run: ") for line in text.splitlines()):
            issues.append(
                f"{artifact_path.relative_to(repo_root)}: remove volatile Run timestamps"
            )
        if '"run_timestamp"' in text:
            issues.append(
                f"{artifact_path.relative_to(repo_root)}: remove volatile run_timestamp fields"
            )
    return issues


def _git_repo_root(start_dir: Path) -> Path:
    """Return the current git repo root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def _git_changed_paths(repo_root: Path) -> set[Path]:
    """Return tracked or untracked paths changed relative to HEAD."""
    changed: set[Path] = set()
    commands = (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if line:
                changed.add(Path(line))
    return changed


def _strip_markdown_wrappers(value: str) -> str:
    """Remove inline markdown wrappers from a table value."""
    stripped = value.strip()
    if stripped.startswith("`") and stripped.endswith("`"):
        return stripped[1:-1]
    if stripped.startswith("[") and "](" in stripped and stripped.endswith(")"):
        _, path = stripped.split("](", 1)
        return path[:-1]
    if "`" in stripped:
        segments = stripped.split("`")
        if len(segments) >= 3:
            return segments[1]
    return stripped


def _is_model_research_dir(relative_dir: Path) -> bool:
    """Whether the path is a model research directory."""
    parts = relative_dir.parts
    return len(parts) >= 3 and parts[0] == "feedcast" and parts[1] == "models"


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Compatibility helper for ``Path.is_relative_to``."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
