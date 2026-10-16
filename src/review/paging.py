"""Keyset paging shared by the worklist and the audit log: newest first, by row id.

A page holds the rows with an id below `before` (all rows when `before` is None). The next
page starts before the last id of a full page. Unlike offset paging, a row added while a
person pages never shifts the rows they have not seen yet.
"""

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def check_page_size(limit: int) -> None:
    """Raise ValueError unless `limit` is between 1 and `MAX_PAGE_SIZE`."""
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}, got {limit}")


def next_before(ids: list[int], limit: int) -> int | None:
    """Return the `before` of the next page, or None when this page was the last one."""
    return ids[-1] if len(ids) == limit else None
