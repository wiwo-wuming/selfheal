"""batch — process multiple test failures at once."""
import json
from pathlib import Path
from typing import Optional
import click
from selfheal.config import Config
from selfheal.engine import SelfHealEngine
from selfheal.events import TestFailureEvent


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="JSON file with array of failure events")
@click.option("--auto-apply", is_flag=True, help="Automatically apply generated patches")
@click.option("--dry-run", is_flag=True, help="Preview patches without modifying any files")
@click.pass_context
def batch(ctx: click.Context, config: Optional[str], input_file: str, auto_apply: bool, dry_run: bool) -> None:
    """Process multiple test failures in batch."""
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

    with open(input_file) as f:
        data = json.load(f)

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
