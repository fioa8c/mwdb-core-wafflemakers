"""SQLite stand-in for mwdb.model.db plus FakeFile / FakeStore.

Imported by conftest.py after the mwdb stubs are in place, and by tests as
`from threatlib.tests.fakes import FakeFile, FakeStore`.
"""

import hashlib
import io
import sys

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker

_engine = sa.create_engine("sqlite://")
_Base = declarative_base()
_Session = scoped_session(sessionmaker(bind=_engine))


class _FakeDB:
    Model = _Base
    Column = sa.Column
    Integer = sa.Integer
    String = sa.String
    Text = sa.Text
    Boolean = sa.Boolean
    DateTime = sa.DateTime
    ForeignKey = sa.ForeignKey
    relationship = staticmethod(relationship)
    engine = _engine
    session = _Session


sys.modules["mwdb.model"].db = _FakeDB

# Minimal `object` table so FKs resolve and FakeStore can insert rows.
sa.Table(
    "object",
    _Base.metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("dhash", sa.String(64), unique=True),
)
_Base.metadata.create_all(_engine, tables=[_Base.metadata.tables["object"]])


class FakeFile:
    """Stand-in for mwdb.model.File exposing what the plugin calls."""

    def __init__(self, object_id: int, content: bytes, file_name: str):
        self.id = object_id
        self.content = content
        self.sha256 = hashlib.sha256(content).hexdigest()
        self.dhash = self.sha256
        self.file_name = file_name
        self.file_size = len(content)
        self.tags: set[str] = set()
        self.attributes: dict[str, set[str]] = {}
        self.calls: list[tuple] = []

    # --- mwdb.model.Object API used by mirror.py ---
    def get_tag(self, tag):
        return tag if tag in self.tags else None

    def add_tag(self, tag, commit=True):
        self.calls.append(("add_tag", tag))
        new = tag not in self.tags
        self.tags.add(tag)
        return new

    def remove_tag(self, tag, commit=True):
        self.calls.append(("remove_tag", tag))
        existed = tag in self.tags
        self.tags.discard(tag)
        return existed

    def add_attribute(self, key, value, commit=True, check_permissions=True):
        self.calls.append(("add_attribute", key, value))
        values = self.attributes.setdefault(key, set())
        new = value not in values
        values.add(value)
        return new

    def remove_attribute(self, key, value, check_permissions=True):
        self.calls.append(("remove_attribute", key, value))
        values = self.attributes.get(key, set())
        existed = value in values
        values.discard(value)
        return existed

    def has_explicit_access(self, user):
        return True

    def read(self):
        return self.content

    def release_after_upload(self):
        pass


class FakeStore:
    """In-memory ObjectStore (see threatlib/sync/store.py) backed by the
    SQLite `object` table so link rows get real object ids."""

    def __init__(self):
        self.by_sha: dict[str, FakeFile] = {}
        self.by_id: dict[int, FakeFile] = {}

    def get_or_create(self, file_name, stream):
        content = stream.read()
        if len(content) == 0:
            from mwdb.model.file import EmptyFileError

            raise EmptyFileError()
        sha = hashlib.sha256(content).hexdigest()
        if sha in self.by_sha:
            return self.by_sha[sha], False
        res = _Session.execute(
            sa.text("INSERT INTO object (dhash) VALUES (:d)"), {"d": sha}
        )
        _Session.commit()
        obj = FakeFile(res.lastrowid, content, file_name)
        self.by_sha[sha] = obj
        self.by_id[obj.id] = obj
        return obj, True

    def load(self, object_id):
        return self.by_id.get(object_id)

    def read(self, object_id):
        return self.by_id[object_id].content

    def add(self, content: bytes, file_name: str = "x.php") -> FakeFile:
        obj, _ = self.get_or_create(file_name, io.BytesIO(content))
        return obj
