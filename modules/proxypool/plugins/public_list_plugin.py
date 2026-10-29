"""
模块名称：modules.proxypool.plugins.public_list_plugin
功能描述：对 SOURCES 中硬编码的公共代理清单源并发抓取纯文本清单，逐行解析为代理 URL，
          按 ip:port 在批次内去重后返回；单源失败只记日志，不影响其余源。
          前身为上游的 github_proxy_plugin：原 12 个源在本机全部不可达（GitHub raw 直连被墙），
          此处替换为实测可用的 GitHub raw + 公开 API 混合清单，并按协议标注来源。
职责边界：负责：各源的并发抓取、文本行到代理 URL 的解析、批次内去重、单源条数上限；
          不负责：代理可用性验证与入库（见 application.ingest）、抓取间隔与测试地址
          （由 PluginManager 的插件配置控制）、网络会话与超时/上游代理（由 PluginContext 注入的
          http_client 承担；抓源代理见 [Pool] plugins.fetch_proxy）。
关键依赖：core.interfaces.services（IProxyPlugin、PluginContext）、core.domain.models.Proxy；
          core.domain.exceptions（PluginExecutionException）。
已知限制：
  1. 源清单硬编码在 SOURCES：10 个源（8 个 http、2 个 socks5），增删源必须改代码。
  2. 单源失败只记日志并跳过，非 200 也按失败抛出；返回非空只代表至少一个源有产出，全部源失败才抛
     PluginExecutionException。
  3. 空行与 # 开头行跳过，行内无 :// 时补该源默认协议，单行解析失败只记 debug 并丢弃。
  4. 去重只覆盖本批次（按 Proxy.from_url 解析出的 ip:port），跨批次与池内重复由 PluginManager 处理。
  5. 单源按文件顺序截断到 MAX_ITEMS_PER_SOURCE 条：proxifly 的 socks5 清单单文件近 8 万条，
     全量灌入会让初筛与入库长时间跑满 CPU，且其中绝大多数是重复或陈旧条目。
  6. 调度键、配置键与入库来源取文件名 stem（public_list_plugin）；插件名在面板「代理池来源」中显示。
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


class PublicListPlugin(IProxyPlugin):
    """公共代理清单抓取插件（GitHub raw 与公开 API 混合）。"""

    # 单源最多取多少条。免费清单动辄数万行，且头部条目通常是最新被验证过的，
    # 截断比全量灌入更快进入验证阶段，也避免数据库被垃圾条目撑大。
    # 3000 是在「初筛 4s + 验证 10s」下实测的可控上限：单轮抓取约 3 分钟，
    # 留给 plugins.execution_timeout_seconds 的余量仍然充足。
    MAX_ITEMS_PER_SOURCE = 3000

    SOURCES: List[_Source] = [
        _Source(
            "openproxylist",
            "https://api.openproxylist.xyz/http.txt",
            "http",
        ),
        _Source(
            "proxifly-http",
            "https://raw.githubusercontent.com/proxifly/free-proxy-list/main"
            "/proxies/protocols/http/data.txt",
            "http",
        ),
        _Source(
            "thespeedx-http",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "http",
        ),
        _Source(
            "proxyscrape-http",
            "https://api.proxyscrape.com/v4/free-proxy-list/get"
            "?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
            "http",
        ),
        _Source(
            "monosans-http",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            "http",
        ),
        _Source(
            "mmpx12-http",
            "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
            "http",
        ),
        _Source(
            "clarketm-http",
            "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
            "http",
        ),
        _Source(
            "roosterkid-https",
            "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
            "http",
        ),
        _Source(
            "monosans-socks5",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
            "socks5",
        ),
        _Source(
            "proxifly-socks5",
            "https://raw.githubusercontent.com/proxifly/free-proxy-list/main"
            "/proxies/protocols/socks5/data.txt",
            "socks5",
        ),
        # 以下两个源与上面协议重复，但清单互不重叠，实测合计多出 7500 余条候选；
        # 免费代理死亡率极高，候选基数大一分，可用数就厚一分。
        _Source(
            "openproxylist-https",
            "https://api.openproxylist.xyz/https.txt",
            "http",
        ),
        _Source(
            "proxyscrape-https",
            "https://api.proxyscrape.com/v4/free-proxy-list/get"
            "?request=display_proxies&protocol=https&proxy_format=ipport&format=text",
            "http",
        ),
    ]

    @property
    def name(self) -> str:
        return "public_list"

    @property
    def version(self) -> str:
        return "3.0.0"

    async def fetch_proxies(self, context: PluginContext) -> List[str]:
        """并发抓取全部源，按 ip:port 去重后返回代理 URL 列表。"""
        logger = context.logger
        logger.info(f"正在从 {len(self.SOURCES)} 个公共代理源并发抓取...")

        tasks = [
            self._fetch_single_source(context, source)
            for source in self.SOURCES
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen_hosts: set = set()
        proxy_list: List[str] = []
        failed_sources = []

        for idx, result in enumerate(results):
            source = self.SOURCES[idx]

            if isinstance(result, Exception):
                logger.error(f"源 {source.name} 抓取失败: {result}")
                failed_sources.append(source.name)
                continue

            if not isinstance(result, list):
                logger.warning(f"源 {source.name} 返回非列表类型: {type(result)}")
                continue

            source_count = 0
            for proxy_url in result:
                try:
                    proxy_obj = Proxy.from_url(proxy_url, source_plugin=self.name)
                    host_key = f"{proxy_obj.ip}:{proxy_obj.port}"
                    if host_key not in seen_hosts:
                        seen_hosts.add(host_key)
                        proxy_list.append(proxy_url)
                        source_count += 1
                except ValueError:
                    continue

            logger.info(f"源 {source.name} 贡献 {source_count} 个新代理")

        if failed_sources and len(failed_sources) == len(self.SOURCES):
            raise PluginExecutionException(
                f"公共代理清单插件全部 {len(self.SOURCES)} 个源都抓取失败"
            )

        logger.info(f"公共代理清单插件共获取 {len(proxy_list)} 个去重代理")
        return proxy_list

    async def _fetch_single_source(
        self,
        context: PluginContext,
        source: _Source,
    ) -> List[str]:
        """抓取单个源并解析；超过单源上限即截断。"""
        logger = context.logger
        proxies: List[str] = []

        try:
            response = await context.http_client.get(source.url)

            if response.status != 200:
                raise PluginExecutionException(
                    f"请求源 {source.name} 失败，HTTP 状态码: {response.status}"
                )

            lines = response.text.strip().split("\n")

            for line in lines:
                if len(proxies) >= self.MAX_ITEMS_PER_SOURCE:
                    logger.info(
                        f"源 {source.name} 达到单源上限 {self.MAX_ITEMS_PER_SOURCE} 条，"
                        f"其余行未取"
                    )
                    break

                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if "://" not in line:
                    line = f"{source.protocol}://{line}"

                try:
                    proxy_obj = Proxy.from_url(line, source_plugin=self.name)
                    proxies.append(proxy_obj.proxy_url)
                except ValueError as e:
                    logger.debug(f"解析代理行失败 '{line}': {e}")
                    continue

            logger.info(f"从 {source.name} 获取了 {len(proxies)} 个代理")

        except Exception as e:
            logger.warning(f"抓取源 {source.name} 发生异常: {e}")
            raise

        return proxies
