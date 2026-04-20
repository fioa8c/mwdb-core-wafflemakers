import os
import time

import click
from flask import g
from flask.cli import with_appcontext

from mwdb.core.normalize import analyze_via_sandbox

PHP_EXTENSIONS = {".php", ".phtml", ".php5", ".php7", ".inc"}
ERROR_THRESHOLD = 100


def _is_php_file(file_name):
    if not file_name:
        return False
    _, ext = os.path.splitext(file_name.lower())
    return ext in PHP_EXTENSIONS


@click.command("evalhook-php")
@with_appcontext
@click.argument("sandbox_url")
def evalhook_php(sandbox_url):
    """Run eval() hook analysis on PHP samples that contain eval()."""
    from mwdb.model import db
    from mwdb.model.blob import TextBlob
    from mwdb.model.file import File

    g.auth_user = None

    sandbox_url = sandbox_url.rstrip("/")

    click.echo("Finding PHP files with eval() that lack evalhook output...")

    all_files = db.session.query(File).all()
    php_files = [f for f in all_files if _is_php_file(f.file_name)]
    click.echo(f"Found {len(php_files)} PHP files total.")

    evalhook_blob_ids = set()
    existing_blobs = (
        db.session.query(TextBlob).filter(TextBlob.blob_type == "evalhook-output").all()
    )
    for blob in existing_blobs:
        for parent in blob.parents:
            evalhook_blob_ids.add(parent.id)

    already_done = 0
    to_check = []
    for f in php_files:
        if f.id in evalhook_blob_ids:
            already_done += 1
        else:
            to_check.append(f)

    click.echo(f"  Already have evalhook: {already_done}")
    click.echo(f"  Need to check for eval(): {len(to_check)}")

    to_process = []
    for f in to_check:
        try:
            fh = f.open()
            try:
                content = fh.read().decode("utf-8", errors="replace")
            finally:
                f.close(fh)
            if "eval(" in content:
                to_process.append((f, content))
        except Exception:
            pass

    click.echo(f"  Contain eval(): {len(to_process)}")

    if not to_process:
        click.echo("Nothing to process.")
        return

    processed = 0
    skipped_no_result = 0
    errors = 0
    start_time = time.time()
    total = len(to_process)

    for i, (file_obj, php_code) in enumerate(to_process, 1):
        try:
            evalhook_output = analyze_via_sandbox(sandbox_url, php_code)
            if not evalhook_output:
                click.echo(f"[{i}/{total}] Skipped (no output) {file_obj.file_name}")
                skipped_no_result += 1
                continue

            TextBlob.get_or_create(
                content=evalhook_output,
                blob_name=f"{file_obj.file_name}.evalhook",
                blob_type="evalhook-output",
                share_3rd_party=False,
                parent=file_obj,
            )

            db.session.commit()

            if hasattr(g, "scheduled_hooks"):
                g.scheduled_hooks = []

            click.echo(
                f"[{i}/{total}] Evalhook {file_obj.file_name} "
                f"({len(evalhook_output)} bytes decoded)"
            )
            processed += 1

        except Exception as e:
            db.session.rollback()
            click.echo(
                f"[{i}/{total}] ERROR {file_obj.file_name}: {e}",
                err=True,
            )
            errors += 1
            if errors >= ERROR_THRESHOLD:
                click.echo(
                    f"Error threshold ({ERROR_THRESHOLD}) reached, aborting.",
                    err=True,
                )
                break

    elapsed = time.time() - start_time
    click.echo("\nSummary:")
    click.echo(f"  Evalhook captured: {processed}")
    click.echo(f"  Already done: {already_done}")
    click.echo(f"  No output: {skipped_no_result}")
    click.echo(f"  Errors: {errors}")
    click.echo(f"  Wall-clock: {elapsed:.0f}s")
