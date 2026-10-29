# ProxyCat Derivative V1.0.0

> Based on the GPL-2.0 open-source project ProxyCat (V3.0.1 baseline).
> The web panel and proxy pool have been reworked; the fetch sources and version line evolve independently.

<p align="center">
  <a href="/README.md">简体中文</a>
  ·
  <a href="/README-EN.md">English</a>
</p>

ProxyCat turns short-lived proxies (valid for 1-60 minutes) into a stable local proxy endpoint: deploy it once,
and your downstream tools all go through a single local HTTP / SOCKS5 listening port while outbound exits are
fetched, validated, rotated and retired automatically by the built-in proxy pool — no manual upkeep required.

![Project Principle](./assets/项目原理图.png)

## Key Features

- **Dual-protocol listening**: a single port accepts both HTTP and SOCKS5 inbound requests; upstreams may be authenticated HTTP / HTTPS / SOCKS5 proxies.
- **Three proxy sources**: `local` reads a local list, `api` fetches from an endpoint live, `pool` uses the built-in proxy pool directly — switching between them on the panel takes effect immediately.
- **Many exits serving at once**: several upstream exits are kept in service simultaneously, and requests are spread by each exit's live load — so when an upstream rate-limits per exit IP, concurrency scales with the number of exits.
- **Retirement by failure share**: an exit's health is judged by the failure share in its sliding window, not by a count of consecutive failures — which is what tells "one network hiccup" apart from "this exit is simply broken".
- **Automatic expansion at peak**: exits are added automatically when load climbs, and released once the peak passes, so the exit count never oscillates.
- **Built-in proxy pool**: plugin-based collection, verify-before-storing, health scoring, offline geolocation, automatic revalidation and cleanup.
- **Real usage feedback**: bad exits found while forwarding flow back into the pool; once consecutive failures reach the threshold the entry is marked invalid, and a passing revalidation restores it automatically.
- **Access control**: username / password authentication on the outbound side, client IP whitelist / blacklist judged by priority, and a bypass list that connects matching targets directly.
- **Web management panel**: four tabs — Proxy Config, Proxy Pool, Access Control and Logs — putting configuration, lists, logs and service start/stop on one screen.

## Screenshots

The panel is a single-page app with four tabs down the left side; the theme follows the system preference.

| Proxy Config | Proxy Pool |
|---|---|
| ![Proxy Config](./assets/screenshots/config-source.png) | ![Proxy Pool](./assets/screenshots/pool-manage.png) |
| Sources (local / API / pool), listening port, exits and concurrency, checks and logging. | Fetch plugins and every collected proxy: protocol, real IP, region, delay, health score and anonymity. |

| Access Control | Logs |
|---|---|
| ![Access Control](./assets/screenshots/access.png) | ![Logs](./assets/screenshots/logs.png) |
| Outbound authentication accounts, the IP whitelist / blacklist and the proxy bypass list. | Live logs and past log files, plus access statistics and per-request detail. |

## Quick Deployment

### Docker

```bash
docker compose up -d --build
```

| Port | Purpose |
|---|---|
| `1080` | Proxy listening port (shared by HTTP and SOCKS5) |
| `5001` | Web panel port |

The host ports default to the table above; to change them, write `PROXY_PORT=8899` / `WEB_PORT=5001`
into a `.env` file next to the compose file (the container-side port comes from `config.ini`, so keep
both sides consistent). If the build is slow or fails, set `PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/`
in `.env` to use a regional PyPI mirror.

`config/`, `logs/` and `modules/proxypool/data/` are bind-mounted to the host, so data and configuration survive
container replacement. The container runs as non-root; if the host directories are not owned by UID/GID 1000,
set `PUID` / `PGID` in `.env`.

### Run from source

Requires **Python 3.10 or later** (3.9 and earlier fail at import time with a `TypeError`):

```bash
pip install -r requirements.txt
cp config/config.example.ini config/config.ini   # edit as needed
python3 app.py                                    # on Windows the command is python
```

`app.py` starts both the web panel and the outbound proxy service.

### Verify the deployment

```bash
# 1. The panel is listening: expect 302 (redirect to /web)
curl -i http://127.0.0.1:5001/

# 2. The proxy is forwarding: after adding an account under [Users] in config.ini,
#    expect 200 with the request actually leaving through an upstream proxy
curl -x http://username:password@127.0.0.1:1080 https://www.baidu.com -I
```

### Three things to know

1. **Set a token before exposing the panel**: an empty `[Server] token` in `config.ini` means no authentication.
   Set it before any external use; it takes effect within about a second, and the panel then requires `?token=`.
2. **Keep port mappings in sync**: the panel allows changing the listening and panel ports online; the actual
   ports in `config.ini` must match `PROXY_PORT` / `WEB_PORT` in `.env` (defaults 1080 / 5001), and the health
   check probes the configured port.
3. **Do not shorten the container grace period**: `stop_grace_period` is relaxed to 60 seconds; the Docker
   default of 10 seconds would `SIGKILL` the app before the write queues drain, losing the last batch of
   validation results and access records.

## Documentation

| Document | Contents |
|---|---|
| [Features](ProxyCat-Manual/Features-EN.md) | Outbound proxy service, built-in proxy pool, panel and operations, upstream exits and concurrency, outbound network requests, benchmarks and project structure |
| [Configuration](ProxyCat-Manual/Configuration-EN.md) | Every `[Server]` / `[Users]` / `[api_credentials]` / `[Pool]` option: defaults, meaning and how changes take effect |
| [Operation Manual](ProxyCat-Manual/Operation%20Manual-EN.md) | Installation, running modes, panel walkthrough, pool usage, logs and access records, FAQ |
| [Investigation Manual](ProxyCat-Manual/Investigation%20Manual-EN.md) | Diagnosing and handling common errors |
| [Changelog](ProxyCat-Manual/logs-EN.md) | Changes in each version |
| [API Reference](API-EN.md) | Panel `/api/*` and pool `/api/pool/*` parameters, response examples and error codes |
| [Proxy Pool Module](modules/proxypool/README-EN.md) | Fetch plugin development, validation strategy, anonymity detection, geolocation, automatic cleanup and other internals |

## License

This project is licensed under the **GNU General Public License v2.0 (GPL-2.0)**; see [LICENSE](LICENSE) for the
full text. Distributing modified versions or derivative works requires licensing them under GPL-2.0 with source
provided. This software comes with no warranty.
