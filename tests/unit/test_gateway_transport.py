# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

"""Contract tests for the optional compatibility transport seam."""

from collections.abc import Mapping
from typing import Any

from plane import PlaneClient
from plane.api.base_resource import GatewayTransport


class RecordingTransport:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, str, Mapping[str, Any] | None, Mapping[str, Any] | None]] = []

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        data: Mapping[str, Any] | list[Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, endpoint, data if isinstance(data, Mapping) else None, params))
        return self.response


def test_client_passes_the_optional_transport_to_all_resources() -> None:
    transport: GatewayTransport = RecordingTransport(
        {"id": "u1", "display_name": "Ada", "email": "ada@example.com"}
    )

    client = PlaneClient(
        base_url="https://plane.example", api_key="key", gateway_transport=transport
    )

    user = client.users.get_me()

    assert user.id == "u1"
    assert client.config.gateway_transport is transport
    assert transport.calls == [("GET", "/users/me", None, None)]


def test_gateway_transport_receives_normalized_payload_and_query() -> None:
    transport = RecordingTransport({"id": "p1", "name": "Project", "identifier": "PRJ"})
    client = PlaneClient(
        base_url="https://plane.example", api_key="key", gateway_transport=transport
    )

    client.projects.retrieve("ws", "p1")

    assert transport.calls == [("GET", "/workspaces/ws/projects/p1", None, None)]


def test_transport_is_opt_in_and_does_not_replace_the_rest_session() -> None:
    client = PlaneClient(base_url="https://plane.example", api_key="key")

    assert client.config.gateway_transport is None
    assert client.users.session is not None
