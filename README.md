Steam-KaKaBase
==============

一个轻量的 SteamDB 风格游戏数据面板，用于查看 Steam 游戏当前价格、历史价格快照、在线人数趋势和玩家评测概况。

## 后端结构

正式入口为 `python -m backend.main`，原来的 `python steamkb.py` 仍作为兼容入口保留。

- `backend/config.py`：环境变量、路径和运行参数
- `backend/logging_utils.py`：日志与日志轮转
- `backend/db.py`：SQLite 初始化、事务、迁移和任务状态
- `backend/steam_client.py`：Steam/ITAD 请求、代理、重试和冷却
- `backend/crawler.py`：定时采集与任务编排
- `backend/services.py`：搜索、详情、榜单、收藏等业务逻辑
- `backend/server.py`：HTTP 路由、静态文件、CORS 和 JSON 响应
- `backend/main.py`：进程启动及调度器生命周期

`backend/_runtime.py` 是拆分期间保留的私有兼容实现。新增代码应通过上述公开模块调用，不应直接依赖 `_runtime.py`。

## 功能

- 点击搜索栏显示热门榜，输入关键词后搜索 Steam 游戏
- 在游戏详情页点击“收藏”加入跟踪，再次点击可取消收藏
- 拉取当前在线人数：`ISteamUserStats/GetNumberOfCurrentPlayers`
- 拉取多地区价格：`store.steampowered.com/api/appdetails`
- 拉取玩家评价：`store.steampowered.com/appreviews`
- 拉取 Steam 商店截图并在详情页底部轮播展示
- Steam 图片会通过本地后端缓存到 `data/image-cache`，重复打开时不再每次直连 Steam CDN
- 异步拉取 Steam 官方热榜，默认目标最多 5000 个游戏
- 可选拉取 IsThereAnyDeal 历史低价：`api.isthereanydeal.com/v01.game.prices`
- 通过 IsThereAnyDeal `/games/lookup/v1` 和 `/games/historylow/v1` 拉取官方史低价
- 使用 SQLite 定时保存快照：在线人数每 30 分钟，价格/评价每天
- Vue3 + ECharts 前端，包含价格折线图和在线人数柱形图

## 启动

Windows PowerShell:

首次启用异步热榜采集前安装依赖：

```powershell
python -m pip install -r requirements.txt
```

```powershell
.\start.ps1
```

也可以双击 `start.bat`。启动脚本会自动启动后端并打开浏览器到正确的本地地址。如果 8765 被旧后端占用且无法自动关闭，脚本会改用下一个空闲端口。

关闭本地后端：

```powershell
.\end.ps1
```

如果要关闭指定端口：

```powershell
.\end.ps1 -Port 8771
```

或只启动后端：

```powershell
python -m backend.main
```

然后打开：

```text
http://127.0.0.1:8765
```

## 测试

开发环境首次安装测试依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

运行全部测试：

```powershell
python -m pytest
```

查看后端覆盖率：

```powershell
python -m pytest --cov=backend --cov-report=term-missing
```

测试使用 `tests/.tmp` 中的独立临时数据库，并模拟 Steam 响应，不会请求真实 Steam 接口或修改 `data/steamkb.sqlite3`。

如果页面显示 `failed to fetch`：

- 不要直接双击 `steamkb.html`，必须先启动后端，再访问 `http://127.0.0.1:8765`。
- 如果 8765 被旧进程占用或表现异常，换一个可用端口：

```powershell
$env:STEAMKB_PORT="8771"
python -m backend.main
```

然后打开对应地址，例如 `http://127.0.0.1:8771`。
- 如果按游戏名搜索没有结果，可以直接输入 Steam appid，例如 `730`、`570`。

## 可选配置

可以通过环境变量调整：

```powershell
$env:STEAMKB_PORT="8765"
$env:STEAMKB_DB="data\steamkb.sqlite3"
$env:STEAMKB_LOG="data\steamkb.log"
$env:STEAMKB_PLAYER_REFRESH_MINUTES="30"
$env:STEAMKB_PRICE_REFRESH_HOURS="24"
$env:STEAMKB_HOTLIST_TARGET="100"
$env:STEAMKB_CATALOG_LIMIT="20000"
$env:STEAMKB_CATALOG_ENRICH_DAILY_LIMIT="500"
$env:STEAMKB_CATALOG_ENRICH_BATCH_LIMIT="100"
$env:STEAMKB_NICHE_POOL_LIMIT="500"
$env:STEAM_API_KEY="your_steam_web_api_key"
$env:STEAMKB_LOG_RETENTION_DAYS="30"
$env:STEAMKB_PRICE_RETENTION_DAYS="730"
$env:STEAMKB_RECOMMENDATION_RETENTION_DAYS="730"
$env:STEAMKB_CRAWL_TASK_RETENTION_DAYS="60"
$env:STEAMKB_HOTLIST_CONCURRENCY="8"
$env:STEAMKB_HOTLIST_BATCH_SIZE="200"
$env:STEAMKB_HOTLIST_REFRESH_HOURS="24"
$env:STEAMKB_HOT_METADATA_CONCURRENCY="2"
$env:STEAMKB_HOT_PREVIEW_TOP_LIMIT="200"
$env:STEAMKB_HOT_PREVIEW_BATCH_LIMIT="100"
$env:STEAMKB_HOT_FULL_METADATA_TOP_LIMIT="50"
$env:STEAMKB_HOT_METADATA_BATCH_LIMIT="50"
$env:STEAMKB_TRACKED_REFRESH_BATCH_LIMIT="1"
$env:STEAMKB_ITAD_HISTORYLOW_BATCH_LIMIT="50"
$env:STEAMKB_STORE_DELAY_MIN_SECONDS="1.5"
$env:STEAMKB_STORE_DELAY_MAX_SECONDS="4.0"
$env:STEAMKB_DIRECT_COOLDOWN_MINUTES="5"
$env:ITAD_API_KEY="你的 IsThereAnyDeal API Key"
python -m backend.main
```

