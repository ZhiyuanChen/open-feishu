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

from functools import wraps
from typing import Callable, List, Mapping, Optional

from . import variables


def authorize(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Check if token, app_id, and app_secret are provided in kwargs
        token = kwargs.get("token")
        app_id = kwargs.get("app_id")
        app_secret = kwargs.get("app_secret")

        # If not provided, raise an error or fetch them from somewhere else
        if token is None:
            if app_id is None:
                if not hasattr(variables, "APP_ID"):
                    raise ValueError("app_id is not provided and not found in variables")
                app_id = kwargs["app_id"] = variables.APP_ID
            if app_secret is None:
                if not hasattr(variables, "APP_SECRET"):
                    raise ValueError("app_secret is not provided and not found in variables")
                app_secret = kwargs["app_secret"] = variables.APP_SECRET

        # Call the original function with provided authorization details
        return func(*args, **kwargs)

    return wrapper


def pagination(
    page_size: int = 50, max_pages: Optional[int] = None, condition: Optional[Callable[[List[Mapping]], bool]] = None
) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(
            env: str,
            space_id: str,
            parent_node_token: str,
            *args,
            **kwargs,
        ) -> Mapping:
            all_items = []
            page_counter = 0
            page_token = None

            while True:
                response = func(
                    env,
                    space_id,
                    parent_node_token,
                    page_size=page_size,
                    page_token=page_token,
                    *args,  # noqa: B026
                    **kwargs,
                )
                items = [i for i in response.data["items"] if i["obj_type"] == "docx"]
                all_items.extend(items)

                page_counter += 1
                if max_pages and page_counter >= max_pages:
                    break

                if condition and condition(all_items):
                    break

                page_token = response.data.get("page_token")
                if not page_token:
                    break

            response.data["items"] = all_items

            return response

        return wrapper

    return decorator
