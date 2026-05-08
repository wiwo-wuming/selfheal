"""metrics — show self-healing metrics and statistics."""
import json
from pathlib import Path

import click

from selfheal.config import Config
from selfheal.core.metrics import MetricsCollector
from selfheal.registry import get_registry


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def metrics(ctx: click.Context, config: str | None, as_json: bool) -> None:
    """Show self-healing metrics and statistics."""
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    collector = MetricsCollector()

    try:
        registry = get_registry()
        store_cls = registry.get_store(cfg.store.type)
        if store_cls:
            store = store_cls(cfg.store)
            for event_type in ("failure", "classification", "patch", "validation"):
                events = store.get_events(event_type, limit=500)
                if len(events) == 500:
                    click.echo(
                        f"Note: {event_type} data truncated at 500 entries "
                        f"(use --json for larger datasets).", err=True
                    )
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
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        click.echo(f"Warning: could not load historical metrics from store: {e}", err=True)

    if as_json:
        click.echo(json.dumps(collector.summary(), indent=2))
    else:
        click.echo(collector.format_report())
