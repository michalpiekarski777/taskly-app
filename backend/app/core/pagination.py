from dataclasses import dataclass

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True)
class Page:
    offset: int
    limit: int


def page_from_query(offset: int | None = None, limit: int | None = None) -> Page:
    """Normalise offset/limit query parameters into a bounded page."""
    offset = max(offset or 0, 0)
    limit = DEFAULT_LIMIT if limit is None else min(max(limit, 1), MAX_LIMIT)
    return Page(offset=offset, limit=limit)
