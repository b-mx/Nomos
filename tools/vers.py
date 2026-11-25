"""Nomos's reading of ``vers:generic/`` ranges.

The vers specification leaves the ordering of ``generic`` versions open, so
Nomos defines it here; ``CONTRIBUTING.md`` ("Licences") documents the same
rule. The shared test vectors live in ``tests/fixtures/vers_generic_vectors.json``.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PREFIX = "vers:generic/"

# A version key sorts like the version; equal keys are equal versions.
VersionKey = tuple[tuple[int, ...], tuple[Any, ...]]

_ALLOWED_CHARS = re.compile(r"[A-Za-z0-9.~+-]+")
_RELEASE = re.compile(r"\d+(?:\.\d+)*")
_PRE_RELEASE = re.compile(r"[-~.]?(?:(dev|alpha|beta|pre|preview|rc)|([ab])(?=\d))([0-9.]*)")
_PRE_RANK = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "pre": 3, "preview": 3, "rc": 3}
_RUN = re.compile(r"[0-9]+|[A-Za-z]+")
_OPERATORS = ("<=", ">=", "!=", "<", ">")


def _trim(numbers: tuple[int, ...]) -> tuple[int, ...]:
    """Drop trailing zeros: a missing number counts as 0."""
    end = len(numbers)
    while end and numbers[end - 1] == 0:
        end -= 1
    return numbers[:end]


def _qualifier_key(qualifier: str) -> tuple[Any, ...]:
    if not qualifier:
        return (1,)
    q = qualifier.lower()
    pre = _PRE_RELEASE.fullmatch(q)
    if pre:
        numbers = tuple(int(n) for n in pre.group(3).split(".") if n)
        return (0, _PRE_RANK[pre.group(1) or pre.group(2)], _trim(numbers))
    # Post-release: runs of digits and letters, separators ignored. A letter
    # run (0, ...) sorts before a digit run (1, ...); a prefix sorts first.
    return (2, tuple((1, int(r)) if r.isdigit() else (0, r) for r in _RUN.findall(q)))


def parse_version(text: str) -> VersionKey | None:
    """Return the sort key of a version, or None when ``text`` is not one."""
    if not _ALLOWED_CHARS.fullmatch(text) or not text[0].isdigit():
        return None
    components = text.split(".")
    if "" in components or "x" in components or "X" in components:
        return None
    release = _RELEASE.match(text)
    assert release  # text starts with a digit
    rest = text[release.end() :]
    starts_qualifier = (
        rest == ""
        or rest[0] in "-~+"
        or rest[0].isalpha()
        or (rest[0] == "." and rest[1:2].isalpha())
    )
    # A trailing separator ("1.0-", "1.0+") is not a version.
    if not starts_qualifier or (rest and not rest[-1].isalnum()):
        return None
    numbers = tuple(int(n) for n in release.group().split("."))
    return (_trim(numbers), _qualifier_key(rest))


def _key(text: str) -> VersionKey:
    key = parse_version(text)
    if key is None:
        raise ValueError(f"{text!r} is not a version")
    return key


def compare(a: str, b: str) -> int:
    ka, kb = _key(a), _key(b)
    return (ka > kb) - (ka < kb)


@dataclass(frozen=True)
class Constraint:
    op: str  # "=", "!=", "<", "<=", ">", ">="
    key: VersionKey


def parse_range(text: str) -> list[Constraint]:
    """Parse and validate a ``vers:generic/`` range; raise ValueError if invalid."""
    if not text.startswith(PREFIX):
        raise ValueError(f"only {PREFIX} ranges are supported")
    constraints: list[Constraint] = []
    for part in text[len(PREFIX) :].split("|"):
        op = next((o for o in _OPERATORS if part.startswith(o)), "=")
        version = part[len(op) :] if op != "=" else part
        constraints.append(Constraint(op, _key(version)))
    keys = [c.key for c in constraints]
    if any(a >= b for a, b in zip(keys, keys[1:], strict=False)):
        raise ValueError("constraints must be in strictly ascending version order")
    bounds = [c.op for c in constraints if c.op != "!="]
    for cur, nxt in zip(bounds, bounds[1:], strict=False):
        allowed = ("<", "<=") if cur in (">", ">=") else ("=", ">", ">=")
        if nxt not in allowed:
            raise ValueError(f"constraint {nxt!r} cannot follow {cur!r}")
    return constraints


def _contains(constraints: list[Constraint], sign: Callable[[VersionKey], int]) -> bool:
    """The vers containment algorithm. ``sign(k)`` compares the tested version to k."""
    for c in constraints:
        if c.op in ("=", "<=", ">=") and sign(c.key) == 0:
            return True
        if c.op == "!=" and sign(c.key) == 0:
            return False
    bounds = [c for c in constraints if c.op not in ("=", "!=")]
    if not bounds:
        return all(c.op == "!=" for c in constraints)
    for i, c in enumerate(bounds):
        if c.op in ("<", "<=") and i == 0 and sign(c.key) < 0:
            return True
        if c.op in (">", ">=") and sign(c.key) > 0:
            if i + 1 == len(bounds) or sign(bounds[i + 1].key) < 0:
                return True
    return False


def contains(versions: str, version: str) -> bool:
    key = _key(version)
    return _contains(parse_range(versions), lambda k: (key > k) - (key < k))


def overlaps(a: str, b: str) -> bool:
    """True when some version lies in both ranges.

    Versions are dense, so it is enough to test every boundary version and one
    point inside each gap between boundaries (position 2i+1 is boundary i,
    position 2i the gap below it).
    """
    ca, cb = parse_range(a), parse_range(b)
    bounds = sorted({c.key for c in ca + cb})
    for point in range(2 * len(bounds) + 1):

        def sign(k: VersionKey, point: int = point) -> int:
            position = 2 * bisect_left(bounds, k) + 1
            return (point > position) - (point < position)

        if _contains(ca, sign) and _contains(cb, sign):
            return True
    return False