也可以复制 `.env.example` 为 `.env`，在项目根目录里填写 `ITAD_API_KEY`，后端启动时会自动读取。

说明：

- Steam 在线人数、商店价格、评价接口不需要 API Key。
- IsThereAnyDeal 的历史价格接口通常需要 API Key；未配置时，系统仍会保存 Steam 当前价格快照，前端历史价格图会基于本地定时快照展示。
- 史低高亮只基于 `historical_lows` 表中的 ITAD 史低记录；当前价换算成人民币后与史低差距不超过 0.5 元才会标记为史低。
- 如果未配置 `ITAD_API_KEY`，价格块会显示“未配置 ITAD”；如果正在同步史低，会显示“史低同步中”；如果 ITAD 还没有入库记录，会显示“史低未入库”。
- 配置 ITAD 后，后端会每 30 分钟批量补一次缺失史低，默认每轮最多 50 个游戏。
- Steam 商店接口有地区、频率和风控限制；如果某些地区价格为空，通常是该区不可售、请求被限制或接口临时返回不完整。
- Steam 商店详情/多地区价格抓取之间默认会随机等待 1.5 到 4 秒，避免单次批量刷新请求过密。
- 启用代理回退后，直连失败会进入默认 5 分钟的独立冷却；冷却期间请求直接走代理，到期后仅用一个请求探测直连是否恢复。
- Steam Web API、Steam Store、ITAD 和图片 CDN 分别维护限流冷却；使用统一代理回退层的请求也按服务维护直连冷却。任一服务返回 429 或连接失败都不会暂停其他服务。`/api/status` 的 `service_cooldowns` 和 `direct_service_cooldowns` 可查看各服务剩余秒数。
- 旧数据库里没有截图的游戏，会在后台自动补一次 Steam 商店详情用于保存截图；失败后会按 24 小时间隔退避，不会反复重试。
- 在线人数接口偶发失败时会跳过本次写入并记录到 `data\steamkb.log`，不会让整次刷新失败。
- 自动采集会每分钟检查一次是否到期，但只有在线人数超过 30 分钟、价格/评价超过 24 小时才会访问外部接口并写入数据库。
- 热门榜采用分层缓存：`/api/hot-games` 只返回本地缓存并快速渲染；`/api/hot-games/ensure` 只投递后台同步，不阻塞页面。
- 热榜在线人数使用 `httpx` + `asyncio.Semaphore` 限制并发，默认并发 8；前 200 名慢慢补国区价格、免费状态、好评率和发售日，前 50 名再补完整简介、开发商和截图。
- 后台任务已拆成 Hotlist、Players、Preview、Metadata、HistoryLow，并通过 `crawl_tasks` 记录优先级、下次重试、错误和完成状态。
- 在线人数历史会自动压缩：保留 7 天内原始快照，7 天前按天保留，1 年前按月保留，2 年前删除。
- 在线人数刷新会同时写入历史快照和热榜当前人数；热门榜会按最新人数动态排序。
- 已跟踪游戏的后台刷新默认每轮只处理 1 个，避免本地调试时后台长期占用；搜索加入跟踪只先补详情、在线人数和评价，价格由后台定时慢慢补。
- SQLite 已开启 WAL 模式；热榜、在线人数、元数据写入都按批次提交，默认每批 200 条。
- `/api/hot-games?limit=100` 可查看已保存的热榜数据；如果 Steam 热榜暂时不可用，会按本地玩家快照兜底返回。
- 页面左上角菜单包含主页占位、游戏详情和热门榜；热门榜支持仅看付费游戏、仅看国区史低游戏、按好评率排序。
- 主页会展示今日史低、每日小众宝藏游戏和今日表情包，三者统一在本地时间每天 00:10 切换并保存当天选择。
- 表情包可放入 `assets\memes`，支持 GIF、WebP、PNG/APNG、JPEG/JFIF、AVIF 和 BMP，扩展名不区分大小写。
- 小众推荐使用独立的 `niche_pool`，不依赖热门榜或浏览器缓存；服务端每日快照保证刷新页面或重启后当天推荐不变。
- `steam_catalog` 只保存 Steam AppList 的 AppID、名称和 enrich 状态，默认最多保存 20,000 条；详情按每日额度分批补全，默认每天 500 条、每批 100 条。
- 目录 enrich 会过滤 Steam 返回的非 `game` 类型；候选池最多保留 500 个近 8 年发行、当前在线至少 10、本站峰值不超过 2000、好评率至少 85% 且评测数据有效的游戏。加权分由好评率 45%、评测量 30%、本站峰值 15% 和发行时间 10% 构成。
- 搜索结果会缓存 10 分钟；搜索/浏览不会自动加入跟踪，只有收藏按钮会写入或取消跟踪状态。
- 搜索结果首次打开如果只有名称和封面，后端会自动轻量补全详情、在线人数、评价和国区价格；左侧关注栏显示国区当前价，详情页显示国区当前价和中国区史低价。
- 详情接口默认只返回最新 500 条历史快照并按时间升序输出；可用 `history_limit` 参数调整，最大 2000。

## 文件

- `backend/`：模块化后端；入口为 `backend.main`
- `steamkb.py`：兼容旧启动命令的轻量入口
- `steamkb.html`：Vue3 前端页面
- `data/steamkb.sqlite3`：运行后自动创建的数据库
- `start.ps1` / `start.bat`：启动脚本
- `end.ps1`：按端口关闭本地后端，默认关闭 8765
- `requirements.txt`：异步热榜采集依赖
