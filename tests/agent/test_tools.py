import threading

import pytest

from feishu.agent.llm import ToolSpec
from feishu.agent.tools import Tool, ToolRegistry, ToolValidationError

SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}


@pytest.fixture
def reg():
    registry = ToolRegistry()

    async def weather(city):
        return f"sunny in {city}"

    registry.register("weather", weather, input_schema=SCHEMA, description="get weather")
    return registry


class TestRegister:
    def test_plain_call_returns_handler(self):
        reg = ToolRegistry()

        async def weather(city):
            return city

        ret = reg.register("weather", weather, input_schema=SCHEMA, description="d")
        assert ret is weather
        assert reg.get("weather").requires_approval is False

    def test_specs(self, reg):
        assert reg.specs() == [ToolSpec(name="weather", description="get weather", input_schema=SCHEMA)]

    async def test_decorator_form(self):
        reg = ToolRegistry()

        @reg.register("echo", input_schema=SCHEMA, description="echo", requires_approval=True)
        async def echo(city):
            return city

        tool = reg.get("echo")
        assert tool.requires_approval is True
        assert tool.handler is echo
        assert await reg.dispatch("echo", {"city": "sh"}) == "sh"

    def test_get_returns_tool(self, reg):
        assert isinstance(reg.get("weather"), Tool)

    def test_get_unknown_raises(self, reg):
        with pytest.raises(KeyError):
            reg.get("missing")


class TestDispatch:
    async def test_async_handler(self, reg):
        assert await reg.dispatch("weather", {"city": "beijing"}) == "sunny in beijing"

    async def test_sync_handler_runs_off_event_loop(self):
        reg = ToolRegistry()
        loop_thread = threading.get_ident()
        seen = {}

        def blocking(city):
            seen["thread"] = threading.get_ident()
            return city.upper()

        reg.register("up", blocking, input_schema=SCHEMA, description="d")
        assert await reg.dispatch("up", {"city": "shanghai"}) == "SHANGHAI"
        # sync handler must run in a worker thread, not block the event loop thread
        assert seen["thread"] != loop_thread

    async def test_async_callable_class_is_awaited(self):
        """Callable-class instance whose __call__ is async must be awaited, not returned as a coroutine."""
        reg = ToolRegistry()

        class AsyncCityHandler:
            async def __call__(self, city: str) -> str:
                return f"async-class:{city}"

        reg.register("async_class", AsyncCityHandler(), input_schema=SCHEMA, description="d")
        assert await reg.dispatch("async_class", {"city": "tokyo"}) == "async-class:tokyo"

    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param({}, id="missing-required"),
            pytest.param({"city": "x", "zzz": 1}, id="unknown-key"),
            pytest.param(["not", "a", "dict"], id="non-dict"),
        ],
    )
    async def test_invalid_arguments_raise(self, reg, arguments):
        with pytest.raises(ToolValidationError):
            await reg.dispatch("weather", arguments)

    async def test_unknown_tool_raises(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError):
            await reg.dispatch("nope", {})
