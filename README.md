# CamoPool

[![CI](https://github.com/xihan123/CamoPool/actions/workflows/ci.yml/badge.svg)](https://github.com/xihan123/CamoPool/actions/workflows/ci.yml)
[![GHCR](https://img.shields.io/badge/GHCR-ghcr.io%2Fxihan123%2Fcamopool-2496ED?logo=docker&logoColor=white)](https://github.com/xihan123/CamoPool/pkgs/container/camopool)
[![Python](https://img.shields.io/badge/Python-3.10--3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/xihan123/CamoPool)](./LICENSE)

CamoPool 是运行在 Docker 中的 Camoufox Playwright BrowserServer 池。客户端通过 HTTP API 申请独占租约，再使用原生 Playwright WebSocket 协议直接连接浏览器。

```python
browser = playwright.firefox.connect(ws_endpoint)
```

Camoufox 使用 Firefox 内核，因此必须调用 `playwright.firefox.connect(...)`。

## 功能特性

- 每个租约独占一个 Camoufox BrowserServer，可创建多个 Context 和 Page。
- 租约释放或超时后终止 BrowserServer，清除 Cookie、缓存和页面状态。
- 支持冷启动与可配置热池，在资源占用和首次连接延迟之间取舍。
- 根据目标域名选择微信、快手、小红书、百度、头条等平台 UA。
- 控制 API 与 Playwright WebSocket 数据通道分离。
- 支持固定代理、GeoIP 对齐和多节点亲和负载均衡。

## 工作流程

```text
客户端 ── POST /v1/leases ──> HTTP API :8000
客户端 <──── ws_endpoint ──── CamoPool
客户端 ── Playwright WS ────> WS 入口 :53333 ──> 独占 BrowserServer
客户端 ── DELETE lease ─────> CamoPool 回收 BrowserServer
```

## 快速开始

复制环境变量示例：

```bash
cp .env.example .env
```

至少修改以下配置：

```dotenv
CAMOPOOL_API_KEY=replace-with-a-long-random-secret
CAMOPOOL_PUBLIC_HOST=服务器IP或域名
```

拉取 GHCR 镜像并启动：

```bash
docker compose pull
docker compose up -d
docker compose logs -f camopool
```

Compose 直接使用 `ghcr.io/xihan123/camopool:latest`，不会在部署机器上构建镜像。
镜像工作流每次运行都会从 Camoufox 官方仓库 `main` 分支刷新依赖，并使用测试通过后的同一份锁文件构建镜像。

| 端口 | 用途 |
| --- | --- |
| `8000` | 租约控制 API、Swagger UI、健康接口 |
| `53333` | Playwright WebSocket 统一入口 |

## Python 客户端

客户端与服务端必须使用相同主次版本的 Playwright：

```bash
pip install playwright==1.61.0
```

```python
import json
import urllib.request

from playwright.sync_api import sync_playwright

api = "http://localhost:8000"
api_key = "replace-with-a-long-random-secret"
target = "https://example.com/"

request = urllib.request.Request(
    f"{api}/v1/leases",
    data=json.dumps({"url": target}).encode(),
    headers={"Content-Type": "application/json", "X-API-Key": api_key},
)
lease = json.load(urllib.request.urlopen(request))

try:
    with sync_playwright() as playwright:
        browser = playwright.firefox.connect(lease["ws_endpoint"], timeout=30_000)
        page = browser.new_page()
        page.goto(target)
        print(page.title())
        browser.close()
finally:
    urllib.request.urlopen(
        urllib.request.Request(
            f"{api}/v1/leases/{lease['lease_id']}",
            method="DELETE",
            headers={"X-API-Key": api_key},
        )
    )
```

完整示例见 [examples/client.py](./examples/client.py)。`browser.close()` 只关闭客户端连接，不会释放 HTTP 租约；客户端仍需调用 `DELETE /v1/leases/{lease_id}`。异常退出的租约会在 TTL 到期后自动回收。

长任务可通过 `POST /v1/leases/{lease_id}/renew` 续期。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/leases` | 申请独占 BrowserServer |
| `GET` | `/v1/leases/{lease_id}` | 查询租约 |
| `POST` | `/v1/leases/{lease_id}/renew` | 续期租约 |
| `DELETE` | `/v1/leases/{lease_id}` | 释放租约并回收 BrowserServer |
| `GET` | `/v1/pool/stats` | 查询池状态 |
| `GET` | `/health/live` | API 进程存活检查 |
| `GET` | `/health/ready` | API 与 WebSocket 入口就绪检查 |

- Swagger UI：`http://localhost:8000/docs`
- ReDoc：`http://localhost:8000/redoc`
- 完整文档：[docs/API.md](./docs/API.md)

## 平台配置

申请租约时传入 `url`，服务会按域名自动选择平台 UA；也可以显式传入 `platform`。

```json
{
  "url": "https://mp.weixin.qq.com/",
  "platform": "auto"
}
```

可选值为 `auto`、`generic`、`wechat`、`kuaishou`、`douyin`、`xiaohongshu`、`baidu` 和 `toutiao`。`url` 仅用于选择平台配置，控制 API 不会访问目标页面。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CAMOPOOL_POOL_SIZE` | `2` | BrowserServer 最大数量 |
| `CAMOPOOL_WARM_POOL_SIZE` | `0` | 空闲时保留的热 BrowserServer 数 |
| `CAMOPOOL_PUBLIC_HOST` | `localhost` | 返回给客户端的 WS 主机名或 IP |
| `CAMOPOOL_PUBLIC_SCHEME` | `ws` | 公网 WebSocket 协议：`ws` 或 `wss` |
| `CAMOPOOL_PUBLIC_WS_PORT` | `53333` | 返回给客户端的公网 WS 端口；留空则省略端口 |
| `CAMOPOOL_WS_PATH_PREFIX` | 空 | WebSocket 路径前缀，例如 `/ws` |
| `CAMOPOOL_ACQUIRE_TIMEOUT_MS` | `10000` | 满池时等待租约的时间 |
| `CAMOPOOL_LAUNCH_TIMEOUT_MS` | `30000` | BrowserServer 启动超时 |
| `CAMOPOOL_LEASE_TTL_SECONDS` | `300` | 租约有效期 |
| `CAMOPOOL_CAMOUFOX_BROWSER` | `jwriter20/150.0.2-beta.25` | Camoufox 浏览器版本 |
| `CAMOPOOL_API_KEY` | 空 | 控制 API 密钥，生产环境必须设置 |
| `CAMOPOOL_PROXY_SERVER` | 空 | BrowserServer 固定代理地址 |
| `CAMOPOOL_GEOIP` | `true` | 按代理出口 IP 对齐地理信息 |
| `CAMOPOOL_INSTANCE_ID` | 主机名 | 实例标识 |

完整配置见 [.env.example](./.env.example)。

## 反向代理与负载均衡

TLS 应由 Nginx、Caddy 或其他反向代理终止，并将 WebSocket 转发到节点的 `53333` 端口。代理必须支持 WebSocket Upgrade。

多节点部署需要保持客户端亲和，因为租约和 BrowserServer 都属于创建它们的节点：

- HTTP API 与 WebSocket 使用相同的节点列表、顺序和权重。
- 两组 upstream 使用相同的 `ip_hash` 或一致性哈希规则。
- 所有节点使用相同的 `CAMOPOOL_API_KEY`、`CAMOPOOL_PUBLIC_HOST`、`CAMOPOOL_PUBLIC_SCHEME`、`CAMOPOOL_PUBLIC_WS_PORT` 和 `CAMOPOOL_WS_PATH_PREFIX`。
- 每个节点设置唯一的 `CAMOPOOL_INSTANCE_ID`。
- 使用 TLS 时设置 `CAMOPOOL_PUBLIC_SCHEME=wss`；反向代理使用标准端口时将 `CAMOPOOL_PUBLIC_WS_PORT` 留空。
- 若代理前还有 CDN，需要恢复真实客户端 IP，避免亲和规则基于 CDN 节点地址。

节点故障时已有租约不能迁移，客户端需要重新申请。

## 开发

```powershell
uv sync --python 3.13
uv run camoufox fetch
uv run camopool
```

运行测试：

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q camopool tests examples
```

本地构建镜像：

```bash
docker build -t camopool:local .
```

## 许可证

[MIT](./LICENSE)
