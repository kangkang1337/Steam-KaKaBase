# Steam-KaKaBase

一个面向本地运行的 Steam 数据面板，设计参考 SteamDB。用于查看游戏价格与本地历史快照、在线人数趋势、玩家评价、热门榜和每日小众宝藏推荐。

> 当前项目处于本地原型阶段。现有 HTTP 服务适合开发和个人使用，尚未按公网生产环境加固。

## 当前功能

- 搜索 Steam 全量轻量目录，也可以直接输入 App ID。
- 查看中国区价格、多地区价格历史、折扣与 ITAD 中国区史低。
- 查看当前在线人数、本站开始记录后的历史峰值和趋势图。
- 查看 Steam 好评率、评测数量、简介、开发商、发行商和发售日期。
- 在详情页收藏或取消收藏，状态持久化到 SQLite。
- 热门榜默认展示 Steam Top 100，支持仅看付费、仅看史低和按好评率排序。
- 独立维护小众游戏池，并从候选池较强的前 50% 随机抽取最多 20 款展示。
- 首页展示今日史低、每日小众宝藏游戏和今日表情包，统一在本地时间每天 00:10 更新。
- Vue 3 + ECharts 前端，价格折线图和在线人数柱形图支持悬停查看数据。

详情页只加载游戏头图，不再批量下载 Steam 截图或徽章。

## 数据策略

后端采用分层缓存，页面访问只读取 SQLite，不等待外部 Steam 请求：

1. 热门榜基础层：排名、App ID、名称、头图、玩家数和更新时间。
2. 轻量预览层：为优先游戏补中国区价格、免费状态、折扣、好评率和发售日期。
3. 详情层：仅为高优先级或用户打开的游戏补简介、开发商和发行商。
4. 历史层：后台保存价格、玩家数和 ITAD 史低数据。

主要刷新频率：

| 数据 | 频率 |
| --- | --- |
| 在线人数 | 默认每 30 分钟 |
| 价格和评价 | 默认每 24 小时 |
| 热门榜 | 默认每天 |
| Steam 轻量目录 | 每天增量同步 |
| 首页三项推荐 | 每天 00:10 |

Steam 目录 `steam_catalog` 默认最多保存 20,000 条 App ID 和名称。目录 enrich 按每日额度逐步补全，不要求首次启动时等待全部元数据。

## 数据来源

- Steam Web API：热门榜、AppList 轻量目录和当前在线人数。
- Steam Store `appdetails`：名称、头图、简介、发行信息和地区价格。
- Steam Store `appreviews`：好评率和评测数量。
- IsThereAnyDeal `games/lookup/v1` 与 `games/historylow/v1`：Game ID 和地区史低。

价格折线图使用本站按计划保存的 Steam 价格快照，不代表 Steam 官方提供完整历史价格。玩家历史峰值同样只统计本站开始记录后的快照。

## 小众池规则

候选必须满足：

- Steam 返回类型为游戏，排除 DLC 等非游戏内容。
- 近 8 年发行。
- 当前在线人数至少 10。
- 本站历史峰值不超过 2,000。
- 好评率至少 85%，且评测数据有效。

加权分由以下部分组成：

| 指标 | 权重 |
| --- | ---: |
| 好评率 | 45% |
| 评测数量 | 30% |
| 本站历史峰值 | 15% |
| 发行时间 | 10% |

发行时间系数为：0～3 年 `1.00`、4～5 年 `0.95`、6～8 年 `0.85`。候选池最多保留 500 款；首页每日选择和小众池页面展示不会固定只取第一名。

## 后端结构

```text
backend/
├── config.py          环境变量、常量和路径
├── logging_utils.py   日志与轮转入口
├── db.py              SQLite 连接、迁移和任务状态
├── steam_client.py    Steam / ITAD 请求、代理、重试和冷却
├── crawler.py         后台采集与任务编排入口
├── services.py        搜索、详情、榜单、推荐和收藏
├── server.py          HTTP 路由、静态文件、CORS 和 JSON
├── main.py            服务与调度器启动入口
└── _runtime.py        模块拆分期间的私有兼容实现
```

