"""Post-commit hooks enforcing the `context` and `source:` contracts."""

from flask import g

from mwdb.core.hooks import HookHandler
from mwdb.model import db

from . import logger, reanalysis
from .attributes import ANALYSIS_KEY
from .context_policy import filter_context, source_tag_allowed

CONTEXT_STRIPPED_TAG = "wm:context-stripped"
SOURCE_STRIPPED_TAG = "wm:source-stripped"


class MalleusHookHandler(HookHandler):
    # Set by `entrypoint` from MWDB_MALLEUS_REPORTER_LOGINS; MWDB instantiates
    # hook handlers without arguments, so this is a class attribute.
    reporter_logins = ("karton",)

    def on_created_attribute(self, object, attribute):
        if attribute.key == "context":
            self._strip_context(object, attribute)
        elif attribute.key == ANALYSIS_KEY:
            self._replace_previous_analysis(object, attribute)

    def _strip_context(self, object, attribute):
        try:
            kept, stripped = filter_context(attribute.value)
            if not stripped:
                return
            # Add the cleaned value first so a failure never loses the context.
            object.add_attribute("context", kept, commit=False, check_permissions=False)
            object.remove_attribute("context", attribute.value, check_permissions=False)
            object.add_tag(CONTEXT_STRIPPED_TAG, commit=False)
            db.session.commit()
            # Key names only, never values.
            logger.info("malleus: stripped context keys %s", ", ".join(stripped))
        except Exception:
            logger.exception("malleus: failed to strip context")
            db.session.rollback()

    def _replace_previous_analysis(self, object, attribute):
        login = getattr(getattr(g, "auth_user", None), "login", None)
        if login is None or login not in self.reporter_logins:
            return
        try:
            removed = reanalysis.clear_previous_analysis(object, attribute)
            logger.info(
                "malleus: new analysis from %s, removed %d previous attribute values",
                login,
                removed,
            )
        except Exception:
            logger.exception("malleus: failed to clear the previous analysis")
            db.session.rollback()

    def on_created_tag(self, object, tag):
        login = getattr(getattr(g, "auth_user", None), "login", None)
        if login is None:
            return
        try:
            if not source_tag_allowed(tag.tag, login, self.reporter_logins):
                object.remove_tag(tag.tag, commit=False)
                object.add_tag(SOURCE_STRIPPED_TAG, commit=False)
                db.session.commit()
                logger.info("malleus: removed source tag set by %s", login)
                return
            if login in self.reporter_logins and tag.tag.startswith(
                reanalysis.VERDICT_PREFIX
            ):
                removed = reanalysis.clear_previous_verdict(object, tag.tag)
                db.session.commit()
                logger.info(
                    "malleus: new %s from %s, removed %d previous tags",
                    tag.tag,
                    login,
                    len(removed),
                )
        except Exception:
            logger.exception("malleus: failed to handle created tag")
            db.session.rollback()
