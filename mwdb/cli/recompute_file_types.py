import time

import click
from flask.cli import with_appcontext

BATCH_SIZE = 200


@click.command("recompute-file-types")
@with_appcontext
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report which file types would change without writing anything.",
)
@click.option(
    "--limit",
    type=int,
    default=0,
    show_default=True,
    help="Stop after examining N files (0 = all).",
)
def recompute_file_types(dry_run, limit):
    """
    Recompute File.file_type for stored files.

    Run after deploying an image with a newer libmagic or after changing
    the file type refinement in mwdb/core/filetype.py.
    """
    from mwdb.core.util import calc_magic
    from mwdb.model import db
    from mwdb.model.file import File

    examined = 0
    changed = 0
    errors = 0
    start_time = time.time()

    # Keyset batches rather than one streaming query: committing (or rolling
    # back) mid-iteration would invalidate a server-side cursor.
    last_id = 0
    done = False
    while not done:
        batch = (
            db.session.query(File.id, File.sha256, File.file_type)
            .filter(File.id > last_id)
            .order_by(File.id)
            .limit(BATCH_SIZE)
            .all()
        )
        if not batch:
            break

        updates = {}
        for file_id, sha256, old_type in batch:
            if limit and examined >= limit:
                done = True
                break
            last_id = file_id
            examined += 1
            try:
                file_obj = db.session.get(File, file_id)
                fh = file_obj.open()
                try:
                    new_type = calc_magic(fh)
                finally:
                    File.close(fh)
            except Exception as e:
                click.echo(f"ERROR {sha256}: {e}", err=True)
                db.session.rollback()
                errors += 1
                continue
            if new_type != old_type:
                click.echo(f"{sha256}: {old_type!r} -> {new_type!r}")
                changed += 1
                updates[file_id] = new_type

        if updates and not dry_run:
            try:
                for file_id, new_type in updates.items():
                    db.session.query(File).filter(File.id == file_id).update(
                        {File.file_type: new_type}, synchronize_session=False
                    )
                db.session.commit()
            except Exception as e:
                click.echo(
                    f"ERROR committing batch ending at id {last_id}: {e}", err=True
                )
                db.session.rollback()
                changed -= len(updates)
                errors += len(updates)
        else:
            db.session.rollback()
        # Drop the batch's File objects from the identity map.
        db.session.expunge_all()
        click.echo(f"... examined {examined}, changed {changed}, errors {errors}")

    elapsed = time.time() - start_time
    click.echo("\nSummary:" + (" (dry run, nothing written)" if dry_run else ""))
    click.echo(f"  Examined: {examined}")
    click.echo(f"  Changed: {changed}")
    click.echo(f"  Errors: {errors}")
    click.echo(f"  Elapsed: {elapsed:.0f}s")
