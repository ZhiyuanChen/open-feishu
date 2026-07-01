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

import copy
import logging
import os
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, cast

import httpx
from chanfig import NestedDict

from ._transport import RetryPolicy, Transport
from .auth.credentials import Credential, InternalCredential
from .auth.tokens import TokenCache, TokenManager
from .consts import DEFAULT_TIMEOUT, MAX_PAGE_SIZE, resolve_base_url
from .pagination import paginate

if TYPE_CHECKING:
    from typing import Protocol

    from .approval.approval import ApprovalNamespace
    from .auth.oauth import OAuthNamespace
    from .bitable.bitable import BitableNamespace
    from .board.board import BoardNamespace
    from .calendar.calendar import CalendarNamespace
    from .cards import Card as _Card
    from .cards import CardAction as _CardAction
    from .cards import ColumnSet as _ColumnSet
    from .contact.contact import ContactNamespace
    from .docx.documents import DocxNamespace
    from .drive.drive import DriveNamespace
    from .im.messages import IMNamespace
    from .meeting_room.rooms import MeetingRoomNamespace
    from .sheets.spreadsheets import SheetsNamespace
    from .task.task import TaskNamespace
    from .vc.vc import VCNamespace
    from .wiki.spaces import WikiNamespace

    class CardsModule(Protocol):
        r"""
        `feishu.cards` 构建模块的类型化外观（typed facade）。

        卡片构建器是无状态的，不绑定客户端、不发起请求，因此 [feishu.client.FeishuClient.cards][]
        直接返回该模块本身；本协议仅用于为属性访问提供静态类型与补全，运行时不构造任何对象。
        工厂函数以返回类型标注（参数签名以各自源函数为准）。
        """

        Card: type[_Card]
        ColumnSet: type[_ColumnSet]
        CardAction: type[_CardAction]
        button: Callable[..., dict[str, Any]]
        column_set: Callable[..., dict[str, Any]]
        hr: Callable[..., dict[str, Any]]
        img: Callable[..., dict[str, Any]]
        md: Callable[..., dict[str, Any]]
        alert_card: Callable[..., dict[str, Any]]
        table_card: Callable[..., dict[str, Any]]
        text_card: Callable[..., dict[str, Any]]
        parse_action: Callable[..., _CardAction]
        escape_markdown: Callable[[str], str]


# Lazy namespace cache attribute names; reset when deriving a user-scoped view (as_user).
_NAMESPACE_SLOTS = (
    "_approval",
    "_bitable",
    "_board",
    "_calendar",
    "_contact",
    "_docx",
    "_drive",
    "_im",
    "_meeting_room",
    "_oauth",
    "_sheets",
    "_task",
    "_vc",
    "_wiki",
)


