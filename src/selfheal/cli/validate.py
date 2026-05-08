"""validate — run tests against a generated patch."""
import json
import sys
from pathlib import Path

import click

from selfheal.cli.utils import reconstruct_classification_event
from selfheal.config import Config
from selfheal.events import PatchEvent
from selfheal.registry import get_registry


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--type", "validator_type", default="local", help="Validator type")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input patch JSON file")
@click.pass_context
def validate(ctx: click.Context, config: str | None, validator_type: str, input_file: str) -> None:
    """Validate a patch."""
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    with open(input_file) as f:
        data = json.load(f)

    classification = reconstruct_classification_event(data["classification_event"])
    patch = PatchEvent(
        classification_event=classification,
        patch_id=data["patch_id"],
        patch_content=data["patch_content"],
        generator=data["generator"],
    )

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
