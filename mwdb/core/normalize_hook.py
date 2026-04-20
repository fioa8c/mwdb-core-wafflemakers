import io
import os

from mwdb.core.hooks import HookHandler
from mwdb.core.log import getLogger
from mwdb.core.normalize import normalize_via_sandbox
from mwdb.core.tlsh import calc_tlsh

logger = getLogger()

PHP_EXTENSIONS = {".php", ".phtml", ".php5", ".php7", ".inc"}


class NormalizePhpHookHandler(HookHandler):
    def on_created_file(self, file):
        sandbox_url = os.environ.get("MWDB_SANDBOX_URL")
        if not sandbox_url:
            return

        _, ext = os.path.splitext((file.file_name or "").lower())
        if ext not in PHP_EXTENSIONS:
            return

        from mwdb.model import db
        from mwdb.model.blob import TextBlob

        try:
            fh = file.open()
            try:
                php_code = fh.read().decode("utf-8", errors="replace")
            finally:
                file.close(fh)

            normalized = normalize_via_sandbox(sandbox_url.rstrip("/"), php_code)
            if not normalized:
                return

            TextBlob.get_or_create(
                content=normalized,
                blob_name=f"{file.file_name}.normalized",
                blob_type="normalized-php",
                share_3rd_party=False,
                parent=file,
            )

            tlsh_hash = calc_tlsh(io.BytesIO(normalized.encode("utf-8")))
            if tlsh_hash:
                file.add_attribute(
                    "normalized_tlsh",
                    tlsh_hash,
                    commit=False,
                    check_permissions=False,
                )

            db.session.commit()
            logger.info(
                "Auto-normalized %s (tlsh: %s)",
                file.file_name,
                tlsh_hash or "n/a",
            )
        except Exception:
            logger.exception("Failed to auto-normalize %s", file.file_name)
            db.session.rollback()
