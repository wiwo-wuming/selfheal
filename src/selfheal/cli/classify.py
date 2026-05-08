"""classify — categorise a test failure."""
import json
import sys
from pathlib import Path

import click

from selfheal.config import Config
from selfheal.events import TestFailureEvent
from selfheal.registry import get_registry


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "classifier_type", default="rule", help="Classifier type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input failure JSON file")
@click.pass_context
def classify(ctx: click.Context, config: str | None, classifier_type: str, input_file: str) -> None:
    """Classify a test failure."""
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    with open(input_file) as f:
        data = json.load(f)

    event = TestFailureEvent(
        test_path=data["test_path"],
        error_type=data["error_type"],
        error_message=data["error_message"],
        traceback=data.get("traceback", ""),
    )

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
