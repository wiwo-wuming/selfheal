"""report — send a notification about a validation result."""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import click
from selfheal.config import Config
from selfheal.events import PatchEvent, ValidationEvent
from selfheal.cli.utils import reconstruct_classification_event
from selfheal.registry import get_registry


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "reporter_type", default="terminal", help="Reporter type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input validation JSON file")
@click.pass_context
def report(ctx: click.Context, config: Optional[str], reporter_type: str, input_file: str) -> None:
    """Generate a report from a validation event."""
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    with open(input_file) as f:
        data = json.load(f)

    patch_data = data["patch_event"]
    classification = reconstruct_classification_event(patch_data["classification_event"])
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

    registry = get_registry()
    reporter_cls = registry.get_reporter(reporter_type)
    if not reporter_cls:
        click.echo(f"Unknown reporter type: {reporter_type}", err=True)
        sys.exit(1)

    reporter = reporter_cls(cfg.reporter)
    reporter.report(validation)
