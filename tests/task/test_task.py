import pytest

from feishu.task.comments import CommentsNamespace
from feishu.task.task import TaskNamespace
from feishu.task.tasks import TasksNamespace


@pytest.fixture
async def task(client):
    """The cached ``client.task`` dispatcher, auto-closed."""
    yield client.task
    await client.aclose()


class TestDispatcher:
    async def test_task_is_cached(self, client, task):
        assert isinstance(task, TaskNamespace)
        assert client.task is task

    @pytest.mark.parametrize(
        "name, expected",
        [("tasks", TasksNamespace), ("comments", CommentsNamespace)],
    )
    async def test_subnamespace_lazily_cached(self, task, name, expected):
        sub = getattr(task, name)
        assert isinstance(sub, expected)
        assert getattr(task, name) is sub
