from tests.conftest import envelope


class TestGet:
    async def test_get_fetches_app_and_returns_data(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"app": {"app_token": "bascnxxx"}}))
        resp = await client.bitable.apps.get("bascnxxx")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/bitable/v1/apps/bascnxxx")
        assert resp["app"]["app_token"] == "bascnxxx"
        await client.aclose()
