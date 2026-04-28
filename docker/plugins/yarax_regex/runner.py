"""Pure-Python wrapper around yara-x for the regex panel.

Synthesizes a single-pattern YARA rule from the user's regex, compiles it,
scans the sample bytes, and normalizes the result into a JSON-friendly dict.
No Flask, no DB — testable in isolation.
"""
import time
from typing import Any

import yara_x

# Maximum wall-clock time for a single scan. The 200 ms client-side debounce
# already protects against keystroke storms; this guards against pathological
# inputs that survive yara-x's compile-time complexity caps.
SCAN_TIMEOUT_SECONDS = 5

# Bound the number of matches returned per pattern. Web samples are small,
# but a regex like `.` against a 5 MB file would produce 5 million matches —
# we want to fail fast, not OOM the response.
MAX_MATCHES_PER_PATTERN = 1000

_RULE_TEMPLATE = "rule _r { strings: $a = /%s/ condition: any of them }"


def _escape_regex_for_template(regex: str) -> str:
    """Escape characters that would break out of the /.../ regex literal.

    The only structural concern is an unescaped `/` inside the regex literal.
    `\\` is part of regex syntax and must NOT be escaped further.
    """
    return regex.replace("/", r"\/")


def _byte_offset_to_line_column(sample_bytes: bytes, offset: int) -> tuple[int, int]:
    """Convert a byte offset into (line, column), both 1-based.

    Newlines are LF (b'\\n'). For samples with CRLF, the column counts the
    CR as a regular byte; this matches how editors display PHP/JS sources.
    """
    assert offset >= 0, f"byte offset must be non-negative, got {offset}"
    if offset == 0:
        return 1, 1
    prefix = sample_bytes[:offset]
    last_nl = prefix.rfind(b"\n")
    line = prefix.count(b"\n") + 1
    column = offset - (last_nl + 1) + 1
    return line, column


def _decode_match_text(sample_bytes: bytes, offset: int, length: int) -> str:
    """Decode the matched span as UTF-8 with replacement; never raises."""
    return sample_bytes[offset : offset + length].decode("utf-8", errors="replace")


def _diagnostic_from_compile_error(exc: yara_x.CompileError) -> dict[str, Any]:
    """Convert a yara-x CompileError into our diagnostic shape.

    yara-x errors look like:
        error[E014]: invalid regular expression
         --> line:1:25
          |
        1 | rule x { strings: $a = /[/ condition: $a }
          |                         ^ unclosed character class

    We surface the full formatted message in `message` and leave parsing
    of line/column to v1 if needed (the synthetic rule's positions are
    not directly useful to the user anyway — they include our wrapping).
    """
    return {
        "severity": "error",
        "code": "compile_error",
        "message": str(exc),
    }


def _diagnostic_from_warning(text: str) -> dict[str, Any]:
    return {
        "severity": "warning",
        "code": "yarax_warning",
        "message": text,
    }


def run(regex: str, sample_bytes: bytes) -> dict[str, Any]:
    """Compile a synthetic rule wrapping `regex`, scan `sample_bytes`,
    return a JSON-serializable result dict.

    Empty regex is treated as a compile error (the resource layer rejects
    empty regex with HTTP 400 before reaching here; this is defense in depth).
    """
    # `elapsed_ms` is included on the success path and on scan_timeout (where
    # it represents the bound wall-clock spent scanning), but is omitted on
    # compile_error responses where no scan was attempted. Frontend must guard
    # against its absence on non-ok statuses.
    if regex == "":
        return {
            "status": "compile_error",
            "diagnostics": [
                {
                    "severity": "error",
                    "code": "empty_regex",
                    "message": "regex must not be empty",
                }
            ],
        }

    started = time.perf_counter()

    source = _RULE_TEMPLATE % _escape_regex_for_template(regex)
    compiler = yara_x.Compiler()
    try:
        compiler.add_source(source)
    except yara_x.CompileError as exc:
        return {
            "status": "compile_error",
            "diagnostics": [_diagnostic_from_compile_error(exc)],
        }

    rules = compiler.build()
    # Warnings are surfaced by add_source/build, so we read them after build.
    # Verified working on yara-x 1.15.0; if a future yara-x version invalidates
    # the compiler post-build, capture warnings before calling build() instead.
    warnings = list(compiler.warnings())

    scanner = yara_x.Scanner(rules)
    scanner.set_timeout(SCAN_TIMEOUT_SECONDS)
    scanner.max_matches_per_pattern(MAX_MATCHES_PER_PATTERN)

    try:
        scan_results = scanner.scan(sample_bytes)
    except yara_x.TimeoutError:
        return {
            "status": "scan_timeout",
            "diagnostics": [
                {
                    "severity": "error",
                    "code": "scan_timeout",
                    "message": (
                        f"scan exceeded {SCAN_TIMEOUT_SECONDS}s; the regex is "
                        "likely catastrophically backtracking — consider "
                        "anchoring or restricting quantifiers"
                    ),
                }
            ],
        }

    # Other scanner exceptions are intentionally NOT caught here. The resource
    # layer surfaces them as HTTP 500 with the full traceback to the log,
    # which is correct: an unexpected yara-x failure means the engine is in a
    # state we can't reason about, and silently returning an empty match list
    # would mask real bugs.
    matches: list[dict[str, Any]] = []
    for matched_rule in scan_results.matching_rules:
        for pattern in matched_rule.patterns:
            for match in pattern.matches:
                line, column = _byte_offset_to_line_column(sample_bytes, match.offset)
                matches.append(
                    {
                        "offset": match.offset,
                        "length": match.length,
                        "line": line,
                        "column": column,
                        "text": _decode_match_text(
                            sample_bytes, match.offset, match.length
                        ),
                    }
                )

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    return {
        "status": "ok",
        "matches": matches,
        "diagnostics": [_diagnostic_from_warning(w) for w in warnings],
        "atoms": [],  # yara-x Python binding does not expose atoms in v1.15.0
        "elapsed_ms": elapsed_ms,
    }
