# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

from unittest.mock import MagicMock

from plane.api.base_resource import BaseResource
from plane.config import Configuration


def test_get_uses_shared_transport_contract() -> None:
    resource = BaseResource(
        Configuration(base_path="https://plane.test", api_key="secret", timeout=5),
        "/workspaces",
    )
    response = MagicMock(
        status_code=200,
        content=b'{"id":"item"}',
        headers={"content-type": "application/json"},
    )
    response.json.return_value = {"id": "item"}
    resource.session.get = MagicMock(return_value=response)

    assert resource._get("items", params={"limit": 1}) == {"id": "item"}
    resource.session.get.assert_called_once_with(
        "https://plane.test/api/v1/workspaces/items/",
        headers={"Content-Type": "application/json", "X-Api-Key": "secret"},
        params={"limit": 1},
        timeout=5,
    )
