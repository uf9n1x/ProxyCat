"""抓源客户端的默认代理回落行为。

约定：default_proxy 只服务「抓取插件」这类自身出网的调用方；
验证与转发链路永远显式传代理或直连，不能被默认值带偏。
"""
from __future__ import annotations

import asyncio

from core.domain.models import HttpResponse
from core.infrastructure.http_client import AioHttpClient


def _capture_proxy(client: AioHttpClient) -> dict:
    """替换会话与发送环节，只留下本次请求实际使用的代理值。"""
    captured: dict = {}

    async def fake_get_session(family=None):
        return object()

    async def fake_send(
        session,
        method,
        url,
        total,
        connect_timeout=None,
        probe=None,
        proxy=None,
        **kwargs,
    ):
        captured["proxy"] = proxy
        return HttpResponse(status=200, text="ok", headers={})

    client._get_session = fake_get_session  # type: ignore[method-assign]
    client._send = fake_send  # type: ignore[method-assign]
    return captured


def test_default_proxy_used_when_request_has_none() -> None:
    """插件只传 URL 时应自动套用抓源代理。"""
    client = AioHttpClient(default_proxy="http://127.0.0.1:7890")
    captured = _capture_proxy(client)

    asyncio.run(client.get("https://example.com"))

    assert captured["proxy"] == "http://127.0.0.1:7890"


def test_explicit_proxy_wins() -> None:
    """显式传入的代理优先，避免验证探测被抓源代理劫持。"""
    client = AioHttpClient(default_proxy="http://127.0.0.1:7890")
    captured = _capture_proxy(client)

    asyncio.run(client.get("https://example.com", proxy="http://10.0.0.1:8080"))

    assert captured["proxy"] == "http://10.0.0.1:8080"


def test_no_default_proxy_means_direct() -> None:
    """未配置抓源代理时保持直连（上游默认行为）。"""
    client = AioHttpClient()
    captured = _capture_proxy(client)

    asyncio.run(client.get("https://example.com"))

    assert captured["proxy"] is None


def test_blank_default_proxy_is_normalized_to_none() -> None:
    """配置里留空（空串或空白）等同于不设代理。"""
    client = AioHttpClient(default_proxy="   ")
    captured = _capture_proxy(client)

    asyncio.run(client.get("https://example.com"))

    assert captured["proxy"] is None


def test_trust_env_flag_is_kept() -> None:
    """抓源客户端需要屏蔽系统代理：Windows 注册表代理会覆盖环境变量。"""
    assert AioHttpClient(trust_env=False)._trust_env is False
    assert AioHttpClient()._trust_env is True
