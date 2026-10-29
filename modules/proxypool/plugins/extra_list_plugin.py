"""
模块名称：modules.proxypool.plugins.extra_list_plugin
功能描述：对 EXTRA_SOURCES 中硬编码的补充公共清单源并发抓取纯文本清单，逐行解析为代理 URL，
          按 ip:port 在批次内去重后返回；单源失败只记日志，不影响其余源。
          与 public_list_plugin 的 12 个源互不重叠：补 TheSpeedX/hookzof/vakhov 的 socks5、
          vakhov 高频验证的 http/socks5、proxyscrape 的 socks5 API、zevtyardt 与 proxyspace 的 http。
职责边界：负责：各源的并发抓取、文本行到代理 URL 的解析、批次内去重、单源条数上限；
          不负责：代理可用性验证与入库（见 application.ingest）、抓取间隔与测试地址
          （由 PluginManager 的插件配置控制）、网络会话与超时/上游代理（由 PluginContext 注入的
          http_client 承担；抓源代理见 [Pool] plugins.fetch_proxy）。
关键依赖：core.interfaces.services（IProxyPlugin、PluginContext）、core.domain.exceptions
          （PluginExecutionException）、core.domain.models.Proxy。
已知限制：
  1. 源清单硬编码在 EXTRA_SOURCES：8 个源（4 个 http、4 个 socks5），增删源必须改代码。
  2. 单源失败只记日志并跳过，非 200 也按失败抛出；全部源失败才抛 PluginExecutionException。
  3. 空行与 # 开头行跳过，行内无 :// 时补该源默认协议，单行解析失败只记 debug 并丢弃。
  4. 去重只覆盖本批次（按 Proxy.from_url 解析出的 ip:port），跨批次与池内重复由 PluginManager 处理。
  5. 单源截断到 MAX_ITEMS_PER_SOURCE 条：hookzof 单文件近 8 万行、zevtyardt 6.4 万行，
     全量灌入会让验证长时间跑满，截断后头部条目通常也是最新被验证过的。
  6. 调度键、配置键与入库来源取文件名 stem（extra_list_plugin）；各源已实测 200 可达
     （2026-10-29，经 fetch_proxy 上游代理）。
"""

import asyncio
from typing import List, NamedTuple

from core.interfaces.services import IProxyPlugin, PluginContext
from core.domain.exceptions import PluginExecutionException
from core.domain.models import Proxy


class _Source(NamedTuple):
    """一个公共清单源：名称用于日志定位，protocol 为该源裸 ip:port 行的默认协议。"""

    name: str
    url: str
    protocol: str


class ExtraListPlugin(IProxyPlugin):
    """补充公共代理清单抓取插件（与 public_list 的源互补，不重叠）。"""

    # 与 public_list_plugin 保持同一上限口径：单轮约 3 分钟，给执行超时留足余量。
    MAX_ITEMS_PER_SOURCE = 3000

    EXTRA_SOURCES: List[_Source] = [
        # socks5 补充：现有源只有 monosans / proxifly 两个 socks5 清单
        _Source(
            "thespeedx-socks5",
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
            "socks5",
        ),
        _Source(
            "hookzof-socks5",
            "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
            "socks5",
        ),
        _Source(
            "vakhov-socks5",
            "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks5.txt",
            "socks5",
        ),
        _Source(
            "proxyscrape-socks5",
            "https://api.proxyscrape.com/v4/free-proxy-list/get"
            "?request=display_proxies&protocol=socks5&proxy_format=ipport&format=text",
            "socks5",
        ),
        # http 补充：vakhov 每小时整体验证一轮，条目质量高于纯聚合清单
        _Source(
            "vakhov-http",
            "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
            "http",
        ),
        _Source(
            "zevtyardt-http",
            "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/http.txt",
            "http",
        ),
        _Source(
            "proxyspace-http",
            "https://proxyspace.pro/http.txt",
            "http",
        ),
        _Source(
            "proxyspace-socks5",
            "https://proxyspace.pro/socks5.txt",
            "socks5",
        ),
    ]

    @property
    def name(self) -> str:
        return "extra_list"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def fetch_proxies(self, context: PluginContext) -> List[str]:
        """并发抓取全部补充源，按 ip:port 去重后返回代理 URL 列表。"""
        logger = context.logger
        logger.info(f"正在从 {len(self.EXTRA_SOURCES)} 个补充代理源并发抓取...")

        tasks = [
            self._fetch_single_source(context, source)
            for source in self.EXTRA_SOURCES
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen_hosts: set = set()
        proxy_list: List[str] = []
        failed_sources = []

        for source, result in zip(self.EXTRA_SOURCES, results):
            if isinstance(result, BaseException):
                failed_sources.append(source.name)
                logger.warning(f"源 {source.name} 抓取失败: {result}")
                continue

            added = 0
            for proxy_url in result:
                try:
                    proxy_obj = Proxy.from_url(proxy_url, source_plugin="extra_list")
                    host_key = f"{proxy_obj.ip}:{proxy_obj.port}"
                except Exception as exc:  # 单行解析失败只丢弃这一行
                    logger.debug(f"解析失败已丢弃: {proxy_url} ({exc})")
                    continue
                if host_key in seen_hosts:
                    continue
                seen_hosts.add(host_key)
                proxy_list.append(proxy_url)
                added += 1

            logger.info(f"源 {source.name} 贡献 {added} 个代理")

        if failed_sources and not proxy_list:
            raise PluginExecutionException(
                f"全部 {len(self.EXTRA_SOURCES)} 个补充源抓取失败: {failed_sources}"
            )

        logger.info(f"extra_list 插件共获取 {len(proxy_list)} 个去重代理")
        return proxy_list

    async def _fetch_single_source(
        self, context: PluginContext, source: _Source
    ) -> List[str]:
        """抓取单个源的纯文本清单，返回带协议的代理 URL 行列表（截断到单源上限）。"""
        response = await context.http_client.get(source.url)
        if response.status != 200:
            raise PluginExecutionException(
                f"源 {source.name} 返回 {response.status}"
            )

        proxy_list: List[str] = []
        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "://" not in line:
                line = f"{source.protocol}://{line}"
            proxy_list.append(line)
            if len(proxy_list) >= self.MAX_ITEMS_PER_SOURCE:
                logger.info(
                    f"源 {source.name} 达到单源上限 {self.MAX_ITEMS_PER_SOURCE}，提前截断"
                )
                break

        return proxy_list
