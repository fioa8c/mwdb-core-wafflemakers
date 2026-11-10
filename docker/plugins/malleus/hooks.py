"""Post-commit hooks enforcing the `context` and `source:` contracts."""

from flask import g

from mwdb.core.hooks import HookHandler
from mwdb.model import db

from . import logger
from .context_policy import filter_context, source_tag_allowed

CONTEXT_STRIPPED_TAG = "wm:context-stripped"
SOURCE_STRIPPED_TAG = "wm:source-stripped"


class MalleusHookHandler(HookHandler):
    def on_created_attribute(self, object, attribute):
        if attribute.key != "context":
            return
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

    def on_created_tag(self, object, tag):
        login = getattr(getattr(g, "auth_user", None), "login", None)
        if login is None:
            return
        try:
            if source_tag_allowed(tag.tag, login):
                return
            object.remove_tag(tag.tag, commit=False)
            object.add_tag(SOURCE_STRIPPED_TAG, commit=False)
            db.session.commit()
            logger.info("malleus: removed foreign source tag set by %s", login)
        except Exception:
            logger.exception("malleus: failed to strip source tag")
            db.session.rollback()
