"""rollback — revert applied patches from backup files."""
from pathlib import Path
from typing import Optional
import click
from selfheal.config import Config
from selfheal.core.applier import PatchApplier
from selfheal.cli.utils import make_rollback_patch


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--patch-id", default=None, help="Rollback a specific patch (default: list available)")
@click.option("--all", "rollback_all", is_flag=True, help="Rollback all tracked patches")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def rollback(ctx: click.Context, config: Optional[str], patch_id: Optional[str], rollback_all: bool, force: bool) -> None:
    """Rollback applied patches from backup files.

    Without --patch-id or --all, lists all tracked backups.
    """
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    applier = PatchApplier(cfg.engine)
    backups = applier.list_backups()

    if not backups:
        click.echo("No tracked backups found. Nothing to rollback.")
        return

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

    if rollback_all:
        if not force:
            click.echo(f"WARNING: About to rollback {len(backups)} patch(es).")
            click.confirm("Continue?", abort=True)

        rolled = 0
        for pid, info in backups.items():
            if not info["exists"]:
                click.echo(f"  Skip {pid[:12]}: backup file missing")
                continue
            patch = make_rollback_patch(pid, info)
            if applier.rollback(patch):
                click.echo(f"  [OK] Rolled back: {info['target_file']}")
                rolled += 1
            else:
                click.echo(f"  [FAIL] Failed: {info['target_file']}")
        click.echo(f"\nRollback complete: {rolled}/{len(backups)} succeeded")
        return

    if patch_id:
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

        patch = make_rollback_patch(pid, info)
        if applier.rollback(patch):
            click.echo(f"[OK] Rolled back: {info['target_file']}")
        else:
            click.echo(f"[FAIL] Rollback failed for: {info['target_file']}")
