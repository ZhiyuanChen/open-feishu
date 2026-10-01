from __future__ import annotations

from ..toolkit.memory import recall_memory, remember_memory
from ..tools import ToolRegistry
from .registry import BUNDLES, BundleContext


class MemoryBundle:
    """Opt-in tools for bounded user and project memory."""

    def register(self, registry: ToolRegistry, _context: BundleContext) -> None:
        registry.add(
            recall_memory(description="按关键词检索当前用户与当前项目已确认保存的记忆；仅在需要过去偏好或决定时调用。")
        )
        registry.add(remember_memory(description="保存一条用户或项目记忆；执行前我会先发确认卡片。"))


BUNDLES.register(MemoryBundle, name="memory", override=True)
