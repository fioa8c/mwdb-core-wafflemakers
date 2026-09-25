"""Object storage abstraction so the sync can be tested without MWDB.

Production uses MwdbStore (wraps mwdb.model.File); tests use FakeStore
from tests/conftest.py.
"""

from __future__ import annotations

from typing import Any, BinaryIO, Protocol


class ObjectStore(Protocol):
    def get_or_create(self, file_name: str, stream: BinaryIO) -> tuple[Any, bool]: ...

    def load(self, object_id: int) -> Any | None: ...

    def read(self, object_id: int) -> bytes: ...


class MwdbStore:
    def __init__(self, share_group_name: str | None = "public"):
        self.share_group_name = share_group_name

    def _share_with(self):
        if not self.share_group_name:
            return None
        from mwdb.model import Group

        group = Group.get_by_name(self.share_group_name)
        return [group] if group is not None else None

    def get_or_create(self, file_name, stream):
        from mwdb.model import File, db

        file_obj, is_new = File.get_or_create(
            file_name=file_name,
            file_stream=stream,
            share_3rd_party=False,
            share_with=self._share_with(),
        )
        db.session.commit()
        file_obj.release_after_upload()
        return file_obj, is_new

    def load(self, object_id):
        from mwdb.model import File, db

        return db.session.get(File, object_id)

    def read(self, object_id):
        file_obj = self.load(object_id)
        if file_obj is None:
            raise KeyError(object_id)
        return file_obj.read()