class FeishuClient:
    r"""
    飞书开放平台异步客户端。

    统一管理凭证、令牌的自动获取与缓存、HTTP 传输（429 自动重试；网络错误与 5xx 仅对幂等请求重试），并提供
    各业务能力的命名空间入口（[feishu.client.FeishuClient.im][]、
    [feishu.client.FeishuClient.contact][]、[feishu.client.FeishuClient.oauth][]
    等）。推荐通过 `async with` 使用以自动释放底层连接。

    单次请求返回的 [chanfig.NestedDict][] 会附带 `raw_envelope` 属性，可据此读取
    顶层的 `code` / `msg` 等原始信封字段；分页 helper 可能返回普通列表。

    应用凭证可直接传入 `app_id` / `app_secret`，也可通过环境变量 `FEISHU_APP_ID` /
    `FEISHU_APP_SECRET` 提供，或传入自定义 `credential`。

    Args:
        app_id: 应用 App ID；缺省时回退至环境变量 `FEISHU_APP_ID`。
        app_secret: 应用 App Secret；缺省时回退至环境变量 `FEISHU_APP_SECRET`。
        region: 区域标识，`feishu`（默认）或 `lark`。
        base_url: 自定义 API 基础地址，优先于 `region`。
        accounts_url: 自定义 OAuth 授权（accounts）域名，优先于 `region`；可用于覆盖 `lark`
            区域下尚未经线上确认的默认主机，与 `base_url`（API 域名）相互独立。
        timeout: 请求超时时间（秒）。
        retry: 重试策略 [feishu.RetryPolicy][]，缺省使用默认策略。
        credential: 自定义凭证对象；提供后将忽略 `app_id` / `app_secret`。
        token_cache: 自定义令牌缓存。
        transport: 自定义 `httpx.AsyncClient`；由调用方提供时其生命周期不归本客户端管理。
        logger: 自定义日志器，缺省使用名为 `feishu` 的日志器。

    Raises:
        ValueError: 当既未提供凭证，也无法从环境变量解析出 `app_id` / `app_secret` 时抛出。

    飞书文档:
        [服务端 API 调用流程](https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/overview)

    Examples:
        >>> import asyncio
        >>> async def main():
        ...     async with FeishuClient("cli_xxx", "secret") as client:
        ...         return await client.im.send("ou_xxx", "hello")
        >>> asyncio.run(main())  # doctest: +SKIP
        {'message_id': 'om_xxx', 'msg_type': 'text', ...}
    """

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        *,
        region: str = "feishu",
        base_url: str | None = None,
        accounts_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retry: RetryPolicy | None = None,
        credential: Credential | None = None,
        token_cache: TokenCache | None = None,
        transport: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.base_url = resolve_base_url(region, base_url)
        self.region = region or "feishu"
        self.accounts_url = accounts_url
        self.logger = logger or logging.getLogger("feishu")
        self._transport = Transport(
            self.base_url,
            timeout=timeout,
            retry=retry or RetryPolicy.default(),
            client=transport,
            logger=self.logger,
        )
        if credential is None:
            app_id = app_id or os.getenv("FEISHU_APP_ID")
            app_secret = app_secret or os.getenv("FEISHU_APP_SECRET")
            if not app_id or not app_secret:
                raise ValueError(
                    "FeishuClient requires app_id/app_secret (args or FEISHU_APP_ID/FEISHU_APP_SECRET env)"
                )
            credential = InternalCredential(app_id, app_secret)
        self._credential: Credential = credential
        self._tokens = TokenManager(credential, self._transport, cache=token_cache)
        self._approval: ApprovalNamespace | None = None
        self._bitable: BitableNamespace | None = None
        self._board: BoardNamespace | None = None
        self._calendar: CalendarNamespace | None = None
        self._contact: ContactNamespace | None = None
        self._docx: DocxNamespace | None = None
        self._drive: DriveNamespace | None = None
        self._im: IMNamespace | None = None
        self._meeting_room: MeetingRoomNamespace | None = None
        self._oauth: OAuthNamespace | None = None
        self._sheets: SheetsNamespace | None = None
        self._task: TaskNamespace | None = None
        self._vc: VCNamespace | None = None
        self._wiki: WikiNamespace | None = None
        self._user_token: str | None = None
        self._owns_transport = True

    @property
    def approval(self) -> ApprovalNamespace:
        r"""
        审批（Approval）命名空间，提供审批定义、实例、任务与评论的查询、创建与处理等能力。

        Returns:
            绑定到本客户端的 Approval 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [审批概述](https://open.feishu.cn/document/server-docs/approval-v4/approval-overview)
        """
        if self._approval is None:
            from .approval.approval import ApprovalNamespace

            self._approval = ApprovalNamespace(self)
        return self._approval

    @property
    def bitable(self) -> BitableNamespace:
        r"""
        多维表格（Bitable）命名空间，提供数据表、字段与记录的查询、创建、更新与删除等能力。

        Returns:
            绑定到本客户端的 Bitable 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [多维表格概述](https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview)
        """
        if self._bitable is None:
            from .bitable.bitable import BitableNamespace

            self._bitable = BitableNamespace(self)
        return self._bitable

    @property
    def board(self) -> BoardNamespace:
        r"""
        画板（Board）命名空间，通过 `client.board.whiteboards` 提供画板主题、节点列举与导出为图片等能力。

        Returns:
            绑定到本客户端的 Board 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [画板概述](https://open.feishu.cn/document/docs/board-v1/overview)
        """
        if self._board is None:
            from .board.board import BoardNamespace

            self._board = BoardNamespace(self)
        return self._board

    @property
    def calendar(self) -> CalendarNamespace:
        r"""
        日历（Calendar）命名空间，提供日历、日程、参与人的查询、创建、更新与删除等能力。

        Returns:
            绑定到本客户端的 Calendar 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [日历概述](https://open.feishu.cn/document/server-docs/calendar-v4/calendar/introduction)
        """
        if self._calendar is None:
            from .calendar.calendar import CalendarNamespace

            self._calendar = CalendarNamespace(self)
        return self._calendar

    @property
    def cards(self) -> CardsModule:
        r"""
        卡片构建命名空间，暴露无状态的卡片构造器与工厂函数。

        与其他业务命名空间不同，卡片构建器不绑定客户端、不发起请求，因此本属性直接返回
        `feishu.cards` 模块本身（以类型化外观 `CardsModule` 标注，以获得静态检查与补全）。

        Returns:
            `feishu.cards` 模块，可直接调用其卡片构造器与工厂函数。

        飞书文档:
            [卡片结构](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)

        Examples:
            >>> import asyncio
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return client.cards.Card().header("Hi").markdown("body").to_dict()
            >>> card = asyncio.run(main())  # doctest: +SKIP
        """
        from . import cards as _cards  # lazy: stateless builder module

        # The module structurally provides every CardsModule member; cast gives callers the
        # typed facade (mypy cannot match a module's functions against Protocol callable attrs).
        return cast("CardsModule", _cards)

    @property
    def contact(self) -> ContactNamespace:
        r"""
        通讯录命名空间，提供用户、部门等信息查询能力。

        Returns:
            绑定到本客户端的通讯录命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [通讯录](https://open.feishu.cn/document/server-docs/contact-v3/resources)
        """
        if self._contact is None:
            from .contact.contact import ContactNamespace

            self._contact = ContactNamespace(self)
        return self._contact

    @property
    def docx(self) -> DocxNamespace:
        r"""
        新版文档（Docx）命名空间，提供云文档的创建、读取与块编辑等能力。

        Returns:
            绑定到本客户端的 Docx 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [文档概述](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/docx-overview)
        """
        if self._docx is None:
            from .docx.documents import DocxNamespace

            self._docx = DocxNamespace(self)
        return self._docx

    @property
    def drive(self) -> DriveNamespace:
        r"""
        云空间（Drive）命名空间，提供文件的列举、上传、下载、复制、移动与删除等能力。

        Returns:
            绑定到本客户端的 Drive 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [云空间概述](https://open.feishu.cn/document/server-docs/docs/drive-v1/introduction)
        """
        if self._drive is None:
            from .drive.drive import DriveNamespace

            self._drive = DriveNamespace(self)
        return self._drive

    @property
    def im(self) -> IMNamespace:
        r"""
        即时消息（IM）命名空间，提供发送、回复、撤回消息等能力。

        Returns:
            绑定到本客户端的 IM 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [消息管理](https://open.feishu.cn/document/server-docs/im-v1/message/intro)
        """
        if self._im is None:
            from .im.messages import IMNamespace

            self._im = IMNamespace(self)
        return self._im

    @property
    def meeting_room(self) -> MeetingRoomNamespace:
        r"""
        会议室命名空间，提供会议室列表、详情与忙闲查询能力。

        Returns:
            绑定到本客户端的 MeetingRoom 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [会议室](https://open.feishu.cn/document/server-docs/calendar-v4/meeting-room-event/query-room-availability)
        """
        if self._meeting_room is None:
            from .meeting_room.rooms import MeetingRoomNamespace

            self._meeting_room = MeetingRoomNamespace(self)
        return self._meeting_room

    @property
    def oauth(self) -> OAuthNamespace:
        r"""
        用户身份 OAuth 命名空间，提供授权链接、授权码换取与刷新令牌等能力。

        Returns:
            绑定到本客户端的 OAuth 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [获取user_access_token](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/authen-v1/access_token/create)
        """
        if self._oauth is None:
            from .auth.oauth import OAuthNamespace

            self._oauth = OAuthNamespace(self)
        return self._oauth

    @property
    def sheets(self) -> SheetsNamespace:
        r"""
        电子表格（Sheets）命名空间，提供电子表格的创建、读取与单元格读写等能力。

        Returns:
            绑定到本客户端的 Sheets 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [电子表格概述](https://open.feishu.cn/document/server-docs/docs/sheets-v3/overview)
        """
        if self._sheets is None:
            from .sheets.spreadsheets import SheetsNamespace

            self._sheets = SheetsNamespace(self)
        return self._sheets

    @property
    def task(self) -> TaskNamespace:
        r"""
        任务（Task）命名空间，提供任务的创建、查询、列举、更新、删除以及任务评论等能力。

        Returns:
            绑定到本客户端的 Task 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [任务概述](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/task-v2/overview)
        """
        if self._task is None:
            from .task.task import TaskNamespace

            self._task = TaskNamespace(self)
        return self._task

    @property
    def tokens(self) -> TokenManager:
        r"""
        令牌管理器，负责应用级令牌的自动获取、缓存与刷新。

        Returns:
            当前客户端使用的 [feishu.auth.tokens.TokenManager][] 实例。
        """
        return self._tokens

    @property
    def vc(self) -> VCNamespace:
        r"""
        视频会议（VC）命名空间，提供会议预约的创建/查询/更新/删除与会议详情查询等能力。

        Returns:
            绑定到本客户端的 VC 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [视频会议概述](https://open.feishu.cn/document/server-docs/vc-v1/video-conferencing-overview)
        """
        if self._vc is None:
            from .vc.vc import VCNamespace

            self._vc = VCNamespace(self)
        return self._vc

    @property
    def wiki(self) -> WikiNamespace:
        r"""
        知识库（Wiki）命名空间，提供知识空间的查询、节点管理与内容组织等能力。

        Returns:
            绑定到本客户端的 Wiki 命名空间对象（首次访问时惰性创建）。

        飞书文档:
            [知识库概述](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview)
        """
        if self._wiki is None:
            from .wiki.spaces import WikiNamespace

            self._wiki = WikiNamespace(self)
        return self._wiki

    async def aclose(self) -> None:
        r"""
        释放客户端持有的资源。

        仅当底层 `httpx.AsyncClient` 由本客户端创建时才会将其关闭；调用方自行传入的
        传输对象不会被关闭。由 [feishu.client.FeishuClient.as_user][] 派生的用户视图共享
        底层连接，对其调用本方法不会关闭传输层。该方法可幂等地重复调用。
        """
        if self._owns_transport:
            await self._transport.aclose()

    def as_user(self, user_token: str) -> FeishuClient:
        r"""
        返回一个以用户身份（`user_access_token`）发起调用的客户端视图。

        视图复用同一传输层与令牌管理器，但所有请求改用传入的用户令牌，从而让接口按该用户
        自身的权限做访问控制（最小权限），适用于读取用户私有的日历、云文档、云空间文件等。
        应用级（`tenant_access_token`）调用仍通过原客户端发起。

        视图不持有传输层生命周期：对视图调用 [feishu.client.FeishuClient.aclose][] 不会关闭
        共享的底层连接（由原客户端负责释放）。视图通过每次调用显式创建，不存在跨调用共享的
        可变令牌状态，因此与应用级调用并发安全。

        Args:
            user_token: 用户访问令牌（`user_access_token`，以 `u-` 开头），不能为空。

        Returns:
            绑定该用户令牌的客户端视图，其各命名空间方法均以该用户身份发起请求。

        Raises:
            ValueError: 当 `user_token` 为空时抛出。

        Examples:
            >>> me = client.as_user("u-xxxx")  # doctest: +SKIP
            >>> await me.calendar.calendars.primary()  # doctest: +SKIP
            {'calendars': [...]}
        """
        if not user_token:
            raise ValueError("as_user requires a non-empty user_access_token")
        scoped = copy.copy(self)
        scoped._user_token = user_token
        scoped._owns_transport = False
        # Reset lazy namespace caches so they rebind to the scoped view (user token).
        for slot in _NAMESPACE_SLOTS:
            setattr(scoped, slot, None)
        return scoped

    async def download(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        token_type: str | None = "tenant",
        token: str | None = None,
    ) -> bytes:
        r"""
        下载二进制资源（图片、文件等）。

        对 Transport.download 的封装，按照与 request() 相同的令牌优先级规则
        自动注入令牌后转发至底层传输层。

        令牌优先级：显式传入的 token 始终优先；token_type 仅在 token is None 时使用。

        Args:
            path: 接口路径（相对于 Open API 前缀）。
            params: URL 查询参数；值为 None 的项会被自动剔除。
            token_type: 未传入 token 时所获取的令牌类型，如 "tenant"；None 表示匿名。
            token: 显式传入的访问令牌，优先级高于 token_type。

        Returns:
            响应体的原始字节。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取消息中的资源文件](https://open.feishu.cn/document/server-docs/im-v1/message/get-2)

            [feishu.client.FeishuClient.download][]

        Examples:
            >>> import asyncio
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return await client.download(
            ...             "im/v1/messages/om_1/resources/file_k",
            ...             params={"type": "image"},
            ...         )
            >>> asyncio.run(main())  # doctest: +SKIP
            b'...'
        """
        if token is None and token_type is not None:
            # On a user-scoped view (as_user) the user token is the fallback; otherwise fetch
            # the requested app-level token. token_type=None means explicit anonymous on both.
            token = self._user_token or await self._tokens.token(token_type)
        return await self._transport.download("GET", path, params=params, token=token)

    async def paginate_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int | None = None,
        max_items: int | None = None,
        items_key: str = "items",
        token_type: str | None = "tenant",
        map_page: Callable[[NestedDict], NestedDict] | None = None,
    ) -> list[Any]:
        r"""
        遍历飞书分页 GET 接口并将全部条目收集为列表。

        每页在 `params` 基础上追加 `page_token` 翻页，并将 `page_size` 截断至
        [feishu.consts.MAX_PAGE_SIZE][] 以内；值为 `None` 的查询参数由传输层自动剔除。
        `items_key` 指定条目所在的响应字段（默认 `items`，如日历列表使用 `calendar_list`）。
        少数接口使用非标准分页字段时，可通过 `map_page` 将响应信封改写为
        `{"data": {"items": ..., "has_more": ..., "page_token": ...}}` 形态。

        Args:
            path: 接口路径（相对于 Open API 前缀）。
            params: 附加的静态查询参数。
            page_size: 每页条数；为 `None` 时不发送该参数（使用接口默认值）。
            max_items: 最多返回的条目数；为 `None` 表示返回全部。
            items_key: 条目所在的响应字段名。
            token_type: 令牌类型，默认 `tenant`。
            map_page: 可选的信封改写函数，用于归一化使用非标准字段名的分页响应。

        Returns:
            汇总后的条目列表。

        飞书文档:
            [调用流程](https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/overview)
        """

        async def fetch(page_token: str | None) -> NestedDict:
            page_params: dict[str, Any] = dict(params or {})
            if page_size is not None:
                page_params["page_size"] = min(page_size, MAX_PAGE_SIZE)
            page_params["page_token"] = page_token
            envelope = await self.request("GET", path, params=page_params, token_type=token_type)
            return map_page(envelope) if map_page is not None else envelope

        return await paginate(fetch, max_items=max_items, items_key=items_key)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        token_type: str | None = "tenant",
        token: str | None = None,
        expect_envelope: bool = True,
        **kwargs: Any,
    ) -> NestedDict:
        r"""
        向飞书开放平台发起一次请求，自动注入令牌并处理重试。

        令牌优先级：显式传入的 `token` 始终优先；`token_type` 仅在 `token is None`
        时决定获取何种令牌（`token_type=None` 表示不获取任何令牌）。即：

        - `token_type=None, token=None`：不携带任何 Bearer（匿名请求）。
        - `token_type=None, token="u"`：携带给定的（用户）Bearer。
        - `token_type="tenant", token="u"`：显式令牌优先，不再获取 tenant 令牌。
        - 用户视图（[feishu.client.FeishuClient.as_user][]）下，`token_type` 非 `None` 时回退为该用户令牌；
          `token_type=None` 仍表示匿名。

        Args:
            method: HTTP 方法，如 `GET` / `POST`。
            path: 接口路径（相对于 Open API 前缀，例如 `im/v1/messages`）。
            params: URL 查询参数；值为 `None` 的项会被自动剔除。
            json: 请求体（将以 JSON 编码）。
            token_type: 当未显式传入 `token` 时需获取的应用级令牌类型，如 `tenant`、`app`；
                为 `None` 表示不获取令牌。如需以用户身份调用，请通过 `token=` 传入 user_access_token，
                或使用 [feishu.client.FeishuClient.as_user][]。
            token: 显式传入的访问令牌，优先级高于 `token_type`。
            expect_envelope: 是否按标准 `{code, msg, data}` 信封解析响应；OAuth 等
                返回裸 `{access_token, ...}` 的接口应设为 `False`。
            **kwargs: 透传给底层传输层的其他参数（如 `headers`）。

        Returns:
            解析后的响应体，类型为 `chanfig.NestedDict`。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [服务端 API 调用流程](https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/overview)

        Examples:
            >>> import asyncio
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return await client.request("GET", "im/v1/messages/om_xxx")
            >>> asyncio.run(main())  # doctest: +SKIP
            {'code': 0, 'msg': 'success', 'data': {...}}
        """
        if token is None and token_type is not None:
            # On a user-scoped view (as_user) the user token is the fallback; otherwise fetch
            # the requested app-level token. token_type=None means explicit anonymous on both.
            token = self._user_token or await self._tokens.token(token_type)
        return await self._transport.request(
            method, path, params=params, json=json, token=token, expect_envelope=expect_envelope, **kwargs
        )

    async def stream_card(
        self,
        tokens: AsyncIterator[str],
        *,
        receive_id: str | None = None,
        receive_id_type: str | None = None,
        reply_to_message_id: str | None = None,
        element_id: str = "md",
        debounce_s: float = 0.25,
        header: str | None = None,
        template: str | None = None,
    ) -> str:
        r"""
        以流式方式逐步更新一张互动卡片（CardKit）。

        先发送一张初始卡片，随后将 `tokens` 产出的文本片段按 `debounce_s` 防抖节流地
        增量写入指定元素，常用于将大模型的流式输出实时渲染到飞书卡片中。可发送为新消息
        （`receive_id`），或以回复形式发送（`reply_to_message_id`，在原消息所在会话内成串显示）。

        Args:
            tokens: 文本片段的可迭代 / 异步可迭代来源（如大模型流式输出）。
            receive_id: 接收方 ID；发新消息时必填，与 `reply_to_message_id` 二者只能取其一。
            receive_id_type: 接收方 ID 类型；为空时按 `receive_id` 前缀自动推断（仅发新消息时适用）。
            reply_to_message_id: 以回复形式发送时的目标消息 `message_id`；提供时 `receive_id` 应留空。
            element_id: 待更新的卡片元素 ID，默认 `md`。
            debounce_s: 流式更新的防抖间隔（秒），默认 0.25。
            header: 卡片标题配置。
            template: 卡片模板配置。

        Returns:
            创建出的卡片实体 `card_id`。

        Raises:
            ValueError: 当 `receive_id` 与 `reply_to_message_id` 未恰好提供其一时抛出。

        飞书文档:
            [流式更新文本](https://open.feishu.cn/document/cardkit-v1/card-element/content)

        Examples:
            >>> import asyncio
            >>> async def tokens():
            ...     for chunk in ("Hello", " ", "world"):
            ...         yield chunk
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return await client.stream_card(tokens(), receive_id="ou_xxx")
            >>> asyncio.run(main())  # doctest: +SKIP
            'card_xxx'
        """
        from .streaming.cardkit import stream_card as _stream_card

        return await _stream_card(
            self,
            tokens,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            reply_to_message_id=reply_to_message_id,
            element_id=element_id,
            debounce_s=debounce_s,
            header=header,
            template=template,
        )

    async def upload(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        token_type: str | None = "tenant",
        token: str | None = None,
    ) -> NestedDict:
        r"""
        以 multipart/form-data 方式上传文件（素材、云空间文件等）。

        对 Transport.upload 的封装，按照与 request() 相同的令牌优先级规则自动注入令牌后
        转发至底层传输层。表单字段经 data 传入、文件经 files 传入，由 httpx 负责设置
        multipart 边界，调用方不应自行指定 Content-Type。

        令牌优先级：显式传入的 token 始终优先；token_type 仅在 token is None 时使用。

        Args:
            path: 接口路径（相对于 Open API 前缀）。
            data: multipart 表单字段（普通字段）。
            files: multipart 文件字段，形如 {"file": bytes} 或 httpx 支持的元组形式。
            token_type: 未传入 token 时所获取的令牌类型，如 "tenant"；None 表示匿名。
            token: 显式传入的访问令牌，优先级高于 token_type。

        Returns:
            解析后的响应体，类型为 chanfig.NestedDict。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [上传文件](https://open.feishu.cn/document/server-docs/docs/drive-v1/upload/upload_all)

        Examples:
            >>> import asyncio
            >>> async def main():
            ...     async with FeishuClient("cli_xxx", "secret") as client:
            ...         return await client.upload(
            ...             "drive/v1/files/upload_all",
            ...             data={"file_name": "a.txt", "parent_type": "explorer", "parent_node": "fld", "size": 3},
            ...             files={"file": b"abc"},
            ...         )
            >>> asyncio.run(main())  # doctest: +SKIP
            {'code': 0, 'msg': 'success', 'data': {'file_token': '...'}}
        """
        if token is None and token_type is not None:
            # On a user-scoped view (as_user) the user token is the fallback; otherwise fetch
            # the requested app-level token. token_type=None means explicit anonymous on both.
            token = self._user_token or await self._tokens.token(token_type)
        return await self._transport.upload(path, data=data, files=files, token=token)

    async def __aenter__(self) -> FeishuClient:
        r"""进入异步上下文管理器，返回客户端自身。"""
        return self

    async def __aexit__(self, *exc: Any) -> None:
        r"""退出异步上下文管理器，调用 [feishu.client.FeishuClient.aclose][] 释放资源。"""
        await self.aclose()
