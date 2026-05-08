"""backups — list all tracked backup files."""
from pathlib import Path

import click

from selfheal.config import Config
from selfheal.core.applier import PatchApplier


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.pass_context
def backups(ctx: click.Context, config: str | None) -> None:
    """List all tracked backup files."""
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    applier = PatchApplier(cfg.engine)
    backups_list = applier.list_backups()

    if not backups_list:
        click.echo("No tracked backups found.")
        return

    click.echo(f"Tracked backups ({len(backups_list)}):\n")
    for pid, info in backups_list.items():
        status = "exists" if info["exists"] else "missing"
        click.echo(f"  [{status}] {pid[:12]}... → {info['target_file']}")
