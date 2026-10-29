# 本项目说明（本地衍生版）

这是基于开源项目 ProxyCat（GPL-2.0，V3.0.1 基线）的衍生版本，
仅本机自用，不对外分发。上游的完整手册见 `ProxyCat-Manual/`、`API.md`，本项目只记录**我们改了什么、怎么用**。

## 快速开始

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
.venv/Scripts/python app.py
```

| 项 | 值 |
|---|---|
| 代理入口（HTTP 与 SOCKS5 共用） | `http://<账号>:<密码>@127.0.0.1:8899` |
| 管理面板 | `http://127.0.0.1:5001/web?token=面板令牌` |
| 口令位置 | `config/config.ini` 的 `[Server] token` 与 `[Users]` 段 |
| 代理池数据库 | `modules/proxypool/data/proxies.db` |

> 面板令牌和代理口令都已替换为随机值，见 `config/config.ini`。
> 仓库没有配置远程，配置里的口令随版本库一起保存；**若以后要推送到远端，先把 `config.ini` 从版本控制中摘出去**。

## 相对上游的改动

上游快照保留在基线提交 `326b88c`，任何时候都可以这样看我们改了什么：

```bash
git diff 326b88c -- modules/proxyserver.py     # 看单个文件的改动
git log --oneline 326b88c..HEAD                # 看我们的提交
```

### 1. 抓源经上游代理出网（`plugins.fetch_proxy`）

上游的抓取插件用 aiohttp 默认会话出网。在 Windows 上 `urllib.getproxies()` 会优先读**注册表里的系统代理**，
环境变量被覆盖，结果就是「明明配了代理，源站还是全超时」——本机实测上游 12 个 GitHub 源全军覆没。

- 新增配置项 `[Pool] plugins.fetch_proxy`，例：`http://127.0.0.1:7890`
- 抓源使用**独立**的 HTTP 客户端（显式代理 + `trust_env=False`），与验证/转发不共用会话：
  验证必须看到出口的真实连通性，一旦被抓源代理带偏，整池结论都不可信
- 配置层会校验地址格式（必须带协议前缀和端口），填错在保存时就拒绝
- 改动文件：`modules/proxypool/core/config.py`、`core/infrastructure/http_client.py`、
  `core/application/app.py`、`modules/pool_config_ini.py`、`modules/modules.py`

### 2. 抓源源清单重建（`public_list_plugin`）

原 `github_proxy_plugin` 的 12 个源在本机全部不可达，替换为 10 个实测有产出的源
（GitHub raw 与公开 API 混合，含 2 个 socks5 源），单源上限 2000 条。

- 实测：候选量 40 → **8520**，10 个源全部有产出
- `proxifly` 的 socks5 清单单文件近 8 万行，不截断会让初筛与入库长时间跑满 CPU
- 改动文件：`modules/proxypool/plugins/public_list_plugin.py`（原文件已删除）

### 3. 明文转发按出口能力位选路

池子里每个出口带 `supports_http` 能力位，但**上游转发层从未使用它**：只肯做 CONNECT 的出口
被拿去做 absolute-form 转发，上游回 `407 Proxy Authentication Required`，调用方凭空失败一次。
本机初始池 25 个出口里有 9 个属于这类，明文请求约一半会撞上。

- `ProxyWorker` 增加 `supports_http`；明文选路新增 `require_http` 过滤，CONNECT 不受影响（不丢容量）
- 运行期遇到 407 就地降级该出口的明文能力并换出口重试，池子自我收敛
- 实测：明文成功 4/8 → **20/20**（0 次 407），HTTPS **20/20**
- 改动文件：`modules/proxy_workers.py`、`modules/proxyserver.py`

## 开发约定

- Windows + Git Bash + uv；Python 3.12
- 所有新增函数写中文文档字符串
- 改完必须跑：`.venv/Scripts/python -m pytest`（当前 26 项）
- 上游本身没有测试，我们只对**自己改动/新增**的部分建测试，逐步扩大覆盖面
- 一片改动一次提交，提交信息写清「为什么改」

## 待办

- [ ] **面板大改**：界面是上游样式，后续按自己的审美重做（用户明确要求，暂不动）
- [ ] 监听地址收敛：代理与面板目前都监听 `0.0.0.0`（局域网可访问），改为可配置并默认 `127.0.0.1`
- [ ] 验证参数调优：`validator.timeout_seconds=5` 对免费代理偏紧，入池率约 3%，可结合失败原因分布调整
- [ ] 池容量：当前 `exit_count` 偏小，可调大以提升并发承载力
