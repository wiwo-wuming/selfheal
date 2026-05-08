"""cleanup — remove old backup files and orphan backups."""
from pathlib import Path

import click

from selfheal.config import Config
from selfheal.core.applier import PatchApplier


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--max-age", default=30, help="Remove backups older than N days (default: 30)")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def cleanup(ctx: click.Context, config: str | None, max_age: int, force: bool) -> None:
    """Remove old backup files and orphan backups."""
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
