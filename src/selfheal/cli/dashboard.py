"""dashboard — generate HTML dashboard or start interactive server."""
from pathlib import Path
from typing import Optional
import click


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--output", type=click.Path(), default=None, help="Write HTML to file instead of stdout")
@click.option("--serve", is_flag=True, help="Start interactive dashboard server")
@click.option("--port", default=8080, help="Server port (default: 8080)")
@click.option("--open", "open_browser", is_flag=True, help="Open browser automatically")
@click.option("--production", is_flag=True, help="Use gunicorn (production mode)")
@click.pass_context
def dashboard(ctx: click.Context, config: Optional[str], output: Optional[str], serve: bool, port: int, open_browser: bool, production: bool) -> None:
    """Generate an HTML dashboard or start an interactive dashboard server."""
    if config:
        from selfheal.config import Config as CfgCls
        CfgCls.from_file(Path(config))

    if serve:
        from selfheal.core.dashboard_server import run_server
        run_server(port=port, open_browser=open_browser, production=production)
        return

    from selfheal.core.dashboard import generate_html
    html = generate_html(output_path=output)
    if not output:
        click.echo(html)
