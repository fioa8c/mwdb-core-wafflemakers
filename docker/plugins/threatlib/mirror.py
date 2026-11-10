"""Keeps the category tag and jpop_threat_name attribute on samples in step
with the threatlib tables, so upstream search keeps working."""

from __future__ import annotations

from .attributes import ATTRIBUTE_KEY


def apply_mirror(file_obj, category: str, name: str) -> None:
    if file_obj is None:
        return
    file_obj.add_tag(category, commit=False)
    file_obj.add_attribute(ATTRIBUTE_KEY, name, commit=False, check_permissions=False)


def remove_mirror(
    file_obj, category: str, name: str, remaining_links: list[tuple[str, str]]
) -> None:
    if file_obj is None:
        return
    if not any(c == category for c, _ in remaining_links):
        file_obj.remove_tag(category, commit=False)
    if not any(n == name for _, n in remaining_links):
        file_obj.remove_attribute(ATTRIBUTE_KEY, name, check_permissions=False)
