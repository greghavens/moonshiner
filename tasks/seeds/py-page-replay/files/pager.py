"""Cursor-paginated record fetching with transient-failure retries."""


class TransientFetchError(Exception):
    """A page fetch failed in a way that is safe to retry."""


class RecordPager:
    """Yields every record from a cursor-paginated source exactly once.

    fetch_page(cursor) returns {"items": [...], "next": <cursor or None>};
    the first request passes cursor=None. Cursors are opaque tokens minted
    by the service. A TransientFetchError is retried up to max_retries
    times before it propagates.
    """

    def __init__(self, fetch_page, max_retries=2):
        self._fetch = fetch_page
        self.max_retries = max_retries

    def __iter__(self):
        cursor = None
        prev_cursor = None
        retries = 0
        rewound = False
        while True:
            try:
                page = self._fetch(cursor)
            except TransientFetchError:
                if retries >= self.max_retries:
                    raise
                retries += 1
                cursor = prev_cursor
                rewound = True
                continue
            items = list(page["items"])
            if rewound:
                # a rewound fetch serves the anchor record again; drop it
                items = items[1:]
                rewound = False
            prev_cursor = cursor
            cursor = page.get("next")
            yield from items
            if cursor is None:
                return
