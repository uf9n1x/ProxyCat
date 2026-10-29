"""出口能力位与选路：明文转发必须跳过只能做 CONNECT 的出口。

免费代理里有一批只肯做 CONNECT，明文 absolute-form 请求会收到 407/403。
它们仍能服务 HTTPS，所以不能整只拉黑，只能按请求形态分别对待。
"""
from __future__ import annotations

from modules.proxy_workers import ProxyWorkerPool

HTTP_OK = "http://2.2.2.2:8080"
CONNECT_ONLY = "http://1.1.1.1:8080"


def _pool_with(*entries) -> ProxyWorkerPool:
    pool = ProxyWorkerPool(capacity=0)
    pool.set_workers(list(entries))
    return pool


def test_require_http_skips_connect_only_exit() -> None:
    """明文请求不能选到只支持 CONNECT 的出口。"""
    pool = _pool_with((CONNECT_ONLY, False), (HTTP_OK, True))
    picked = pool._pick(require_http=True)
    assert picked is not None and picked.url == HTTP_OK


def test_connect_path_still_uses_connect_only_exit() -> None:
    """HTTPS/CONNECT 仍可使用这类出口，容量不能被白白丢掉。"""
    pool = _pool_with((CONNECT_ONLY, False))
    picked = pool._pick()
    assert picked is not None and picked.url == CONNECT_ONLY


def test_plain_url_defaults_to_http_capable() -> None:
    """无能力信息来源（本地文件/远程接口）按支持处理，避免误杀。"""
    pool = _pool_with("http://3.3.3.3:8080")
    picked = pool._pick(require_http=True)
    assert picked is not None and picked.url == "http://3.3.3.3:8080"


def test_runtime_marking_removes_exit_from_plain_selection() -> None:
    """运行期发现 407 后，该出口不再参与明文选路。"""
    pool = _pool_with(CONNECT_ONLY, HTTP_OK)
    pool.mark_http_unsupported(CONNECT_ONLY)

    picks = {pool._pick(require_http=True).url for _ in range(4)}
    assert picks == {HTTP_OK}


def test_require_http_returns_none_when_no_capable_exit() -> None:
    """只有 CONNECT 出口时，明文请求应明确失败而不是硬塞一个必坏的出口。"""
    pool = _pool_with((CONNECT_ONLY, False))
    assert pool._pick(require_http=True) is None
