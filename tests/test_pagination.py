"""Pagination behavior over a fake fetch — the natural seam for paginate/iterate."""

from chanfig import NestedDict

from feishu.pagination import iterate, paginate


def pages(*chunks):
    """Build a fake ``fetch(page_token)`` serving ``(items, has_more)`` per page."""
    seq = list(chunks)

    async def fetch(page_token):
        idx = 0 if page_token is None else int(page_token)
        items, has_more = seq[idx]
        return NestedDict({"data": {"items": items, "has_more": has_more, "page_token": str(idx + 1)}})

    return fetch


def looping(page, *, limit=5):
    """Build a ``fetch`` that always returns ``page`` but raises past ``limit`` calls.

    Used to pin down the infinite-loop guard: the paginator stops itself via the guard
    long before ``limit``, so a regression that keeps re-fetching trips the bound and
    fails fast instead of hanging the suite (the loop never awaits the event loop, so a
    timeout-based guard could never fire).
    """
    calls = {"n": 0}

    async def fetch(page_token):
        calls["n"] += 1
        if calls["n"] > limit:
            raise AssertionError(f"paginator re-fetched past {limit} pages without advancing the token")
        return NestedDict(page)

    return fetch


class TestPaginate:
    async def test_walks_all_pages(self):
        fetch = pages(([1, 2], True), ([3], False))
        assert await paginate(fetch) == [1, 2, 3]

    async def test_max_items_caps(self):
        fetch = pages(([1, 2], True), ([3, 4], True))
        assert await paginate(fetch, max_items=3) == [1, 2, 3]

    async def test_early_stop_halts(self):
        fetch = pages(([1, 2, 3], True))
        assert await paginate(fetch, early_stop=lambda acc, item: item == 3) == [1, 2]

    async def test_empty_first_page_yields_nothing(self):
        fetch = pages(([], False))
        assert await paginate(fetch) == []

    async def test_empty_page_terminates(self):
        # Empty first page that still claims has_more without advancing the token. Without
        # the falsy-token guard this re-fetches the same empty page forever; the guard
        # stops after the first fetch, yielding nothing.
        fetch = looping({"data": {"items": [], "has_more": True, "page_token": None}})
        assert await paginate(fetch) == []

    async def test_falsy_token_terminates(self):
        # has_more is True but every page carries a falsy page_token, and the fetch never
        # signals has_more=False -- so termination must come from the falsy-token guard.
        # ``looping`` raises if the paginator over-fetches, so a regression fails fast.
        fetch = looping({"data": {"items": [1, 2], "has_more": True, "page_token": None}})
        assert await paginate(fetch) == [1, 2]

    async def test_unchanged_token_terminates(self):
        # has_more stays True and page_token never advances (always "same"). Without the
        # equals-previous guard this re-fetches the identical page forever; the guard
        # stops once the second fetch returns the same token as the first.
        fetch = looping({"data": {"items": [9], "has_more": True, "page_token": "same"}})
        assert await paginate(fetch) == [9, 9]


class TestIterate:
    async def test_streams_items_across_pages(self):
        fetch = pages(([1, 2], True), ([3], False))
        out = [item async for item in iterate(fetch)]
        assert out == [1, 2, 3]

    async def test_max_items_caps_the_stream(self):
        fetch = pages(([1, 2], True), ([3, 4], True))
        out = [item async for item in iterate(fetch, max_items=3)]
        assert out == [1, 2, 3]

    async def test_early_stop_sees_running_items(self):
        # early_stop receives the items yielded so far; it halts before the matching item.
        fetch = pages(([1, 2], True), ([3], False))
        out = [item async for item in iterate(fetch, early_stop=lambda acc, item: sum(acc) >= 3)]
        assert out == [1, 2]
