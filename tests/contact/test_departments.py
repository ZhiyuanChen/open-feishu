import httpx
import pytest

from feishu.contact import normalize_department
from tests.conftest import envelope, make_client, paginated_responder, token_handler


def _truthy_param(value: str) -> bool:
    return value.lower() in ("true", "1")


@pytest.fixture
async def departments(client_factory, recorder):
    """A bound ``contact.departments`` namespace; the test sets the responder via ``.respond``."""

    state: dict = {"responder": lambda r: envelope({})}

    def responder(request):
        return state["responder"](request)

    client = client_factory(recorder=recorder, responder=responder)

    def respond(fn):
        state["responder"] = fn
        return client.contact.departments

    client.contact.departments.respond = respond  # type: ignore[attr-defined]
    try:
        yield client.contact.departments
    finally:
        await client.aclose()


class TestGetDepartment:
    async def test_get_returns_raw(self, departments, recorder):
        department = {"department_id": "d1", "open_department_id": "od-1", "name": "Eng", "member_count": 7}
        departments.respond(lambda r: envelope({"department": department}))
        data = await departments.get("od-1")

        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("contact/v3/departments/od-1")
        # Raw Feishu body passes through; normalization is opt-in by the caller.
        assert data["department"] == department
        assert normalize_department(data["department"])["member_count"] == 7

    @pytest.mark.parametrize(
        "id_type, expected",
        [(None, "open_department_id"), ("department_id", "department_id")],
        ids=["default", "custom"],
    )
    async def test_get_forwards_id_type(self, departments, recorder, id_type, expected):
        departments.respond(lambda r: envelope({"department": {}}))
        kwargs = {"department_id_type": id_type} if id_type else {}
        await departments.get("123", **kwargs)
        assert recorder.last[2]["department_id_type"] == expected


