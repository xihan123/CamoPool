# CamoPool BrowserServer API

CamoPool HTTP API 只管理 Camoufox BrowserServer 租约。页面操作通过 Playwright WebSocket 直接发送到租用的 BrowserServer，不经过 HTTP 控制 API。

## 基本信息

- HTTP API：`http://localhost:8000`
- WebSocket 入口：`ws://localhost:53333`，可通过 `CAMOPOOL_WS_PATH_PREFIX=/ws` 配置为 `ws://localhost:53333/ws/...`
- 经过标准端口反向代理时，将 `CAMOPOOL_PUBLIC_WS_PORT` 留空即可返回不带端口的地址
- 服务端 Playwright：`1.61.0`
- Camoufox Python：每次 CI 构建从官方仓库 `main` 分支解析最新提交
- Camoufox Browser：默认 `jwriter20/150.0.2-beta.25`
- 浏览器类型：`firefox`
- 鉴权 Header：`X-API-Key`

远程客户端必须使用相同的 Playwright `1.61.x` 版本，并通过：

```python
browser = playwright.firefox.connect(ws_endpoint)
```

## POST /v1/leases

申请一个独占 BrowserServer。

### 请求

```json
{
  "url": "https://mp.weixin.qq.com/",
  "platform": "auto"
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `url` | HTTP URL/null | 否 | `null` | 只用于自动选择平台 UA，服务端不会访问该地址 |
| `platform` | string | 否 | `auto` | 显式平台优先于 URL 识别 |

`platform` 可选值：`auto`、`generic`、`wechat`、`kuaishou`、`douyin`、`xiaohongshu`、`baidu`、`toutiao`。

### 响应

状态码：`201 Created`

```json
{
  "lease_id": "bf4012ee6f9e45e79852f40ac9251317",
  "ws_endpoint": "ws://localhost:53333/7bc0fe20796b99d900ae00f8d7f1d7ad",
  "browser_type": "firefox",
  "playwright_version": "1.61.0",
  "camoufox_version": "0.5.2",
  "browser_version": "150.0.2-beta.25",
  "platform": "wechat",
  "user_agent": "Mozilla/5.0 ... MicroMessenger/8.0.56 ...",
  "expires_at": "2026-07-11T11:30:00Z"
}
```

### 连接

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.firefox.connect(lease["ws_endpoint"], timeout=30_000)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://mp.weixin.qq.com/")
    browser.close()
```

`browser.close()` 不会释放 HTTP 租约。使用完毕后仍需调用 DELETE。

## GET /v1/leases/{lease_id}

查询当前租约和到期时间。成功响应与申请租约相同。

- 找到：`200`
- 不存在或已过期：`404`

## POST /v1/leases/{lease_id}/renew

将到期时间延长为“当前时间 + `CAMOPOOL_LEASE_TTL_SECONDS`”。成功响应与申请租约相同。

长任务应在剩余 TTL 足够大时续期。续期失败后不应继续依赖现有 WebSocket。

## DELETE /v1/leases/{lease_id}

释放租约，终止并按相同平台重新启动对应 BrowserServer。

- 成功：`204 No Content`
- 租约不存在：`404`

重启完成后端口会回到池中，但 WS 随机路径会改变。旧地址不能重连。

## GET /v1/pool/stats

```json
{
  "started": true,
  "instance_id": "camopool-1",
  "servers": 8,
  "connected_servers": 3,
  "warm_servers": 1,
  "leased": 2,
  "available": 6,
  "starting_servers": 0,
  "warm_pool_size": 1,
  "base_port": 53400,
  "restarts": 14
}
```

| 字段 | 说明 |
| --- | --- |
| `started` | 池是否启动 |
| `instance_id` | 当前 CamoPool 实例标识 |
| `servers` | 最大 BrowserServer 容量 |
| `connected_servers` | 当前存活且已获得 WS 地址的 Server 数 |
| `warm_servers` | 当前存活且未出租的热 Server 数 |
| `leased` | 活跃租约数 |
| `available` | 可分配槽位数，包含需要冷启动的槽位 |
| `starting_servers` | 正在申请或回收中的槽位数 |
| `warm_pool_size` | 配置的空闲热 Server 下限 |
| `base_port` | 第一个 BrowserServer 内部端口 |
| `restarts` | 隔离回收、平台切换或故障导致的重启次数 |

## 健康检查

### GET /health/live

只检查 HTTP API 进程：

```json
{"status":"ok"}
```

### GET /health/ready

响应结构与池状态相同。控制 API 与 WebSocket 入口完成启动时返回 `200`，否则返回 `503`。探针不会因为池满、热实例被租用或浏览器冷启动而把承载活跃租约的节点摘除。

## 鉴权

设置 `CAMOPOOL_API_KEY` 后，所有 `/v1/*` 接口必须携带：

```http
X-API-Key: your-secret
```

`/health/*` 不校验 API Key。

API Key 只保护控制 API。BrowserServer WS 地址依靠随机路径标识，生产环境仍应通过防火墙、专网或反向代理限制统一 WS 端口访问。

## 错误响应

```json
{
  "detail": "browser server pool is at capacity"
}
```

| 状态码 | 场景 |
| --- | --- |
| `401` | API Key 缺失或错误 |
| `404` | 租约不存在或已被 TTL 回收 |
| `422` | 请求 JSON、URL 或平台枚举错误 |
| `503` | 池满、BrowserServer 启动失败或就绪检查失败 |

池满时响应包含：

```http
Retry-After: 1
```

## 租约超时

租约到期后控制面会：

1. 从活跃租约表删除租约。
2. 终止 BrowserServer 及其浏览器子进程。
3. 使用同一平台配置重新启动 Server。
4. 将槽位放回可用队列。

该过程会断开仍连接的 WebSocket，并使旧 `ws_endpoint` 永久失效。

## 版本兼容

Playwright `BrowserType.connect` 要求客户端与启动 BrowserServer 的 Playwright 主次版本一致。本项目固定：

```text
playwright==1.61.0
```

客户端应使用同一版本。

## 浏览器版本与兼容

CamoPool 默认使用 JWriter20 `150.0.2-beta.25`。

- v150 的 Juggler schema 仍不接受 Playwright 1.61 的 `isMobile` 和 `contrast` 字段，CamoPool 会在 Node 内存加载 coreBundle 时移除它们。
- v152+ 不加载兼容钩子，原生使用新版协议字段。
- Playwright 安装文件不会被修改；补丁源码不匹配时 BrowserServer 会启动失败。

浏览器版本通过 `CAMOPOOL_CAMOUFOX_BROWSER` 配置，并在每次租约响应的 `browser_version` 字段中返回。v150 上 `is_mobile` 和 `contrast` 可以调用，但不会产生新的浏览器语义。

## 连接参数

Playwright 1.61 客户端可以使用：

```python
browser = playwright.firefox.connect(
    lease["ws_endpoint"],
    timeout=30_000,
    headers={"X-Trace-Id": "optional"},
    # expose_network="<loopback>",  # 仅在远程浏览器需要客户端本机网络时开启
)
```

`expose_network` 会扩大远程浏览器可访问的网络范围，应使用最小规则，不能无条件设为 `*`。
