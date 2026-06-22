import pytest

from feishu.vc.meetings import MeetingsNamespace
from feishu.vc.reserves import ReservesNamespace
from feishu.vc.vc import VCNamespace


@pytest.fixture
async def vc(client):
    yield client.vc
    await client.aclose()


class TestDispatcher:
    async def test_vc_namespace_cached(self, client):
        assert isinstance(client.vc, VCNamespace)
        assert client.vc is client.vc
        await client.aclose()

    @pytest.mark.parametrize(
        "attr, cls",
        [
            ("reserves", ReservesNamespace),
            ("meetings", MeetingsNamespace),
        ],
    )
    async def test_subnamespace_cached(self, vc, attr, cls):
        sub = getattr(vc, attr)
        assert isinstance(sub, cls)
        assert getattr(vc, attr) is sub
