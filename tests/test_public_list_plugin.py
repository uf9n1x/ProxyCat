"""公共清单抓取插件：解析、去重、单源上限与失败语义。

插件在生产环境由 PluginManager 按文件路径动态加载（文件名 stem 即插件键），
这里用同样的方式加载，避免测试跑在与生产不同的导入路径上。
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Dict

import pytest

from core.domain.exceptions import PluginExecutionException
from core.domain.models import HttpResponse
from core.interfaces.services import PluginContext

PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "modules"
    / "proxypool"
    / "plugins"
    / "public_list_plugin.py"
)


def _load_plugin_module():
    """按生产同款方式（spec_from_file_location）加载插件模块。"""
    spec = importlib.util.spec_from_file_location(
        "public_list_plugin_under_test", PLUGIN_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plugin_module():
    return _load_plugin_module()


class StubClient:
    """按 URL 返回预置响应；未登记的源一律返回空正文。"""

    def __init__(self, responses: Dict[str, object] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[str] = []

    async def get(self, url: str, proxy=None, **kwargs) -> HttpResponse:
        self.calls.append(url)
        value = self._responses.get(url)
        if value is None:
            return HttpResponse(status=200, text="", headers={})
        if isinstance(value, Exception):
            raise value
        return value


def _context(client: StubClient) -> PluginContext:
    return PluginContext(
        http_client=client,
        logger=logging.getLogger("test.public_list"),
        config=None,
    )


def _urls(plugin_module, indexes: list[int]) -> list[str]:
    return [plugin_module.PublicListPlugin.SOURCES[i].url for i in indexes]


def _first_url_of(plugin_module, protocol: str) -> str:
    """按协议取第一个源的地址：源清单会增删，用协议定位比用下标稳。"""
    for source in plugin_module.PublicListPlugin.SOURCES:
        if source.protocol == protocol:
            return source.url
    raise AssertionError(f"源清单里没有 {protocol} 源")


def test_parses_lines_and_dedups_by_host_port(plugin_module) -> None:
    """裸 ip:port 按源协议补全；跨源重复的 ip:port 只保留一条。"""
    plugin = plugin_module.PublicListPlugin()
    http_url = _first_url_of(plugin_module, "http")
    socks_url = _first_url_of(plugin_module, "socks5")
    client = StubClient(
        {
            http_url: HttpResponse(
                status=200, text="1.1.1.1:8080\n2.2.2.2:8081\n", headers={}
            ),
            socks_url: HttpResponse(
                status=200, text="3.3.3.3:1080\n1.1.1.1:8080\n", headers={}
            ),
        }
    )

    result = asyncio_run(plugin.fetch_proxies(_context(client)))

    assert sorted(result) == sorted(
        ["http://1.1.1.1:8080", "http://2.2.2.2:8081", "socks5://3.3.3.3:1080"]
    )


def test_per_source_cap(plugin_module) -> None:
    """单源超过上限即截断：免费清单动辄数万行，全量灌入会拖垮初筛。"""
    plugin = plugin_module.PublicListPlugin()
    cap = plugin_module.PublicListPlugin.MAX_ITEMS_PER_SOURCE
    lines = "\n".join(f"10.0.{i // 256}.{i % 256}:8080" for i in range(cap + 300))
    client = StubClient(
        {_urls(plugin_module, [0])[0]: HttpResponse(status=200, text=lines, headers={})}
    )

    result = asyncio_run(plugin.fetch_proxies(_context(client)))

    assert len(result) == cap


def test_partial_failure_keeps_other_sources(plugin_module) -> None:
    """单源失败只丢自己，其余源照常返回。"""
    plugin = plugin_module.PublicListPlugin()
    good_url, bad_url = _urls(plugin_module, [0, 1])
    client = StubClient(
        {
            good_url: HttpResponse(status=200, text="1.1.1.1:8080\n", headers={}),
            bad_url: OSError("源站不可达"),
        }
    )

    result = asyncio_run(plugin.fetch_proxies(_context(client)))

    assert result == ["http://1.1.1.1:8080"]


def test_all_sources_failing_raises(plugin_module) -> None:
    """全部源失败必须抛异常，让调度层记一次失败并重试，而不是静默返回空。"""
    plugin = plugin_module.PublicListPlugin()
    every_url = _urls(plugin_module, list(range(len(plugin_module.PublicListPlugin.SOURCES))))
    client = StubClient({url: OSError("全部不可达") for url in every_url})

    with pytest.raises(PluginExecutionException):
        asyncio_run(plugin.fetch_proxies(_context(client)))


def test_non_200_is_treated_as_failure(plugin_module) -> None:
    """源站返回 403/404 时不能当成功解析，否则空正文会掩盖真实故障。"""
    plugin = plugin_module.PublicListPlugin()
    every_url = _urls(plugin_module, list(range(len(plugin_module.PublicListPlugin.SOURCES))))
    client = StubClient(
        {url: HttpResponse(status=403, text="forbidden", headers={}) for url in every_url}
    )

    with pytest.raises(PluginExecutionException):
        asyncio_run(plugin.fetch_proxies(_context(client)))


def test_source_list_is_wellformed(plugin_module) -> None:
    """源清单自身的质量门：全 https、协议合法、无重复 URL。"""
    sources = plugin_module.PublicListPlugin.SOURCES
    assert len(sources) >= 8
    urls = [source.url for source in sources]
    assert len(set(urls)) == len(urls)
    for source in sources:
        assert source.url.startswith("https://"), source.url
        assert source.protocol in {"http", "socks5"}, source.protocol


def asyncio_run(coro):
    """在同步测试里跑协程，避免为单个用例引入 pytest-asyncio 依赖。"""
    import asyncio

    return asyncio.run(coro)
