import pytest

from tests.conftest import envelope, make_client


@pytest.fixture
async def tasks(recorder):
    client = make_client(recorder=recorder, responder=lambda r: envelope({}))
    try:
        yield client.approval.tasks
    finally:
        await client.aclose()


class TestTasks:
    @pytest.mark.parametrize(
        "action, endpoint, task",
        [
            (
                "approve",
                "approve",
                {"approval_code": "ABC123", "instance_code": "INST1", "task_id": "T1", "user_id": "u1"},
            ),
            (
                "reject",
                "reject",
                {"approval_code": "ABC123", "instance_code": "INST1", "task_id": "T1", "user_id": "u1"},
            ),
            ("transfer", "transfer", {"task_id": "T1", "user_id": "u1", "transfer_user_id": "u2"}),
        ],
    )
    async def test_posts_task(self, tasks, recorder, action, endpoint, task):
        await getattr(tasks, action)(task)
        method, path, _, body = recorder.last
        assert method == "POST"
        assert path.endswith(f"/approval/v4/tasks/{endpoint}")
        assert body == task
