"""CLI interface for SelfHeal."""

import sys
from pathlib import Path
from typing import Optional

import click

from selfheal import __version__
from selfheal.config import Config
from selfheal.engine import SelfHealEngine


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
    import json
    from selfheal.events import ClassificationEvent, ErrorSeverity, TestFailureEvent
    from selfheal.registry import get_registry

    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    # Load event from file
    with open(input_file) as f:
        data = json.load(f)

    original_data = data["original_event"]
    original_event = TestFailureEvent(
        test_path=original_data["test_path"],
        error_type=original_data["error_type"],
        error_message=original_data["error_message"],
        traceback=original_data.get("traceback", ""),
    )

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
    import json
    from selfheal.events import (
        ClassificationEvent,
        ErrorSeverity,
        PatchEvent,
        TestFailureEvent,
    )
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
    original_data = classification_data["original_event"]
    original = TestFailureEvent(
        test_path=original_data["test_path"],
        error_type=original_data["error_type"],
        error_message=original_data["error_message"],
        traceback=original_data.get("traceback", ""),
    )
    classification = ClassificationEvent(
        original_event=original,
        category=classification_data["category"],
        severity=ErrorSeverity(classification_data["severity"]),
        confidence=classification_data["confidence"],
        reasoning=classification_data.get("reasoning", ""),
    )
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
    import json
    from datetime import datetime
    from selfheal.events import (
        ClassificationEvent,
        ErrorSeverity,
        PatchEvent,
        TestFailureEvent,
        ValidationEvent,
    )
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
    original_data = classification_data["original_event"]

    original = TestFailureEvent(
        test_path=original_data["test_path"],
        error_type=original_data["error_type"],
        error_message=original_data["error_message"],
        traceback=original_data.get("traceback", ""),
    )
    classification = ClassificationEvent(
        original_event=original,
        category=classification_data["category"],
        severity=ErrorSeverity(classification_data["severity"]),
        confidence=classification_data["confidence"],
        reasoning=classification_data.get("reasoning", ""),
    )
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
@click.pass_context
def batch(ctx: click.Context, config: Optional[str], input_file: str, auto_apply: bool) -> None:
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
    try:
        registry = get_registry()
        store_cls = registry.get_store(cfg.store.type)
        if store_cls:
            store = store_cls(cfg.store)
            for event_type in ("failure", "classification", "patch", "validation"):
                events = store.get_events(event_type, limit=1000)
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
    except Exception as e:
        click.echo(f"Warning: could not load historical metrics from store: {e}", err=True)

    if as_json:
        click.echo(json.dumps(collector.summary(), indent=2))
    else:
        click.echo(collector.format_report())


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
