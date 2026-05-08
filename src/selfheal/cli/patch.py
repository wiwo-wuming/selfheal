"""patch — generate a code patch for a failure."""
import json
import sys
from pathlib import Path

import click

from selfheal.cli.utils import reconstruct_failure_event
from selfheal.config import Config
from selfheal.events import ClassificationEvent, ErrorSeverity
from selfheal.registry import get_registry


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "patcher_type", default="template", help="Patcher type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input classification JSON file")
@click.pass_context
def patch(ctx: click.Context, config: str | None, patcher_type: str, input_file: str) -> None:
    """Generate a patch for a failure."""
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    with open(input_file) as f:
        data = json.load(f)

    original_event = reconstruct_failure_event(data["original_event"])
    classification = ClassificationEvent(
        original_event=original_event,
        category=data["category"],
        severity=ErrorSeverity(data["severity"]),
        confidence=data["confidence"],
        reasoning=data.get("reasoning", ""),
    )

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
