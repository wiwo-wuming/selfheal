"""CLI interface for SelfHeal."""

import json
import sys
from pathlib import Path
from typing import Any, Optional

import click

from selfheal import __version__
from selfheal.config import Config
from selfheal.engine import SelfHealEngine
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
    ValidationEvent,
)


def _reconstruct_failure_event(data: dict[str, Any]) -> TestFailureEvent:
    """Reconstruct a TestFailureEvent from a serialised dict."""
    return TestFailureEvent(
        test_path=data["test_path"],
        error_type=data["error_type"],
        error_message=data["error_message"],
        traceback=data.get("traceback", ""),
    )


def _reconstruct_classification_event(data: dict[str, Any]) -> ClassificationEvent:
    """Reconstruct a ClassificationEvent from a serialised dict."""
    original = _reconstruct_failure_event(data["original_event"])
    return ClassificationEvent(
        original_event=original,
        category=data["category"],
        severity=ErrorSeverity(data["severity"]),
        confidence=data["confidence"],
        reasoning=data.get("reasoning", ""),
    )


@click.group()
@click.version_option(version=__version__)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """SelfHeal - Intelligent Test Self-Healing Framework."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--watch", multiple=True, help="Paths to watch")
@click.option("--auto-apply", is_flag=True, help="Automatically apply generated patches")
@click.pass_context
def watch(ctx: click.Context, config: Optional[str], watch: tuple, auto_apply: bool) -> None:
    """Start watching for test failures."""
    verbose = ctx.obj.get("verbose", False)

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    if auto_apply:
        cfg.engine.auto_apply = True
        click.echo("⚠️  Auto-apply mode ENABLED - patches will be written to source files")

    engine = SelfHealEngine(cfg)

    click.echo("Starting SelfHeal watch mode...")
    try:
        engine.watch(list(watch) if watch else [cfg.watcher.path])
    except KeyboardInterrupt:
        click.echo("\nShutting down...")
        click.echo(engine.get_metrics_report())
        engine.shutdown()


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "classifier_type", default="rule", help="Classifier type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input failure JSON file")
@click.pass_context
def classify(ctx: click.Context, config: Optional[str], classifier_type: str, input_file: str) -> None:
    """Classify a test failure."""
    import json
    from selfheal.events import TestFailureEvent
    from selfheal.registry import get_registry

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    # Load event from file
    with open(input_file) as f:
        data = json.load(f)

    event = TestFailureEvent(
        test_path=data["test_path"],
        error_type=data["error_type"],
        error_message=data["error_message"],
        traceback=data.get("traceback", ""),
    )

    # Classify
    registry = get_registry()
    classifier_cls = registry.get_classifier(classifier_type)
    if not classifier_cls:
        click.echo(f"Unknown classifier type: {classifier_type}", err=True)
        sys.exit(1)

    classifier = classifier_cls(cfg.classifier)
    result = classifier.classify(event)

    click.echo(f"Category: {result.category}")
    click.echo(f"Severity: {result.severity.value}")
    click.echo(f"Confidence: {result.confidence:.2f}")
    if result.reasoning:
        click.echo(f"Reasoning: {result.reasoning}")


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "patcher_type", default="template", help="Patcher type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input classification JSON file")
@click.pass_context
def patch(ctx: click.Context, config: Optional[str], patcher_type: str, input_file: str) -> None:
    """Generate a patch for a failure."""
    from selfheal.registry import get_registry

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    # Load event from file
    with open(input_file) as f:
        data = json.load(f)

    original_event = _reconstruct_failure_event(data["original_event"])
    classification = ClassificationEvent(
        original_event=original_event,
        category=data["category"],
        severity=ErrorSeverity(data["severity"]),
        confidence=data["confidence"],
        reasoning=data.get("reasoning", ""),
    )

    # Generate patch
    registry = get_registry()
    patcher_cls = registry.get_patcher(patcher_type)
    if not patcher_cls:
        click.echo(f"Unknown patcher type: {patcher_type}", err=True)
        sys.exit(1)

    patcher = patcher_cls(cfg.patcher)
    result = patcher.generate(classification)

    click.echo(f"Patch ID: {result.patch_id}")
    click.echo(f"Generator: {result.generator}")
    click.echo(f"\nPatch Content:\n{result.patch_content}")


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "validator_type", default="local", help="Validator type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input patch JSON file")
@click.pass_context
def validate(ctx: click.Context, config: Optional[str], validator_type: str, input_file: str) -> None:
    """Validate a patch."""
    from selfheal.registry import get_registry

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    # Load patch event from file
    with open(input_file) as f:
        data = json.load(f)

    # Reconstruct PatchEvent from JSON
    classification_data = data["classification_event"]
    classification = _reconstruct_classification_event(classification_data)
    patch = PatchEvent(
        classification_event=classification,
        patch_id=data["patch_id"],
        patch_content=data["patch_content"],
        generator=data["generator"],
    )

    # Validate
    registry = get_registry()
    validator_cls = registry.get_validator(validator_type)
    if not validator_cls:
        click.echo(f"Unknown validator type: {validator_type}", err=True)
        sys.exit(1)

    validator = validator_cls(cfg.validator)
    result = validator.validate(patch)

    click.echo(f"Result: {result.result}")
    click.echo(f"Duration: {result.duration:.2f}s")
    if result.test_output:
        click.echo(f"\nTest Output:\n{result.test_output}")
    if result.error_message:
        click.echo(f"\nError: {result.error_message}")


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "reporter_type", default="terminal", help="Reporter type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input validation JSON file")
@click.pass_context
def report(ctx: click.Context, config: Optional[str], reporter_type: str, input_file: str) -> None:
    """Generate a report from a validation event."""
    from datetime import datetime
    from selfheal.registry import get_registry

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    # Load validation event from file
    with open(input_file) as f:
        data = json.load(f)

    # Reconstruct full event chain from JSON
    patch_data = data["patch_event"]
    classification_data = patch_data["classification_event"]
    classification = _reconstruct_classification_event(classification_data)
    patch = PatchEvent(
        classification_event=classification,
        patch_id=patch_data["patch_id"],
        patch_content=patch_data["patch_content"],
        generator=patch_data["generator"],
    )
    validation = ValidationEvent(
        patch_event=patch,
        result=data["result"],
        test_output=data.get("test_output", ""),
        duration=data.get("duration", 0.0),
        error_message=data.get("error_message", ""),
        timestamp=datetime.now(),
    )

    # Report
    registry = get_registry()
    reporter_cls = registry.get_reporter(reporter_type)
    if not reporter_cls:
        click.echo(f"Unknown reporter type: {reporter_type}", err=True)
        sys.exit(1)

    reporter = reporter_cls(cfg.reporter)
    reporter.report(validation)


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="JSON file with array of failure events")
@click.option("--auto-apply", is_flag=True, help="Automatically apply generated patches")
@click.option("--dry-run", is_flag=True, help="Preview patches without modifying any files")
@click.pass_context
def batch(ctx: click.Context, config: Optional[str], input_file: str, auto_apply: bool, dry_run: bool) -> None:
    """Process multiple test failures in batch."""
    import json
    from selfheal.events import TestFailureEvent

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    if auto_apply:
        cfg.engine.auto_apply = True
        click.echo("⚠️  Auto-apply mode ENABLED")

    if dry_run:
        cfg.engine.dry_run = True
        click.echo("[DRY-RUN] Patches will be previewed but NOT applied")

    # Load failures from JSON
    with open(input_file) as f:
        data = json.load(f)

    # Accept both a single object or a list
    if isinstance(data, dict):
        data = [data]

    events = [
        TestFailureEvent(
            test_path=item["test_path"],
            error_type=item["error_type"],
            error_message=item["error_message"],
            traceback=item.get("traceback", ""),
        )
        for item in data
    ]

    engine = SelfHealEngine(cfg)
    click.echo(f"Processing {len(events)} failures...")

    results = engine.process_batch(events)

    passed = sum(1 for r in results if r.result == "passed")
    failed = sum(1 for r in results if r.result == "failed")

    click.echo(f"\nBatch complete: {passed} passed, {failed} failed, {len(results)} total")
    click.echo(engine.get_metrics_report())
    engine.shutdown()


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--patch-id", default=None, help="Rollback a specific patch (default: list available)")
@click.option("--all", "rollback_all", is_flag=True, help="Rollback all tracked patches")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def rollback(ctx: click.Context, config: Optional[str], patch_id: Optional[str], rollback_all: bool, force: bool) -> None:
    """Rollback applied patches from backup files.

    Without --patch-id or --all, lists all tracked backups.
    """
    from selfheal.core.applier import PatchApplier

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    applier = PatchApplier(cfg.engine)
    backups = applier.list_backups()

    if not backups:
        click.echo("No tracked backups found. Nothing to rollback.")
        return

    # List mode
    if not patch_id and not rollback_all:
        click.echo(f"Found {len(backups)} tracked backup(s):\n")
        for pid, info in backups.items():
            status = "[OK]" if info["exists"] else "[MISSING]"
            click.echo(
                f"  {pid[:12]}...  {status}"
                f"\n    target: {info['target_file']}"
                f"\n    backup: {info['backup_path']}"
                f"\n    size: {info['size']} bytes, created: {info['created']}\n"
            )
        click.echo("Use --patch-id <ID> to rollback one, or --all to rollback all.")
        return

    # Rollback all
    if rollback_all:
        if not force:
            click.echo(f"WARNING: About to rollback {len(backups)} patch(es).")
            click.confirm("Continue?", abort=True)

        rolled = 0
        for pid, info in backups.items():
            if not info["exists"]:
                click.echo(f"  Skip {pid[:12]}: backup file missing")
                continue
            # Create a minimal PatchEvent for rollback
            patch = _make_rollback_patch(pid, info)
            if applier.rollback(patch):
                click.echo(f"  [OK] Rolled back: {info['target_file']}")
                rolled += 1
            else:
                click.echo(f"  [FAIL] Failed: {info['target_file']}")
        click.echo(f"\nRollback complete: {rolled}/{len(backups)} succeeded")
        return

    # Rollback one
    if patch_id:
        # Find by prefix match
        matched = {pid: info for pid, info in backups.items() if pid.startswith(patch_id)}
        if not matched:
            click.echo(f"No backup found for patch ID starting with: {patch_id}")
            return
        if len(matched) > 1:
            click.echo(f"Ambiguous patch ID. Matches: {list(matched.keys())}")
            return

        pid, info = next(iter(matched.items()))
        if not info["exists"]:
            click.echo(f"Backup file missing for {pid[:12]}")
            return

        patch = _make_rollback_patch(pid, info)
        if applier.rollback(patch):
            click.echo(f"[OK] Rolled back: {info['target_file']}")
        else:
            click.echo(f"[FAIL] Rollback failed for: {info['target_file']}")


def _make_rollback_patch(patch_id: str, info: dict):
    """Create a minimal PatchEvent for rollback operations."""
    from selfheal.events import PatchEvent, ClassificationEvent, ErrorSeverity, TestFailureEvent
    dummy_event = TestFailureEvent(
        test_path=info["target_file"],
        error_type="rolled_back",
        error_message="Manual rollback",
    )
    dummy_classification = ClassificationEvent(
        original_event=dummy_event,
        category="unknown",
        severity=ErrorSeverity.MEDIUM,
        confidence=0.0,
    )
    return PatchEvent(
        classification_event=dummy_classification,
        patch_id=patch_id,
        patch_content="",
        generator="rollback",
        target_file=info["target_file"],
        backup_path=info["backup_path"],
    )


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.pass_context
def backups(ctx: click.Context, config: Optional[str]) -> None:
    """List all tracked backup files."""
    from selfheal.core.applier import PatchApplier

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    applier = PatchApplier(cfg.engine)
    backups = applier.list_backups()

    if not backups:
        click.echo("No tracked backups found.")
        return

    click.echo(f"Tracked backups ({len(backups)}):\n")
    for pid, info in backups.items():
        status = "exists" if info["exists"] else "missing"
        click.echo(f"  [{status}] {pid[:12]}... → {info['target_file']}")


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--max-age", default=30, help="Remove backups older than N days (default: 30)")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def cleanup(ctx: click.Context, config: Optional[str], max_age: int, force: bool) -> None:
    """Remove old backup files and orphan backups."""
    from selfheal.core.applier import PatchApplier

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    applier = PatchApplier(cfg.engine)

    if not force:
        click.echo(f"WARNING: This will remove backups older than {max_age} days and orphan files.")
        click.confirm("Continue?", abort=True)

    stats = applier.cleanup_backups(max_age_days=max_age)
    click.echo(
        f"Cleanup complete:\n"
        f"  Removed expired: {stats['removed_index_entries']}\n"
        f"  Removed orphans: {stats['removed_orphan_files']}\n"
        f"  Errors: {stats['errors']}"
    )


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def metrics(ctx: click.Context, config: Optional[str], as_json: bool) -> None:
    """Show self-healing metrics and statistics."""
    import json
    from selfheal.core.metrics import MetricsCollector
    from selfheal.registry import get_registry

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    collector = MetricsCollector()

    # Load historical events from the configured store to populate metrics
    # Use aggregated queries when possible to avoid loading all events into memory
    try:
        registry = get_registry()
        store_cls = registry.get_store(cfg.store.type)
        if store_cls:
            store = store_cls(cfg.store)
            for event_type in ("failure", "classification", "patch", "validation"):
                events = store.get_events(event_type, limit=500)
                if len(events) == 500:
                    click.echo(
                        f"Note: {event_type} data truncated at 500 entries "
                        f"(use --json for larger datasets).", err=True
                    )
                for event in events:
                    if event_type == "failure":
                        collector.record_failure()
                    elif event_type == "classification":
                        collector.record_classification(
                            event.category, event.severity.value
                        )
                    elif event_type == "patch":
                        collector.record_patch(event.status)
                    elif event_type == "validation":
                        collector.record_validation(event.result, event.duration)
            store.close()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        click.echo(f"Warning: could not load historical metrics from store: {e}", err=True)

    if as_json:
        click.echo(json.dumps(collector.summary(), indent=2))
    else:
        click.echo(collector.format_report())


@main.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--output", type=click.Path(), default=None, help="Write HTML to file instead of stdout")
@click.pass_context
def dashboard(ctx: click.Context, config: Optional[str], output: Optional[str]) -> None:
    """Generate an HTML dashboard of self-healing statistics."""
    from selfheal.config import Config as CfgCls
    from selfheal.core.dashboard import generate_html

    if config:
        CfgCls.from_file(Path(config))  # trigger experience store setup

    html = generate_html(output_path=output)

    if not output:
        click.echo(html)


@main.command()
@click.option("--output", type=click.Path(), default="selfheal.yaml", help="Output config file")
def init(output: str) -> None:
    """Initialize a new SelfHeal configuration."""
    default_config = Config()

    config_path = Path(output)
    default_config.to_file(config_path)

    click.echo(f"Configuration written to {config_path}")
    click.echo("\nEdit the configuration file to customize your setup.")


if __name__ == "__main__":
    main()