正式入口为 `python -m backend.main`。`python steamkb.py` 作为兼容入口保留。新增后端代码应优先通过公开模块调用，不应继续扩大 `_runtime.py`。

SQLite 开启 WAL 模式，读写可以并行；批量采集按批次提交，避免每抓取一个 App 就提交一次。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 可访问 Steam Web API、Steam Store 和所需图片 CDN 的网络
- ITAD 史低功能需要 IsThereAnyDeal API Key
- Steam AppList 目录同步需要 Steam Web API Key

## 快速启动

安装运行依赖：

```powershell
python -m pip install -r requirements.txt
```

启动后端并打开浏览器：

```powershell
.\start.ps1
```

也可以双击 `start.bat`。默认地址为：

```text
http://127.0.0.1:8765
```

彻底关闭本地服务：

```powershell
.\end.ps1
```

只启动后端：

```powershell
python -m backend.main
```

不要直接双击 `steamkb.html`。页面需要通过本地后端地址打开，否则浏览器无法访问 API。

## 环境配置

复制示例配置：

```powershell
Copy-Item .env.example .env
```

然后在 `.env` 中填写需要的配置。不要提交 `.env`、真实 API Key、数据库或日志。

常用变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `STEAM_API_KEY` | 空 | Steam AppList 使用的 Web API Key |
| `ITAD_API_KEY` | 空 | ITAD lookup 和 historylow API Key |
| `STEAMKB_PORT` | `8765` | 本地 HTTP 端口 |
| `STEAMKB_DB` | `data/steamkb.sqlite3` | SQLite 文件路径 |
| `STEAMKB_LOG` | `data/steamkb.log` | 日志路径 |
| `STEAMKB_PLAYER_REFRESH_MINUTES` | `30` | 在线人数刷新间隔，最小 30 分钟 |
| `STEAMKB_PRICE_REFRESH_HOURS` | `24` | 价格刷新间隔，最小 24 小时 |
| `STEAMKB_HOTLIST_TARGET` | `100` | 本地热门榜目标数量 |
| `STEAMKB_CATALOG_LIMIT` | `20000` | Steam 轻量目录上限 |
| `STEAMKB_CATALOG_ENRICH_DAILY_LIMIT` | `500` | 每日目录 enrich 尝试额度 |
| `STEAMKB_CATALOG_ENRICH_BATCH_LIMIT` | `50` | 单轮 enrich 数量 |
| `STEAMKB_NICHE_POOL_LIMIT` | `500` | 小众候选池上限 |
| `STEAMKB_DIRECT_COOLDOWN_MINUTES` | `5` | 直连失败后的独立冷却时间 |
| `STEAMKB_STORE_DELAY_MIN_SECONDS` | `1.5` | 商店请求随机延迟下限 |
| `STEAMKB_STORE_DELAY_MAX_SECONDS` | `4.0` | 商店请求随机延迟上限 |
| `STEAMKB_HISTORICAL_LOW_TOLERANCE_CNY` | `0.5` | 当前价判定史低时允许的人民币误差 |

代理回退配置：

```env
USE_PROXY=true
STEAMKB_PROXY_URL=http://127.0.0.1:7890
```

请求优先直连。只有明确开启代理且启动探测确认代理可连接时，直连失败或超时才会回退到代理。直连正常时不会经过代理；代理地址不可用时继续采用直连。

## 冷却与重试

以下服务分别维护限流冷却，不会因一个服务返回 `429` 而暂停其他服务：

- `steam_api`：热门榜、AppList、在线人数。
- `steam_store`：商店详情、价格和评价。
- `itad`：Game ID 与史低。
- `image_cdn`：后端图片缓存。

使用统一代理回退层的请求还会按服务维护直连冷却。冷却期间直接使用已确认可用的代理；到期后只放行一次直连探测。前端状态栏和首页监控区会显示冷却服务、剩余时间以及是否正在使用代理回退。

