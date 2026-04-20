import io
import os
import time

import click
import requests
from flask import g
from flask.cli import with_appcontext

from mwdb.core.tlsh import calc_tlsh

PHP_EXTENSIONS = {".php", ".phtml", ".php5", ".php7", ".inc"}
ERROR_THRESHOLD = 100


def _is_php_file(file_name):
    if not file_name:
        return False
    _, ext = os.path.splitext(file_name.lower())
    return ext in PHP_EXTENSIONS


def _normalize_via_sandbox(sandbox_url, php_code):
    try:
        r = requests.post(
            f"{sandbox_url}/var_deobfuscate_web.php",
            data={"phpCode": php_code},
            timeout=60,
        )
        if r.ok:
            data = r.json()
            if data.get("success") and data.get("deobfuscated_code"):
                return data["deobfuscated_code"]
    except (requests.RequestException, ValueError):
        pass

    try:
        r = requests.post(
            f"{sandbox_url}/beautify.php",
            data={"phpCode": php_code},
            timeout=60,
        )
        if r.ok:
            data = r.json()
            if data.get("success") and data.get("beautified_code"):
                return data["beautified_code"]
    except (requests.RequestException, ValueError):
        pass

    return None


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
        description="TLSH hash of deobfuscated/normalized PHP code",
        url_template="",
        rich_template="",
        example_value="",
    )
    db.session.add(defn)
    db.session.commit()
    click.echo(f"Created attribute definition: {key}")


@click.command("normalize-php")
@with_appcontext
@click.argument("sandbox_url")
def normalize_php(sandbox_url):
    """Batch-normalize PHP samples via the deobfuscation sandbox."""
    from mwdb.model import db
    from mwdb.model.attribute import Attribute
    from mwdb.model.blob import TextBlob
    from mwdb.model.file import File

    g.auth_user = None

    _ensure_attribute_definition("normalized_tlsh")

    sandbox_url = sandbox_url.rstrip("/")

    click.echo("Querying PHP files...")
    all_files = db.session.query(File).all()
    php_files = [f for f in all_files if _is_php_file(f.file_name)]
    click.echo(f"Found {len(php_files)} PHP files (of {len(all_files)} total).")

    already_done = 0
    to_process = []
    for f in php_files:
        has_attr = (
            db.session.query(Attribute)
            .filter(Attribute.object_id == f.id, Attribute.key == "normalized_tlsh")
            .first()
        )
        if has_attr:
            already_done += 1
        else:
            to_process.append(f)

    click.echo(f"  Already normalized: {already_done}, to process: {len(to_process)}")

    if not to_process:
        click.echo("Nothing to process.")
        return

    processed = 0
    skipped_no_result = 0
    errors = 0
    start_time = time.time()
    total = len(to_process)

    for i, file_obj in enumerate(to_process, 1):
        try:
            fh = file_obj.open()
            try:
                php_code = fh.read().decode("utf-8", errors="replace")
            finally:
                file_obj.close(fh)

            normalized = _normalize_via_sandbox(sandbox_url, php_code)
            if not normalized:
                click.echo(
                    f"[{i}/{total}] Skipped (sandbox returned no result) "
                    f"{file_obj.file_name}"
                )
                skipped_no_result += 1
                continue

            blob_obj, _ = TextBlob.get_or_create(
                content=normalized,
                blob_name=f"{file_obj.file_name}.normalized",
                blob_type="normalized-php",
                share_3rd_party=False,
                parent=file_obj,
            )

            tlsh_hash = calc_tlsh(io.BytesIO(normalized.encode("utf-8")))

            if tlsh_hash:
                file_obj.add_attribute(
                    "normalized_tlsh",
                    tlsh_hash,
                    commit=False,
                    check_permissions=False,
                )

            db.session.commit()

            if hasattr(g, "scheduled_hooks"):
                g.scheduled_hooks = []

            tlsh_display = tlsh_hash or "n/a"
            click.echo(
                f"[{i}/{total}] Normalized {file_obj.file_name} "
                f"(normalized_tlsh: {tlsh_display})"
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
    click.echo(f"  Normalized: {processed}")
    click.echo(f"  Already done (skipped): {already_done}")
    click.echo(f"  Sandbox returned no result: {skipped_no_result}")
    click.echo(f"  Errors: {errors}")
    click.echo(f"  Wall-clock: {elapsed:.0f}s")
