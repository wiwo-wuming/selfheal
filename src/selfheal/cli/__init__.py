"""CLI interface for SelfHeal."""
import click

from selfheal import __version__


@click.group()
@click.version_option(version=__version__)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """SelfHeal - Intelligent Test Self-Healing Framework."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# Commands are registered from individual modules under cli/
from selfheal.cli.watch import watch

main.add_command(watch)

from selfheal.cli.classify import classify

main.add_command(classify)

from selfheal.cli.patch import patch

main.add_command(patch)

from selfheal.cli.validate import validate

main.add_command(validate)

from selfheal.cli.report import report

main.add_command(report)

from selfheal.cli.batch import batch

main.add_command(batch)

from selfheal.cli.rollback import rollback

main.add_command(rollback)

from selfheal.cli.backups import backups

main.add_command(backups)

from selfheal.cli.cleanup import cleanup

main.add_command(cleanup)

from selfheal.cli.metrics import metrics

main.add_command(metrics)

from selfheal.cli.dashboard import dashboard

main.add_command(dashboard)

from selfheal.cli.init import init

main.add_command(init)

from selfheal.cli.apply import apply

main.add_command(apply)


if __name__ == "__main__":
    main()
