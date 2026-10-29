# ProxyCat 衍生版 V1.0.0

> 本项目基于 GPL-2.0 协议的开源项目 ProxyCat（V3.0.1 基线）衍生：
> Web 面板与代理池经重构，抓取源与版本线独立演进。

<p align="center">
  <a href="/README.md">简体中文</a>
  ·
  <a href="/README-EN.md">English</a>
</p>

ProxyCat 把短效代理（有效期 1-60 分钟）聚合成一个稳定的本地代理入口：部署一次，下游工具统一走本机的
HTTP / SOCKS5 监听端口，出口由内置代理池自动抓取、验证、轮换与淘汰，无需人工维护。

![项目原理图](./assets/项目原理图.png)

## 核心功能

- **双协议监听**：同一个端口同时接受 HTTP 与 SOCKS5 入站请求；上游支持带认证的 HTTP / HTTPS / SOCKS5 代理。
- **三种代理来源**：`local` 读本地列表、`api` 实时取接口、`pool` 用内置代理池，面板上切换即时生效。
- **多出口并发**：同时维持多个上游出口服务，请求按实时负载分摊；上游按出口 IP 限流时，并发随出口数成倍放大。
- **失败占比淘汰**：出口故障看滑动窗口里的失败占比，把「一次网络抖动」和「这个出口就是坏的」区分开。
- **高峰期自动扩容**：负载顶上来自动加出口，高峰过去自动收回，避免出口数来回震荡。
- **内置代理池**：插件化抓取、先验证再入库、健康评分、离线归属地、自动重验证与自动清理。
- **真实使用反馈**：转发期发现的坏出口回流到池，连续失败达阈值即标记失效，重验证测通后自动恢复。
- **访问控制**：出口账号密码认证，客户端 IP 黑白名单按优先级判定，绕过名单让指定目标直连。
- **Web 管理面板**：代理配置、代理池、访问控制、运行日志四个页签，配置、名单、日志与服务启停一屏完成。

## 界面预览

面板是单页应用，左侧四个页签，深浅主题跟随系统。

| 代理配置 | 代理池 |
|---|---|
| ![代理配置](./assets/screenshots/config-source.png) | ![代理池](./assets/screenshots/pool-manage.png) |
| 来源（本地 / API / 维护池）、监听端口、出口与并发、检测与日志设置。 | 抓取插件与代理一览：协议、真实 IP、归属地、延迟、健康分与匿名度。 |

| 访问控制 | 运行日志 |
|---|---|
| ![访问控制](./assets/screenshots/access.png) | ![运行日志](./assets/screenshots/logs.png) |
| 出口认证账号、IP 黑白名单与代理绕过名单。 | 实时日志、历史文件回看，以及访问统计与逐条明细。 |

## 快速部署

### Docker 部署

```bash
docker compose up -d --build
```

| 端口 | 用途 |
|---|---|
| `1080` | 代理监听端口（HTTP 与 SOCKS5 共用） |
| `5001` | Web 面板端口 |

`config/`、`logs/`、`modules/proxypool/data/` 三个目录 bind mount 到宿主，数据与配置不随容器销毁。
容器以非 root 运行，宿主目录属主不是 UID/GID 1000 时，在 `.env` 里设 `PUID` / `PGID`。

### 源码运行

需要 **Python 3.10 及以上**（3.9 及更早会在导入阶段报 `TypeError`）：

```bash
pip install -r requirements.txt
cp config/config.example.ini config/config.ini   # 按需修改
python3 app.py                                    # Windows 上命令名为 python
```

`app.py` 同时拉起 Web 面板与出口代理服务。

### 验证部署

```bash
# 1. 面板在监听：期望 302（跳转到 /web）
curl -i http://127.0.0.1:5001/

# 2. 代理在转发：在 config.ini [Users] 里加了账号后，期望 200 且请求经过上游代理
curl -x http://用户名:密码@127.0.0.1:1080 https://www.baidu.com -I
```

### 三条注意事项

1. **面板对外必须设 token**：`config.ini` 的 `[Server] token` 留空表示不校验，对外使用前务必设置，
   改完约 1 秒内生效；设置后必须带 `?token=` 才能打开面板。
2. **改了端口要同步映射**：面板允许在线改监听端口与面板端口，`config.ini` 里的实际端口必须与
   `docker-compose.yml` 的端口映射一致，健康检查按实际端口探活。
3. **不要改小容器宽限期**：`stop_grace_period` 已放宽到 60 秒，降到 Docker 默认的 10 秒会在写队列
   排空前被 `SIGKILL`，丢失最后一批验证结果与访问记录。

## 文档导航

| 文档 | 内容 |
|---|---|
| [功能特性](ProxyCat-Manual/Features.md) | 出口代理服务、内置代理池、面板与运维、上游出口与并发机制、对外网络请求、性能基准与项目结构 |
| [详细配置](ProxyCat-Manual/Configuration.md) | `[Server]` / `[Users]` / `[api_credentials]` / `[Pool]` 全部配置项：默认值、说明与生效方式 |
| [使用手册](ProxyCat-Manual/Operation%20Manual.md) | 安装部署、运行方式、面板逐页讲解、代理池使用、日志与访问记录、常见问题 Q&A |
| [报错排查手册](ProxyCat-Manual/Investigation%20Manual.md) | 常见报错的判断与处理 |
| [更新日志](ProxyCat-Manual/logs.md) | 各版本的改动记录 |
| [接口文档](API.md) | 面板 `/api/*` 与代理池 `/api/pool/*` 的参数、响应示例与错误码 |
| [代理池模块文档](modules/proxypool/README.md) | 抓取插件开发、验证策略、匿名度判定、归属地解析、自动清理等实现细节 |

上表每一份文档都有英文版 —— 与中文版同名、加 `-EN` 后缀（如 `ProxyCat-Manual/Features-EN.md`、`API-EN.md`）。

## 开源协议

本项目采用 **GNU General Public License v2.0（GPL-2.0）**，完整条款见 [LICENSE](LICENSE)。
分发修改版或衍生作品时，须同样以 GPL-2.0 授权并提供源码；本软件不提供任何担保。
