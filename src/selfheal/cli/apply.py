"""apply — apply a generated patch to a source file."""
import json
import sys
from pathlib import Path
from typing import Optional
import click
from selfheal.config import Config
from selfheal.core.applier import PatchApplier
from selfheal.events import PatchEvent, ClassificationEvent, ErrorSeverity, TestFailureEvent


@click.command()
@click.option("--config", type=click.Path(exists=True), help="Path to config file")
@click.option("--input", "input_file", type=click.Path(exists=True), required=True, help="Input patch JSON file")
@click.option("--target", type=click.Path(), default=None, help="Override target file path")
@click.option("--auto-apply", is_flag=True, help="Automatically apply the patch to the source file")
@click.option("--dry-run", is_flag=True, help="Preview patch without modifying files")
@click.pass_context
def apply(ctx: click.Context, config: Optional[str], input_file: str, target: Optional[str], auto_apply: bool, dry_run: bool) -> None:
    """Apply a generated patch to a source file.

    Reads a patch event JSON file and applies the patch to the target
    file with automatic backup.  Use --dry-run to preview first.
    """
    if config:
        cfg = Config.from_file(Path(config))
    else:
        cfg = Config.load_default()

    with open(input_file) as f:
        data = json.load(f)

    classification_data = data.get("classification_event", {})
    original_data = classification_data.get("original_event", {})
    original = TestFailureEvent(
        test_path=original_data.get("test_path", ""),
        error_type=original_data.get("error_type", "Unknown"),
        error_message=original_data.get("error_message", ""),
        traceback=original_data.get("traceback", ""),
    )
    classification = ClassificationEvent(
        original_event=original,
        category=classification_data.get("category", "unknown"),
        severity=ErrorSeverity(classification_data.get("severity", "medium")),
        confidence=classification_data.get("confidence", 0.0),
        reasoning=classification_data.get("reasoning", ""),
    )
    patch = PatchEvent(
        classification_event=classification,
        patch_id=data.get("patch_id", "unknown"),
        patch_content=data.get("patch_content", ""),
        generator=data.get("generator", "unknown"),
        target_file=target or data.get("target_file"),
    )

    applier = PatchApplier(cfg.engine)

    if dry_run:
        preview = applier.dry_run_preview(patch)
        click.echo(f"--- Dry-run preview for patch {patch.patch_id} ---")
        click.echo(preview)
        return

    if not patch.target_file:
        click.echo("Error: no target file specified. Use --target or include target_file in the JSON.", err=True)
        sys.exit(1)

    if auto_apply:
        if not applier.apply(patch):
            click.echo(f"Failed to apply patch {patch.patch_id}", err=True)
            sys.exit(1)
        click.echo(f"[OK] Applied patch {patch.patch_id} -> {patch.target_file}")
        click.echo(f"Backup saved at: {patch.backup_path}")
    else:
        click.echo(f"Patch {patch.patch_id} loaded but NOT applied (use --auto-apply to apply)")
        click.echo(f"Target: {patch.target_file}")
        click.echo(f"\nPatch content:\n{patch.patch_content[:500]}")
        if len(patch.patch_content) > 500:
            click.echo("... (truncated)")