可以通过以下接口查看状态：

```text
GET /api/status
```

其中 `service_cooldowns` 表示接口限流冷却，`direct_service_cooldowns` 表示直连失败冷却。

## 首页与图片

首页三项内容共用 `00:10` 日界线，并保存 SQLite 每日快照。刷新页面或重启服务不会改变当天选择，快照默认保留两年。

表情包放在 `assets/memes/`，支持以下浏览器图片格式，扩展名不区分大小写：

```text
GIF, WebP, PNG, APNG, JPG, JPEG, JFIF, AVIF, BMP
```

游戏头图通过 `/api/image-cache` 使用本地限量缓存。缓存默认最长保留 30 天、总量上限 512 MB、单张上限 2 MB，并采用最近最少使用方向清理。当前没有批量截图缓存。

## 数据保留

- 日志保留 30 天并按周期轮转。
- 价格历史最多保留 2 年，较旧数据按时间粒度压缩。
- 在线人数保留 7 天原始快照，之后按天压缩；1 年前按月压缩，2 年前删除。
- 每日推荐快照保留 2 年。
- 已完成或失败的 `crawl_tasks` 默认保留 60 天。

历史图接口默认返回最新 500 条记录，再按时间升序交给前端；`history_limit` 最大可设置为 2,000。

## 主要 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/status` | 后台任务、目录进度、代理和冷却状态 |
| `GET` | `/api/games` | 已收藏游戏列表 |
| `GET` | `/api/games/{appid}` | 游戏详情与历史数据 |
| `GET` | `/api/search?q=...` | 搜索本地目录和 Steam |
| `GET` | `/api/hot-games?limit=100` | 读取本地热门榜缓存 |
| `GET` | `/api/hot-games/version` | 热门榜缓存版本 |
| `GET` | `/api/hot-games/ensure` | 仅投递热门榜后台刷新 |
| `GET` | `/api/niche-pool` | 小众池展示数据 |
| `GET` | `/api/home-picks` | 首页三项每日快照 |
| `POST` | `/api/track` | 收藏游戏 |
| `POST` | `/api/untrack` | 取消收藏 |
| `POST` | `/api/games/{appid}/refresh` | 提升并刷新指定游戏 |

`/api/hot-games` 和普通页面访问只读取已有缓存，不应隐式等待大量 Steam 请求。

## 测试

安装开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

运行测试：

```powershell
python -m pytest
```

查看覆盖率：

```powershell
python -m pytest --cov=backend --cov-report=term-missing
```

测试使用 `tests/.tmp` 中的独立 SQLite 文件，并模拟外部响应，不会请求真实 Steam/ITAD 接口，也不会修改 `data/steamkb.sqlite3`。

## 故障排查

### 页面显示 `failed to fetch`

确认已经运行 `start.ps1`，并通过 `http://127.0.0.1:8765` 访问。若端口被旧进程占用：

```powershell
.\end.ps1
.\start.ps1
```

### 搜索暂时没有结果

搜索优先读取本地 `steam_catalog`。目录仍在补充时可以直接输入 App ID，例如 `730` 或 `570`。页面访问不会等待整个目录 enrich 完成。

### Steam 请求频繁失败

先查看 `/api/status` 和 `data/steamkb.log`，确认失败属于 Steam API、Steam Store、ITAD、图片 CDN、直连还是代理。不要直接提高并发；持续出现 `429` 时应等待对应服务冷却结束。

## 项目文件

- `steamkb.html`：Vue 3 单页前端。
- `backend/`：Python 后端。
- `steamkb.py`：兼容启动入口。
- `start.ps1` / `start.bat`：Windows 启动脚本。
- `end.ps1`：停止本地后端。
- `requirements.txt`：运行依赖。
- `requirements-dev.txt`：测试依赖。
- `.env.example`：不含密钥的环境变量示例。
- `kaka.md`：后续开发路线。

运行时数据库、日志和图片缓存位于 `data/`，不应提交到 Git。
