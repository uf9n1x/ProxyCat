"""抓源代理（plugins.fetch_proxy）的配置解析与校验测试。

这一层是「抓源能不能出网」的总开关，配置错了会在运行时表现为「所有源都抓不到」，
排障成本极高，因此格式校验必须在这里拦住。
"""
from __future__ import annotations

import pytest

from modules.pool_config_ini import (
    PoolConfigError,
    dump_pool_config,
    parse_pool_section,
)


def test_default_is_direct_connection() -> None:
    """不配置时抓源直连，行为与上游一致。"""
    config = parse_pool_section({})
    assert config.plugins.fetch_proxy == ""


def test_fetch_proxy_is_parsed() -> None:
    """配置了 http 代理后能原样读出。"""
    config = parse_pool_section({"plugins.fetch_proxy": "http://127.0.0.1:7890"})
    assert config.plugins.fetch_proxy == "http://127.0.0.1:7890"


def test_fetch_proxy_accepts_socks() -> None:
    """抓源同样允许 socks5，便于把抓取流量丢进统一出口。"""
    config = parse_pool_section({"plugins.fetch_proxy": "socks5://127.0.0.1:1080"})
    assert config.plugins.fetch_proxy == "socks5://127.0.0.1:1080"


def test_empty_string_means_direct() -> None:
    """显式留空等同于直连，不应报错。"""
    config = parse_pool_section({"plugins.fetch_proxy": ""})
    assert config.plugins.fetch_proxy == ""


@pytest.mark.parametrize(
    "bad_value",
    [
        "127.0.0.1:7890",          # 漏协议前缀，运行时必然连不上
        "http://127.0.0.1",        # 缺端口
        "socks5://:1080",          # 缺主机
        "7890",                     # 只有端口
        "ftp://127.0.0.1:21",      # 不支持的协议
    ],
)
def test_invalid_fetch_proxy_rejected(bad_value: str) -> None:
    """非法代理地址在保存时就拒绝，而不是等抓取时全军覆没。"""
    with pytest.raises(PoolConfigError):
        parse_pool_section({"plugins.fetch_proxy": bad_value})


def test_fetch_proxy_survives_dump() -> None:
    """dump 必须带上该项，否则面板保存一次就会把它清掉。"""
    config = parse_pool_section({"plugins.fetch_proxy": "http://127.0.0.1:7890"})
    dumped = dump_pool_config(config)
    assert dumped["plugins.fetch_proxy"] == "http://127.0.0.1:7890"
