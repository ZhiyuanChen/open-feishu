# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .llm import ToolSpec
from .result import ToolOutcome, ToolResult


class ToolValidationError(ValueError):
    r"""
    工具参数校验失败时抛出。

    当 [feishu.agent.tools.ToolRegistry.dispatch][] 收到的参数不是对象、缺少必填字段，或在
    `additionalProperties` 为 `False` 时出现多余字段，即抛出该异常。

    Examples:
        >>> raise ToolValidationError("missing required argument")
        Traceback (most recent call last):
            ...
        feishu.agent.tools.ToolValidationError: missing required argument
    """


@dataclass
class Tool:
    r"""
    一个已注册的工具：名称、描述、参数 Schema、处理函数及是否需要审批。

    `handler` 既可为同步函数也可为协程函数；同步函数在分发时会被放到工作线程中执行，避免阻塞事件循环。
    当 `requires_approval` 为 `True` 时，[feishu.agent.loop.Agent][] 会先发送审批卡片并挂起本轮对话，
    待用户批准后再执行。

    Examples:
        >>> async def weather(city):
        ...     return f"{city}：晴"
        >>> tool = Tool(
        ...     name="weather",
        ...     description="查询天气",
        ...     input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
        ...     handler=weather,
        ... )
        >>> tool.requires_approval
        False
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Awaitable[Any] | Any]
    requires_approval: bool = False


def _validate(name: str, input_schema: dict[str, Any], arguments: Any) -> None:
    _validate_schema(name, "$", input_schema, arguments)


def _validate_schema(name: str, path: str, schema: dict[str, Any], value: Any) -> None:
    if not isinstance(schema, dict):
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ToolValidationError(f"tool {name!r} argument {path} must be one of {schema['enum']!r}")
    schema_type = schema.get("type")
    if schema_type is not None and not _matches_schema_type(value, schema_type):
        expected = " or ".join(schema_type) if isinstance(schema_type, list) else str(schema_type)
        raise ToolValidationError(f"tool {name!r} argument {path} expects {expected}, got {type(value).__name__}")
    if isinstance(value, dict):
        _validate_object_schema(name, path, schema, value)
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema(name, f"{path}[{index}]", item_schema, item)


def _validate_object_schema(name: str, path: str, schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ToolValidationError(f"tool {name!r} expects an object of arguments, got {type(arguments).__name__}")
    for required in schema.get("required", []):
        if required not in arguments:
            raise ToolValidationError(f"tool {name!r} missing required argument {required!r}")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        allowed = set(properties)
        extra = set(arguments) - allowed
        if extra:
            raise ToolValidationError(f"tool {name!r} got unexpected argument(s) {sorted(extra)}")
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            if key in arguments and isinstance(subschema, dict):
                _validate_schema(name, f"{path}.{key}" if path != "$" else key, subschema, arguments[key])
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        for key, item in arguments.items():
            if isinstance(properties, dict) and key in properties:
                continue
            _validate_schema(name, f"{path}.{key}" if path != "$" else key, additional, item)


def _matches_schema_type(value: Any, schema_type: str | list[str]) -> bool:
    types = schema_type if isinstance(schema_type, list) else [schema_type]
    return any(_matches_single_schema_type(value, item) for item in types)


def _matches_single_schema_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


class ToolRegistry:
    r"""
    工具注册表，负责工具的注册、声明导出与分发执行。

    既支持装饰器形式注册，也支持直接传入处理函数；通过 [feishu.agent.tools.ToolRegistry.specs][] 将
    已注册工具导出为 [feishu.agent.llm.ToolSpec][] 列表交给模型，再由
    [feishu.agent.tools.ToolRegistry.dispatch][] 校验参数并执行对应处理函数。

    Examples:
        >>> import asyncio
        >>> reg = ToolRegistry()
        >>> schema = {"type": "object"}
        >>> async def weather(city):
        ...     return f"{city}：晴"
        >>> _ = reg.register("weather", weather, input_schema=schema, description="天气")
        >>> reg.specs()
        [ToolSpec(name='weather', description='天气', input_schema={'type': 'object'})]
        >>> asyncio.run(reg.dispatch("weather", {"city": "上海"})).content
        '上海：晴'
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str | None = None,
        handler: Callable[..., Any] | None = None,
        *,
        input_schema: dict[str, Any],
        description: str,
        requires_approval: bool = False,
    ) -> Callable[..., Any] | None:
        r"""
        注册一个工具，支持装饰器与直接调用两种形式。

        直接传入 `handler` 时立即注册并原样返回该处理函数；省略 `handler` 时返回一个装饰器，可直接装饰处理
        函数。未显式指定 `name` 时取处理函数的 `__name__` 作为工具名。

        Args:
            name: 工具名称。省略时取处理函数的 `__name__`。
            handler: 工具处理函数，可为同步函数或协程函数。省略时本方法返回装饰器。
            input_schema: 描述工具参数的 JSON Schema。
            description: 工具描述，供模型理解其用途。
            requires_approval: 是否在执行前要求用户审批。默认为 `False`。

        Returns:
            直接调用形式下原样返回 `handler`；装饰器形式下返回用于装饰处理函数的装饰器。

        Raises:
            ValueError: 既未提供 `name` 又无法从处理函数推断出名称时抛出。

        Examples:
            >>> reg = ToolRegistry()
            >>> schema = {"type": "object", "properties": {}}
            >>> @reg.register("ping", input_schema=schema, description="心跳")
            ... async def ping():
            ...     return "pong"
            >>> reg.get("ping").name
            'ping'
        """

        def _add(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or getattr(fn, "__name__", None)
            if not tool_name:
                raise ValueError("tool name is required")
            self._tools[tool_name] = Tool(
                name=tool_name,
                description=description,
                input_schema=input_schema,
                handler=fn,
                requires_approval=requires_approval,
            )
            return fn

        if handler is not None:
            return _add(handler)
        return _add  # decorator form

    def add(self, tool: Tool) -> Tool:
        r"""
        注册一个已构造的 [feishu.agent.tools.Tool][]，并原样返回。

        适用于注册由工厂产出的工具（如 [feishu.agent.toolkit][] 中的工厂），无需经 `register` 重新声明
        Schema 与描述。

        Args:
            tool: 待注册的工具。

        Returns:
            原样返回 `tool`，便于链式使用。

        Examples:
            >>> reg = ToolRegistry()
            >>> async def ping(): return "pong"
            >>> tool = Tool(name="ping", description="心跳", input_schema={"type": "object"}, handler=ping)
            >>> reg.add(tool).name
            'ping'
        """
        self._tools[tool.name] = tool
        return tool

    def specs(self) -> list[ToolSpec]:
        r"""
        将所有已注册工具导出为 [feishu.agent.llm.ToolSpec][] 列表。

        Returns:
            工具声明列表，可直接作为 `tools` 参数传给 [feishu.agent.llm.LlmBackend.stream][]。

        Examples:
            >>> reg = ToolRegistry()
            >>> async def ping():
            ...     return "pong"
            >>> _ = reg.register("ping", ping, input_schema={"type": "object"}, description="心跳")
            >>> reg.specs()
            [ToolSpec(name='ping', description='心跳', input_schema={'type': 'object'})]
        """
        return [
            ToolSpec(name=t.name, description=t.description, input_schema=t.input_schema) for t in self._tools.values()
        ]

    def get(self, name: str) -> Tool:
        r"""
        按名称获取已注册的工具。

        Args:
            name: 工具名称。

        Returns:
            对应的 [feishu.agent.tools.Tool][]。

        Raises:
            KeyError: 工具未注册时抛出。
        """
        return self._tools[name]

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        r"""
        校验参数并执行指定工具，返回归一化的 [feishu.agent.result.ToolResult][]。

        先依据工具的 `input_schema` 校验 `arguments`，再调用对应处理函数。协程处理函数会被 `await`；
        同步处理函数则放到工作线程中执行，避免阻塞事件循环。

        Args:
            name: 工具名称。
            arguments: 已解析为字典的工具参数。

        Returns:
            归一化后的 [feishu.agent.result.ToolResult][]；处理函数返回的原始值会被包装为 `COMPLETED` 结果，
            使调用方（主循环、审批引擎）始终拿到统一的结果形状。

        Raises:
            KeyError: 工具未注册时抛出。
            ToolValidationError: 参数未通过 `input_schema` 校验时抛出。

        Examples:
            >>> import asyncio
            >>> reg = ToolRegistry()
            >>> schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
            >>> async def weather(city):
            ...     return f"{city}：晴"
            >>> _ = reg.register("weather", weather, input_schema=schema, description="查询天气")
            >>> asyncio.run(reg.dispatch("weather", {"city": "北京"})).content
            '北京：晴'
        """
        tool = self._tools[name]  # raises KeyError if unknown
        _validate(name, tool.input_schema, arguments)
        if inspect.iscoroutinefunction(tool.handler) or inspect.iscoroutinefunction(type(tool.handler).__call__):
            result = await tool.handler(**arguments)
        else:
            result = await asyncio.to_thread(tool.handler, **arguments)
            if inspect.isawaitable(result):
                result = await result
        # Normalize every handler's return into a ToolResult so callers get one uniform shape; a raw value
        # becomes a COMPLETED result carrying it verbatim.
        return result if isinstance(result, ToolResult) else ToolResult(ToolOutcome.COMPLETED, content=result)
