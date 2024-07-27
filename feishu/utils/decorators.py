# open-feishu
# Copyright (C) 2024-Present  Zhiyuan Chen

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

from functools import wraps
from inspect import isfunction
from typing import Callable, Optional

from feishu import variables


def flexible_decorator(maybe_decorator: Optional[Callable] = None):
    r"""
    元装饰器，使得装饰器可以在不接受参数时直接使用，也可以接受参数。

    Examples:
        For decorator defined as follows:

        >>> @flexible_decorator
        ... def decorator(*args, **kwargs):
        ...     def wrapper(func, *args, **kwargs):
        ...         pass
        ...     return wrapper

        The following two are equivalent:

        >>> @decorator
        ... def func(*args, **kwargs):
        ...     pass

        >>> @decorator()
        ... def func(*args, **kwargs):
        ...     pass
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if len(args) == 1 and isfunction(args[0]):
                return func(**kwargs)(args[0])
            return func(*args, **kwargs)

        return wrapper

    if maybe_decorator is None:
        return decorator
    return decorator(maybe_decorator)


def authorize(func: Callable) -> Callable:
    r"""
    授权装饰器，用于自动注入 app_id 和 app_secret。
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Check if token, app_id, and app_secret are provided in kwargs
        token = kwargs.get("token")
        app_id = kwargs.get("app_id")
        app_secret = kwargs.get("app_secret")

        # If not provided, raise an error or fetch them from somewhere else
        if token is None:
            if app_id is None:
                if not hasattr(variables, "APP_ID") and variables.APP_ID:
                    raise ValueError("app_id is not provided and not found in variables")
                app_id = kwargs["app_id"] = variables.APP_ID
            if app_secret is None:
                if not hasattr(variables, "APP_SECRET") and variables.APP_SECRET:
                    raise ValueError("app_secret is not provided and not found in variables")
                app_secret = kwargs["app_secret"] = variables.APP_SECRET

        # Call the original function with provided authorization details
        return func(*args, **kwargs)

    return wrapper


@flexible_decorator
def pagination(stop_on_page: Callable | None = None, stop_on_accum: Callable | None = None):
    r"""
    分页装饰器，用于收集分页数据。

    Args:
        stop_on_page: 根据当前分页数据判断是否应该停止分页的函数。
        stop_on_accum: 根据累积数据判断是否应该停止分页的函数。

    注意：
        该装饰器会读取函数参数中的 `stop_on_page` 和 `stop_on_accum` 参数，如果没有提供则使用默认值。
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            _stop_on_page = kwargs.pop("stop_on_page", stop_on_page)
            _stop_on_accum = kwargs.pop("stop_on_accum", stop_on_accum)
            all_items = []
            page_token = None

            while True:
                response = func(*args, **kwargs, page_token=page_token)
                items = response["data"]["items"]
                if _stop_on_page and _stop_on_page(items):
                    break
                all_items.extend(items)
                if _stop_on_accum and _stop_on_accum(all_items):
                    break
                if not response["data"].get("has_more"):
                    break
                page_token = response["data"]["page_token"]

            response["data"]["items"] = all_items
            return response

        return wrapper

    return decorator
