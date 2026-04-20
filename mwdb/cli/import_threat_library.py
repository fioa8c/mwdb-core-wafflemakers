import os
import time

import click
from flask import g
from flask.cli import with_appcontext

from mwdb.core.config import app_config

SCOPED_DIRS = {
    "threats": "nested",
    "for-later-review": "nested",
    "webshells": "flat",
    "escalated_issues_samples": "nested",
}

ERROR_THRESHOLD = 100


def _is_sample_file(filename):
    return filename != "README.md" and not filename.startswith(".")


def _discover_samples(base_path):
    for dirname, layout in SCOPED_DIRS.items():
        dir_path = os.path.join(base_path, dirname)
        if not os.path.isdir(dir_path):
            click.echo(
                f"Warning: scoped directory {dirname}/ not found, skipping", err=True
            )
            continue

        if layout == "nested":
            for subdir in sorted(os.listdir(dir_path)):
                subdir_path = os.path.join(dir_path, subdir)
                if not os.path.isdir(subdir_path):
                    continue
                readme_path = os.path.join(subdir_path, "README.md")
                readme = readme_path if os.path.isfile(readme_path) else None
                has_samples = False
                for fname in sorted(os.listdir(subdir_path)):
                    fpath = os.path.join(subdir_path, fname)
                    if os.path.isfile(fpath) and _is_sample_file(fname):
                        has_samples = True
                        yield fpath, dirname, subdir, readme
                if not has_samples and readme:
                    click.echo(
                        f"  (no sample files in {dirname}/{subdir}/, skipping)",
                        err=True,
                    )

        elif layout == "flat":
            for fname in sorted(os.listdir(dir_path)):
                fpath = os.path.join(dir_path, fname)
                if os.path.isfile(fpath) and _is_sample_file(fname):
                    yield fpath, dirname, None, None


def _ensure_attribute_definition(key):
    from mwdb.model import db
    from mwdb.model.attribute import AttributeDefinition

    existing = (
        db.session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == key)
        .first()
    )
    if existing:
        return
    defn = AttributeDefinition(
        key=key,
        label=key,
        description="Threat name imported from jetpack-threat-library",
        url_template="",
        rich_template="",
        example_value="",
    )
    db.session.add(defn)
    db.session.commit()
    click.echo(f"Created attribute definition: {key}")


@click.command("import-jetpack-threat-library")
@with_appcontext
@click.argument("path", type=click.Path(exists=True, file_okay=False))
def import_jetpack_threat_library(path):
    """One-shot import of jetpack-threat-library samples into MWDB."""
    from mwdb.model import User, db
    from mwdb.model.comment import Comment
    from mwdb.model.file import EmptyFileError, File

    admin = (
        db.session.query(User).filter(User.login == app_config.mwdb.admin_login).first()
    )
    if not admin:
        raise click.ClickException(
            f"Admin user '{app_config.mwdb.admin_login}' not found"
        )

    g.auth_user = None

    _ensure_attribute_definition("jpop_threat_name")

    click.echo("Discovering samples...")
    samples = list(_discover_samples(path))
    total = len(samples)
    click.echo(f"Found {total} sample files.")

    if total == 0:
        click.echo("Nothing to import.")
        return

    imported = 0
    skipped = 0
    errors = 0
    start_time = time.time()

    for i, (file_path, tag_name, subdir_name, readme_path) in enumerate(samples, 1):
        rel_path = os.path.relpath(file_path, path)
        try:
            with open(file_path, "rb") as f:
                file_obj, is_new = File.get_or_create(
                    file_name=os.path.basename(file_path),
                    file_stream=f,
                    share_3rd_party=False,
                )

            file_obj.add_tag(tag_name, commit=False)

            if subdir_name:
                file_obj.add_attribute(
                    "jpop_threat_name",
                    subdir_name,
                    commit=False,
                    check_permissions=False,
                )

            if readme_path:
                readme_content = open(
                    readme_path, "r", encoding="utf-8", errors="replace"
                ).read()
                existing_comments = [c.comment for c in file_obj.comments]
                if readme_content not in existing_comments:
                    comment = Comment(
                        comment=readme_content,
                        user_id=admin.id,
                        object_id=file_obj.id,
                    )
                    db.session.add(comment)

            db.session.commit()
            file_obj.release_after_upload()

            if hasattr(g, "scheduled_hooks"):
                g.scheduled_hooks = []

            if is_new:
                sha = file_obj.sha256[:16]
                tlsh_val = file_obj.tlsh or "n/a"
                click.echo(
                    f"[{i}/{total}] Imported {rel_path} (sha256: {sha}... tlsh: {tlsh_val})"
                )
                imported += 1
            else:
                click.echo(f"[{i}/{total}] Skipped (duplicate) {rel_path}")
                skipped += 1

        except EmptyFileError:
            click.echo(f"[{i}/{total}] Skipped (empty) {rel_path}")
            skipped += 1
        except Exception as e:
            db.session.rollback()
            click.echo(f"[{i}/{total}] ERROR {rel_path}: {e}", err=True)
            errors += 1
            if errors >= ERROR_THRESHOLD:
                click.echo(
                    f"Error threshold ({ERROR_THRESHOLD}) reached, aborting.",
                    err=True,
                )
                break

    elapsed = time.time() - start_time
    click.echo("\nSummary:")
    click.echo(f"  Imported: {imported}")
    click.echo(f"  Skipped: {skipped}")
    click.echo(f"  Errors: {errors}")
    click.echo(f"  Wall-clock: {elapsed:.0f}s")
