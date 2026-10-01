from __future__ import annotations

import json
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from feishu.errors import FeishuError
from feishu.gateway.notifications import (
    InMemoryEventMessageStore,
    JsonFileEventMessageStore,
    _write_json,
    upsert_interactive_card,
)


@pytest.mark.asyncio
async def test_upsert_updates_card(gateway_client) -> None:
    store = InMemoryEventMessageStore()
    first_card = {"body": {"elements": [{"content": "firing"}]}}
    resolved_card = {"body": {"elements": [{"content": "resolved"}]}}

    first = await upsert_interactive_card(
        gateway_client,
        "incident:42",
        first_card,
        "oc_ops",
        store=store,
        uuid_prefix="incident-",
    )
    second = await upsert_interactive_card(
        gateway_client,
        "incident:42",
        resolved_card,
        "oc_ops",
        store=store,
        uuid_prefix="incident-",
    )

    assert first.action == "created"
    assert second.action == "updated"
    assert first.message_id == second.message_id
    assert len(gateway_client.im.send.calls) == 1
    assert len(gateway_client.im.patch.calls) == 1
    send_args, _ = gateway_client.im.send.calls[0]
    assert send_args == ("oc_ops", first_card)
    assert gateway_client.im.patch.calls[0][0][1] == resolved_card


@pytest.mark.asyncio
async def test_upsert_sends_a_new_card_when_the_previous_message_is_too_old() -> None:
    class _Patch:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            self.calls.append(args)
            raise FeishuError(230031, "Message can only be modified within 14 days after sending.")

    class _Send:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            self.calls.append((args, kwargs))
            return {"message_id": "om_new"}

    client = type("Client", (), {})()
    client.im = type("IM", (), {"patch": _Patch(), "send": _Send()})()
    store = InMemoryEventMessageStore()
    store.set("incident:42", "om_old")

    delivery = await upsert_interactive_card(
        client,
        "incident:42",
        {"body": {"elements": [{"content": "firing"}]}},
        "oc_ops",
        store=store,
    )

    assert delivery.action == "created"
    assert delivery.message_id == "om_new"
    assert store.get("incident:42") == "om_new"
    assert client.im.patch.calls
    assert client.im.send.calls


def test_file_store_persists_messages(tmp_path) -> None:
    path = tmp_path / "event-messages.json"
    store = JsonFileEventMessageStore(path)
    store.set("incident:42", "om_event")

    assert JsonFileEventMessageStore(path).get("incident:42") == "om_event"


def test_file_store_treats_blank_file_as_empty(tmp_path) -> None:
    path = tmp_path / "event-messages.json"
    path.write_text("")
    store = JsonFileEventMessageStore(path)

    assert store.get("incident:42") is None
    store.set("incident:42", "om_event")

    assert JsonFileEventMessageStore(path).get("incident:42") == "om_event"


@pytest.mark.parametrize("spelling", ("same", "relative", "symlink", "case", "case_initial", "unicode"))
def test_file_stores_preserve_concurrent_updates(tmp_path, monkeypatch, spelling: str) -> None:
    if spelling in ("case", "case_initial"):
        directory_alias = tmp_path.with_name(tmp_path.name.upper())
        if not directory_alias.exists() or not directory_alias.samefile(tmp_path):
            pytest.skip("filesystem does not support case aliases")
    path = tmp_path / ("event-messagés.json" if spelling == "unicode" else "event-messages.json")
    first = JsonFileEventMessageStore(path)
    if spelling != "case_initial":
        first.set("initial", "om_initial")
    if spelling == "relative":
        monkeypatch.chdir(tmp_path)
        other_path = Path("event-messages.json")
    elif spelling == "symlink":
        other_path = tmp_path / "alias.json"
        other_path.symlink_to(path)
    elif spelling in ("case", "case_initial"):
        other_path = path.with_name(path.name.upper())
    elif spelling == "unicode":
        other_path = path.with_name(unicodedata.normalize("NFD", path.name))
        if not other_path.exists() or not other_path.samefile(path):
            pytest.skip("filesystem does not support Unicode normalization aliases")
    else:
        other_path = path
    second = JsonFileEventMessageStore(other_path)
    first_read = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    read = first._read

    def paused_read() -> dict[str, str]:
        data = read()
        first_read.set()
        assert release_first.wait(timeout=5)
        return data

    def update_second() -> None:
        second_started.set()
        try:
            second.set("second", "om_second")
        finally:
            second_finished.set()

    monkeypatch.setattr(first, "_read", paused_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_update = pool.submit(first.set, "first", "om_first")
        try:
            assert first_read.wait(timeout=5)
            second_update = pool.submit(update_second)
            assert second_started.wait(timeout=5)
            # A store sharing the lock waits until the first update commits.
            # Independent locks let the second update commit stale state here.
            second_finished.wait(timeout=0.2)
        finally:
            release_first.set()
        first_update.result(timeout=5)
        second_update.result(timeout=5)

    expected = {
        "first": "om_first",
        "second": "om_second",
    }
    if spelling != "case_initial":
        expected["initial"] = "om_initial"
    assert json.loads(path.read_text()) == expected
    assert JsonFileEventMessageStore(other_path).get("first") == "om_first"
    if spelling == "symlink":
        assert other_path.is_symlink()


def test_json_writes_use_independent_temporary_files(tmp_path, monkeypatch) -> None:
    path = tmp_path / "event-messages.json"
    first_ready = threading.Event()
    release_first = threading.Event()
    replace = Path.replace

    def paused_replace(temporary: Path, target: Path) -> Path:
        if not first_ready.is_set():
            first_ready.set()
            assert release_first.wait(timeout=5)
        return replace(temporary, target)

    monkeypatch.setattr(Path, "replace", paused_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_write = pool.submit(_write_json, path, {"first": "om_first"})
        try:
            assert first_ready.wait(timeout=5)
            pool.submit(_write_json, path, {"second": "om_second"}).result(timeout=5)
        finally:
            release_first.set()
        first_write.result(timeout=5)

    assert json.loads(path.read_text()) == {"first": "om_first"}
    assert list(tmp_path.iterdir()) == [path]


def test_json_write_failure_preserves_state_and_removes_temporary_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "event-messages.json"
    path.write_text('{"initial": "om_initial"}')

    def failed_replace(temporary: Path, target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", failed_replace)
    with pytest.raises(OSError, match="replace failed"):
        _write_json(path, {"first": "om_first"})

    assert json.loads(path.read_text()) == {"initial": "om_initial"}
    assert list(tmp_path.iterdir()) == [path]