class TestListDepartments:
    async def test_paginates_and_caps(self, client_factory, recorder):
        responder = paginated_responder(
            [
                [{"department_id": "d1", "open_department_id": "od-1", "name": "A"}],
                [{"department_id": "d2", "open_department_id": "od-2", "name": "B"}],
            ]
        )
        client = client_factory(recorder=recorder, responder=responder)
        children = await client.contact.departments.list("od-root", page_size=200)
        # Raw children, order preserved across pages.
        assert [c["department_id"] for c in children] == ["d1", "d2"]
        # Opt-in normalization is applied explicitly by the caller.
        assert normalize_department(children[0])["open_department_id"] == "od-1"

        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("contact/v3/departments/od-root/children")
        assert _truthy_param(params["fetch_child"])
        assert int(params["page_size"]) <= 50
        # Second page carries the page_token from the first response.
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_defaults_to_root(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        result = await client.contact.departments.list()
        assert result == []
        assert recorder.last[1].endswith("contact/v3/departments/0/children")
        await client.aclose()


class TestGetDepartmentParent:
    async def test_parent_returns_raw_departments(self):
        pages = [
            ([{"open_department_id": "od-parent-1", "name": "Parent"}], True, "p2"),
            ([{"department_id": "d-parent-2", "name": "Root"}], False, None),
        ]
        record = []
        state = {"call": 0}

        def handler(request):
            tok = token_handler(request)
            if tok is not None:
                return tok
            record.append((request.method, request.url.path, dict(request.url.params)))
            items, has_more, token = pages[state["call"]]
            state["call"] += 1
            data = {"items": items, "has_more": has_more}
            if token:
                data["page_token"] = token
            return httpx.Response(200, json=envelope(data))

        client = make_client(handler)
        parents = await client.contact.departments.parent("od-child")
        assert parents == [
            {"open_department_id": "od-parent-1", "name": "Parent"},
            {"department_id": "d-parent-2", "name": "Root"},
        ]
        method, path, params = record[0]
        assert method == "GET" and path.endswith("contact/v3/departments/parent")
        assert params["department_id"] == "od-child"
        assert record[1][2]["page_token"] == "p2"
        await client.aclose()


class TestGetDepartmentParentIds:
    async def test_prefers_open_id(self):
        # Page 1 yields a parent identified by open_department_id; page 2 yields one
        # that only has department_id. Both ids must surface, in order.
        pages = [
            ([{"open_department_id": "od-parent-1"}], True, "p2"),
            ([{"department_id": "d-parent-2"}], False, None),
        ]
        record = []
        state = {"call": 0}

        def handler(request):
            tok = token_handler(request)
            if tok is not None:
                return tok
            record.append((request.method, request.url.path, dict(request.url.params)))
            items, has_more, token = pages[state["call"]]
            state["call"] += 1
            data = {"items": items, "has_more": has_more}
            if token:
                data["page_token"] = token
            return httpx.Response(200, json=envelope(data))

        client = make_client(handler)
        ids = await client.contact.departments.parent_ids("od-child")
        # Open id preferred, else department id; both pages contribute.
        assert ids == ["od-parent-1", "d-parent-2"]
        method, path, params = record[0]
        assert method == "GET" and path.endswith("contact/v3/departments/parent")
        assert params["department_id"] == "od-child"
        assert record[1][2]["page_token"] == "p2"
        await client.aclose()


class TestExpandDepartmentIds:
    async def test_dedupes_preserving_order(self):
        # Each seed maps to its parent chain. The two seeds share a common ancestor
        # ("od-root") so the raw concatenation has duplicates.
        parents = {
            "od-a": [{"open_department_id": "od-mid"}, {"open_department_id": "od-root"}],
            "od-b": [{"open_department_id": "od-root"}],
        }

        def handler(request):
            tok = token_handler(request)
            if tok is not None:
                return tok
            did = request.url.params["department_id"]
            return httpx.Response(200, json=envelope({"items": parents[did], "has_more": False}))

        client = make_client(handler)
        expanded = await client.contact.departments.expand_ids(["od-a", "od-b"])
        # De-duplicated, first-seen order preserved; od-root appears exactly once.
        assert expanded == ["od-a", "od-mid", "od-root", "od-b"]
        assert expanded.count("od-root") == 1
        # A seed appears before the parents it introduced.
        assert expanded.index("od-a") < expanded.index("od-mid") < expanded.index("od-root")
        # od-root keeps its first position even though od-b also lists it.
        assert expanded.index("od-root") < expanded.index("od-b")
        await client.aclose()


class TestWriteDepartment:
    async def test_create_posts_body(self, departments, recorder):
        departments.respond(lambda r: envelope({"department": {"open_department_id": "od-1"}}))
        result = await departments.create({"name": "Eng", "parent_department_id": "0"})

        method, path, _, body = recorder.last
        assert method == "POST"
        assert path.endswith("/contact/v3/departments")
        assert body["name"] == "Eng"
        assert body["parent_department_id"] == "0"
        assert result["department"]["open_department_id"] == "od-1"

    async def test_update_patches_body(self, departments, recorder):
        departments.respond(lambda r: envelope({"department": {"name": "Platform"}}))
        result = await departments.update("od-1", {"name": "Platform"})

        method, path, _, body = recorder.last
        assert method == "PATCH"
        assert path.endswith("/contact/v3/departments/od-1")
        assert body["name"] == "Platform"
        assert result["department"]["name"] == "Platform"

    async def test_delete_issues_delete(self, departments, recorder):
        await departments.delete("od-1")
        method, path, _, _ = recorder.last
        assert method == "DELETE"
        assert path.endswith("/contact/v3/departments/od-1")

    async def test_id_types_omitted_when_unset(self, departments, recorder):
        # Representative absence guard: id-type query params are not sent unless set.
        departments.respond(lambda r: envelope({"department": {}}))
        await departments.create({"name": "Eng"})
        params = recorder.last[2]
        assert "department_id_type" not in params
        assert "user_id_type" not in params

    @pytest.mark.parametrize(
        "call",
        [
            lambda d: d.create({"name": "Eng"}, department_id_type="department_id", user_id_type="open_id"),
            lambda d: d.update("123", {"name": "P"}, department_id_type="department_id", user_id_type="open_id"),
            lambda d: d.delete("123", department_id_type="department_id"),
        ],
        ids=["create", "update", "delete"],
    )
    async def test_id_types_forwarded_when_set(self, departments, recorder, call):
        departments.respond(lambda r: envelope({"department": {}}))
        await call(departments)
        params = recorder.last[2]
        assert params["department_id_type"] == "department_id"
