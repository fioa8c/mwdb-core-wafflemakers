"""
Refinements on top of libmagic's file type verdict.

libmagic keys PHP detection on the file's first bytes, so PHP embedded in
HTML (or preceded by JavaScript) comes out as "HTML document" or
"JavaScript source". A PHP open tag anywhere in the file is what makes the
PHP interpreter execute it, so that is what decides the type here.

Kept free of heavy imports (magic, ssdeep, boto3) so it is unit-testable
without the backend's native dependencies.
"""

import os

PHP_TAGS = (b"<?php", b"<?=")
CHUNK_SIZE = 1024 * 1024
# Longest tag minus one: enough to catch a tag split across two chunks.
_OVERLAP = max(len(tag) for tag in PHP_TAGS) - 1


def contains_php_tag(stream) -> bool:
    """
    True if the stream contains a PHP open tag (case-insensitive).
    The stream is left positioned at offset 0.
    """
    stream.seek(0, os.SEEK_SET)
    try:
        tail = b""
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                return False
            window = tail + chunk.lower()
            if any(tag in window for tag in PHP_TAGS):
                return True
            tail = window[-_OVERLAP:]
    finally:
        stream.seek(0, os.SEEK_SET)


def refine_file_type(stream, verdict: str) -> str:
    """
    Turn a non-PHP libmagic verdict into "PHP script, ..." when the file
    contains a PHP open tag, keeping libmagic's remaining details.
    """
    if verdict.startswith("PHP"):
        return verdict
    if not contains_php_tag(stream):
        return verdict
    _, sep, rest = verdict.partition(",")
    return "PHP script" + sep + rest
