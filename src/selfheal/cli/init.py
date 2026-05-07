"""init — create a new SelfHeal configuration file."""
from pathlib import Path
import click
from selfheal.config import Config


@click.command()
@click.option("--output", type=click.Path(), default="selfheal.yaml", help="Output config file")
def init(output: str) -> None:
    """Initialize a new SelfHeal configuration."""
    default_config = Config()
    config_path = Path(output)
    default_config.to_file(config_path)
    click.echo(f"Configuration written to {config_path}")
    click.echo("\nEdit the configuration file to customize your setup.")
