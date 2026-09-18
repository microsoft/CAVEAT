# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared 24-card pagination helpers for harvested compatibility APIs."""

from __future__ import annotations

from typing import Sequence, TypeVar

from .counting import PAGE_SIZE

T = TypeVar("T")


def slice_page(rows: Sequence[T], limit: int = PAGE_SIZE, offset: int = 0):
    try:
        limit = max(1, min(int(limit), PAGE_SIZE))
    except (TypeError, ValueError):
        limit = PAGE_SIZE
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    return list(rows[offset:offset + limit]), limit, offset


def page_headers(response, *, total: int, limit: int, offset: int) -> None:
    response.headers["X-Storefront-Total"] = str(total)
    response.headers["X-Storefront-Page"] = str(offset // limit + 1)
    response.headers["X-Storefront-Page-Size"] = str(limit)
    response.headers["X-Storefront-Has-Next"] = "1" if offset + limit < total else "0"

