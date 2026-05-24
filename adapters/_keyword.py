"""Shared title-matching helper for the search/discovery adapters."""
from __future__ import annotations


def title_matches(title: str, keywords: list[str] | None) -> bool:
    """Case-insensitive substring AND-match.

    Every keyword must appear (as a case-insensitive substring) in `title`.
    No keywords / empty list = match-all.
    """
    if not keywords:
        return True
    t = (title or "").lower()
    return all(k.lower() in t for k in keywords)
