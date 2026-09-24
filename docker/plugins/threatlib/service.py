"""All writes to the threatlib tables, with the sample mirror kept in step.

Used by the REST resources and by the sync. Functions take `commit=True`
by default; the sync passes commit=False and commits per file itself.
"""

from __future__ import annotations

import posixpath

from sqlalchemy import func

from mwdb.model import db

from .mirror import apply_mirror, remove_mirror
from .model import Threat, ThreatSample, utcnow
from .validation import (
    ValidationError,
    validate_category,
    validate_name,
    validate_rel_path,
)


class NameConflict(Exception):
    pass


class PathConflict(Exception):
    pass


def _default_load_file(object_id: int):
    from mwdb.model import File

    return db.session.get(File, object_id)


# Monkeypatched by tests and by the sync (store.load).
_load_file = _default_load_file


def get_threat(name: str) -> Threat | None:
    return db.session.query(Threat).filter(Threat.name == name).first()


def create_threat(
    name, category, readme=None, created_by=None, *, flat=False, commit=True
) -> Threat:
    name = validate_name(name)
    category = validate_category(category)
    if get_threat(name) is not None:
        raise NameConflict(name)
    now = utcnow()
    threat = Threat(
        name=name,
        category=category,
        readme=readme,
        flat=flat,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.session.add(threat)
    db.session.flush()
    if commit:
        db.session.commit()
    return threat


def list_threats(query=None, category=None, page=1, per_page=50):
    counts = (
        db.session.query(
            ThreatSample.threat_id.label("threat_id"),
            func.count().label("n"),
        )
        .group_by(ThreatSample.threat_id)
        .subquery()
    )
    q = db.session.query(Threat, func.coalesce(counts.c.n, 0)).outerjoin(
        counts, counts.c.threat_id == Threat.id
    )
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        q = q.filter(Threat.name.like(escaped + "%", escape="\\"))
    if category:
        q = q.filter(Threat.category == category)
    total = q.count()
    rows = q.order_by(Threat.name).offset((page - 1) * per_page).limit(per_page).all()
    return [(t, int(n)) for t, n in rows], total


def sample_count(threat: Threat) -> int:
    return (
        db.session.query(func.count())
        .select_from(ThreatSample)
        .filter(ThreatSample.threat_id == threat.id)
        .scalar()
    )


def links_for_object(object_id: int) -> list[tuple[str, str]]:
    rows = (
        db.session.query(Threat.category, Threat.name)
        .join(ThreatSample, ThreatSample.threat_id == Threat.id)
        .filter(ThreatSample.object_id == object_id)
        .order_by(Threat.name)
        .all()
    )
    return [(c, n) for c, n in rows]


def _check_layout(threat: Threat, rel_path: str) -> None:
    """Flat threats are one file `<category>/<name><ext>`; a directory
    threat's `README.md` is its README, never a sample."""
    if threat.flat:
        if "/" in rel_path or posixpath.splitext(rel_path)[0] != threat.name:
            raise ValidationError(
                f"a flat threat's only sample must be named {threat.name}<ext>"
            )
    elif rel_path == "README.md":
        raise ValidationError("README.md is the threat README, not a sample path")


def link_sample(threat: Threat, file_obj, rel_path, *, commit=True):
    rel_path = validate_rel_path(rel_path)
    _check_layout(threat, rel_path)
    object_id = None if file_obj is None else file_obj.id
    existing = db.session.get(ThreatSample, (threat.id, rel_path))
    if existing is not None:
        if existing.object_id == object_id:
            return existing, False
        raise PathConflict(rel_path)
    if threat.flat and sample_count(threat) > 0:
        raise ValidationError("a flat threat has exactly one sample")
    now = utcnow()
    link = ThreatSample(
        threat_id=threat.id, rel_path=rel_path, object_id=object_id, added_at=now
    )
    db.session.add(link)
    apply_mirror(file_obj, threat.category, threat.name)
    threat.updated_at = now
    db.session.flush()
    if commit:
        db.session.commit()
    return link, True


def _unlink(threat: Threat, link: ThreatSample) -> None:
    object_id = link.object_id
    db.session.delete(link)
    db.session.flush()
    if object_id is not None:
        remaining = links_for_object(object_id)
        remove_mirror(_load_file(object_id), threat.category, threat.name, remaining)
    threat.updated_at = utcnow()


def unlink_sample(threat: Threat, rel_path: str, *, commit=True) -> bool:
    link = db.session.get(ThreatSample, (threat.id, rel_path))
    if link is None:
        return False
    _unlink(threat, link)
    if commit:
        db.session.commit()
    return True


def set_category(threat: Threat, category: str, *, commit=True) -> None:
    category = validate_category(category)
    old = threat.category
    if category == old:
        return
    if threat.flat:
        raise ValidationError("flat threats cannot change category")
    object_ids = sorted(
        {link.object_id for link in threat.samples if link.object_id is not None}
    )
    threat.category = category
    db.session.flush()
    for object_id in object_ids:
        file_obj = _load_file(object_id)
        if file_obj is None:
            continue
        file_obj.add_tag(category, commit=False)
        others = [(c, n) for c, n in links_for_object(object_id) if n != threat.name]
        if not any(c == old for c, _ in others):
            file_obj.remove_tag(old, commit=False)
    threat.updated_at = utcnow()
    if commit:
        db.session.commit()


def set_readme(threat: Threat, readme, *, commit=True) -> None:
    threat.readme = readme
    threat.updated_at = utcnow()
    if commit:
        db.session.commit()


def delete_threat(threat: Threat, *, commit=True) -> None:
    for link in list(threat.samples):
        _unlink(threat, link)
    # `_unlink` deleted each link row and (via remove_mirror -> Object.remove_attribute)
    # may have already committed mid-loop, which can leave the in-memory `samples`
    # collection holding now-stale/expired instances. Refresh it before deleting the
    # threat so SQLAlchemy's delete-orphan cascade doesn't re-touch already-deleted rows.
    db.session.expire(threat, ["samples"])
    db.session.delete(threat)
    if commit:
        db.session.commit()
