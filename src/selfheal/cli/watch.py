"""watch — start watching for test failures."""
from pathlib import Path
from typing import Optional
import click
from selfheal.config import Config
from selfheal.engine import SelfHealEngine


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--watch", multiple=True, help="Paths to watch")
@click.option("--auto-apply", is_flag=True, help="Automatically apply generated patches")
@click.pass_context
def watch(ctx: click.Context, config: Optional[str], watch: tuple, auto_apply: bool) -> None:
    """Start watching for test failures."""
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
