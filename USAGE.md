# 用法手册

本机自用的轮换代理。上游出口来自公开代理清单，程序自动抓取、验证、淘汰、补货，
对外只暴露一个固定地址——你的脚本/工具指过来就行，不用关心下面换了多少出口。

## 一、地址与凭据

| 项目 | 值 |
|---|---|
| HTTP 代理 | `http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899` |
| SOCKS5 代理 | `socks5://<你的代理账号>:<你的代理密码>@127.0.0.1:8899` |
| 管理面板 | <http://127.0.0.1:5001/web?token=<你的面板token>> |

HTTP 与 SOCKS5 **共用一个端口**，程序按首字节自动识别，两种协议都能连。
必须带账号口令：`config/whitelist.txt` 只放行本机 IP，但认证仍然需要。

改动账号口令：编辑 `config/config.ini` 的 `[Auth]` 段，保存即热生效。

## 二、启停

```bash
cd D:/Mytools/Code_editor/All_code_project/other/proxy_pool
.venv/Scripts/python app.py
```

前台运行，`Ctrl+C` 停止。日志在 `logs/`：`access.log` 是每条请求的结果，
`proxy.log` 是出口轮换与重试，`main.log` 是服务启动与配置重载。

想常驻可以挂到任务计划程序，或用 `start /b` 起一个后台进程。

## 三、怎么用

### 1. curl

```bash
curl -x http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899 https://api.ipify.org?format=json
```

SOCKS5（远程解析域名，避免 DNS 泄漏）：

```bash
curl --socks5-hostname 127.0.0.1:8899 --proxy-user <你的代理账号>:<你的代理密码> https://api.ipify.org
```

### 2. Python

`requests`：

```python
import requests

PROXY = "http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899"
proxies = {"http": PROXY, "https": PROXY}
r = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=40)
print(r.json())
```

`httpx`（支持 SOCKS5 需要装 `httpx[socks]`）：

```python
import httpx
with httpx.Client(proxy="http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899", timeout=40) as c:
    print(c.get("https://api.ipify.org?format=json").json())
```

`Playwright`（整页走代理，含页面内所有子请求）：

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(proxy={
        "server": "http://127.0.0.1:8899",
        "username": "<你的代理账号>",
        "password": "<你的代理密码>",
    })
    page = browser.new_page()
    page.goto("https://api.ipify.org?format=json", timeout=60000)
    print(page.content())
```

`Selenium` 用 `--proxy-server=http://127.0.0.1:8899` 启动参数，
但 Chrome 对带认证的代理不弹框，需要额外插件；建议优先用 Playwright 或下面第 4 节的环境变量方式。

### 3. Node.js

```js
// undici（Node 18+ 内置 fetch）
import { ProxyAgent, setGlobalDispatcher } from "undici";
setGlobalDispatcher(new ProxyAgent("http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899"));
const r = await fetch("https://api.ipify.org?format=json");
console.log(await r.json());
```

`axios` 用 `proxy: { host: "127.0.0.1", port: 8899, auth: { username: "<你的代理账号>", password: "<你的代理密码>" } }`。

### 4. 命令行工具（不想改代码）

设环境变量，绝大多数 CLI 会自动读取：

```bash
export HTTP_PROXY="http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899"
export HTTPS_PROXY="http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899"
export NO_PROXY="127.0.0.1,localhost"
```

PowerShell：

```powershell
$env:HTTP_PROXY  = "http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899"
$env:HTTPS_PROXY = "http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899"
$env:NO_PROXY    = "127.0.0.1,localhost"
```

生效范围：`pip install`、`git clone`、`npm install`、`curl`、`wget` 等。
注意这是**当前终端会话**的变量，关窗口即失效；要长期生效得写进系统环境变量。

支持 `--proxy` 参数的工具直接用：

```bash
sqlmap -u "http://target/" --proxy=http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899
nuclei -u https://target -proxy http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899
ffuf -u https://target/FUZZ -w wordlist.txt -x http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899
```

不支持代理的工具（如 `nmap`）用 proxychains 包一层，把它的配置指向本代理即可。

### 5. 验证真的走了代理

```bash
curl -s https://api.ipify.org?format=json              # 直连出口
curl -s -x http://<你的代理账号>:<你的代理密码>@127.0.0.1:8899 https://api.ipify.org?format=json
```

两个 IP 不一样就说明代理生效了。连续多打几次，IP 会变——每次请求都可能换出口。

## 四、面板看什么

打开面板后，「出口」页是核心：

- **在用出口数**：正常应稳定在 60（`exit_count`）。长期低于它就是池子在饿。
- **失败占比 / 请求数**：单个出口失败占比过高会被自动停用并补货，属正常自愈。
- **代理池页**：`有效 / 总数`。有效数持续下滑说明免费源在死，等下一轮抓取
  （每 30 分钟一次）会自动补上；也可以点「立即执行」强制抓一轮。

## 五、排障

| 现象 | 原因与处理 |
|---|---|
| 407 | 客户端没带账号口令。把 `账号:口令@` 写进代理 URL |
| 502 / 504 | 连续几次出口都失败。免费代理本来就有失败率，重试或过几秒再发 |
| 首请求慢（10s+） | 命中了慢出口。连接是持续保温的，通常 1~3s；偶发慢是出口本身的问题 |
| 目标站 403 / 429 | 该站按 IP 封禁或限流。程序会换出口重试一次；仍失败说明这批出口都被拦 |
| 池子变小 | 免费代理半小时死一批。看面板「代理池」页，或降低 `validator.timeout_seconds` 之外的阈值 |
| 端口被占 | `netstat -ano \| grep :8899` 找 PID，确认不是残留进程 |

## 六、必须知道的限制

1. **免费代理不可信**。出口是陌生人的机器，可能记录流量、篡改内容。
   只用它访问公开信息、做压测或练手；**不要登录账号、不要过支付、不要传敏感数据**。
2. **IP 会频繁变**。同一站点连续请求可能来自不同国家，需要登录态的站点会反复掉线
   或弹验证码——这是轮换代理的固有代价，不是故障。
3. **成功率高但不等于 100%**。实测 HTTPS 约 96%，明文约 100%（同机、同一时段）。
   写脚本时把超时设到 40s 以上，并自己加一层重试。
4. **带宽与延迟取决于出口**。免费出口普遍 1~5s 首字节，不适合对延迟敏感的活。

## 七、调参速查（`config/config.ini`）

| 键 | 当前值 | 作用 |
|---|---|---|
| `exit_count` | 60 | 同时保持多少个出口在用 |
| `validator.timeout_seconds` | 10 | 单个代理验证超时，调大入池更多但更慢 |
| `validator.connect_timeout_seconds` | 4.0 | 初筛建连超时 |
| `validator.max_concurrent` | 400 | 验证并发。调到 600 会让三成候选因本机排队被判无效 |
| `plugins.default_interval_minutes` | 30 | 多久抓一轮新代理 |
| `auto_revalidation.interval_minutes` | 30 | 多久复验一次已有代理 |
| `max_concurrent_requests` | 3000 | 网关整体并发上限 |

改完保存即热生效，不需要重启（改 `plugins.*` 之外的抓取代码才需要重启）。
