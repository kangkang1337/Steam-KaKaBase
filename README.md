# Steam-KaKaBase

## v0.6.1: Secure monitoring controls

Fixed the administrator control panel so owner-only maintenance actions work with the production systemd sandbox. The panel now uses a restricted local Unix Socket broker instead of sudo, retains `NoNewPrivileges=true` for the Web service, uses an in-page confirmation dialog, and correctly records manual local backup completion in monitoring.

## v0.6.0：受保护的管理员监控

管理员可从“更多”进入监控页，查看当日访问聚合、账号与收藏总量、Web/crawler/数据库/备份状态、CPU/内存/磁盘、crawler 最近成功周期、备份与恢复演练，以及脱敏后的最近日志。仅服主可调整管理员名单或执行固定的运维动作：本地/异地备份、隔离恢复演练、重启 Web 与 crawler。普通用户无法读取运行数据，普通管理员也不能调整权限或执行操作。健康摘要会将 5xx、crawler 心跳、备份、恢复演练和磁盘空间的异常以红色提示。

## v0.5.0：账号同步收藏

未登录时，收藏仅保存在当前浏览器的 `localStorage`；登录后，收藏会按账号写入 SQLite，可在不同设备同步查看。账号使用用户名与密码注册，勾选“记住我”会保存最长 30 天的 `Secure`、HttpOnly 会话 Cookie（生产环境）。密码仅保存 PBKDF2-SHA256 派生值和随机盐，不保存明文。登录、注册、删号与收藏写操作受 Nginx 和应用内双层 IP 限流保护。

公网部署务必使用 HTTPS，并备份 SQLite；账号、会话和收藏在数据库迁移 v8 中创建，管理员监控与权限名单在 v9 中创建。

一个面向本地运行的 Steam 数据面板，设计参考 SteamDB。用于查看游戏价格与本地历史快照、在线人数趋势、玩家评价、热门榜和每日小众宝藏推荐。

当前版本：`v0.6.1`

> 项目支持本地运行和单机 VPS 自托管。生产部署使用 Nginx、HTTPS、UFW、systemd、受限写接口与异地 SQLite 备份；仍建议先在个人规模下运行并持续观察 Steam/ITAD 的限流情况。

## 当前功能

- 搜索 Steam 全量轻量目录，也可以直接输入 App ID。
- 详情页可在右上角选择价格地区；价格、折扣、史低提示和价格折线图随选择切换，并会记住浏览器选择。
- 查看中国区价格、多地区价格历史与折扣；本站观测最低作为基础，ITAD 中国区和美国区史低作为增强缓存。
- 查看当前在线人数、本站开始记录后的历史峰值和趋势图。
- 查看 Steam 好评率、评测数量、简介、开发商、发行商和发售日期。
- 在详情页收藏或取消收藏，状态持久化到 SQLite。
- 热门榜默认展示 Steam Top 100，支持仅看付费、仅看史低和按好评率排序。
- 独立维护小众游戏池，并从候选池较强的前 50% 随机抽取最多 20 款展示。
- 首页展示今日史低、每日小众宝藏游戏和今日表情包，统一在本地时间每天 00:10 更新。
- Vue 3 + ECharts 前端，价格折线图和在线人数快照柱形图支持悬停查看数据；玩家图以等宽快照柱显示，并固定展示最多 7 个横轴时间点。
- 仅管理员可查看的运行监控页；仅服主可触发固定的备份、恢复演练和服务重启动作，健康摘要、日志和基础设施数据不会暴露给普通用户。

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

Steam 目录 `steam_catalog` 使用持久化游标按日推进，最终完成 AppList 全量扫描，并在完整扫描后每周校验。目录 enrich 按每日 1,500 条的额度逐步分类和补全，不要求首次启动时等待全部元数据。

## 数据来源

- Steam Web API：热门榜、AppList 轻量目录和当前在线人数。
- Steam Store `appdetails`：名称、头图、简介、发行信息和地区价格。
- Steam Store `appreviews`：好评率和评测数量。
- IsThereAnyDeal `games/lookup/v1` 与 `games/historylow/v1`：Game ID 和地区史低。

价格折线图使用本站按计划保存的 Steam 价格快照，不代表 Steam 官方提供完整历史价格。玩家历史峰值同样只统计本站开始记录后的快照。

价格地区选择只影响游戏详情的价格相关内容。首页、热门榜和小众池继续使用中国区汇总数据，以保持列表查询稳定快速。游戏简介、开发商等元数据目前固定为简体中文；真正按地区切换语言需要单独的多语言元数据缓存，尚未启用。

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
├── catalog.py         Steam AppList 扫描、游标和增量 enrich
├── crawler.py         后台采集与任务编排入口
├── services.py        搜索、详情、榜单、推荐和收藏
├── schemas.py         FastAPI 请求模型与参数校验
├── server.py          仅读缓存并投递任务的 FastAPI 路由
├── main.py            Uvicorn Web 进程入口
├── crawler_main.py    独立采集进程、心跳和单实例租约
└── _runtime.py        模块拆分期间的私有兼容实现
```

Web 入口为 `python -m backend.main`，采集入口为 `python -m backend.crawler_main`。`python steamkb.py` 作为兼容 Web 入口保留。`start.ps1` 会启动并监控两个独立进程；业务配置统一由 `.env` 和 `backend/config.py` 解析。新增后端代码应优先通过公开模块调用，不应继续扩大 `_runtime.py`。

API 由 FastAPI 提供，并包含 `/health`、`/ready`；开发环境提供 `/docs`，生产环境会关闭 API 文档。所有 GET 页面和 API 都严格读取缓存，不会写数据库、投递任务、访问 Steam/ITAD 或下载 CDN 图片。受管理员令牌保护的 POST 只向 SQLite 投递任务，crawler 独立消费。SQLite 中的进程租约确保同一数据库同一时刻只有一个 crawler。

Vue 3.5.13 与 ECharts 5.6.0 使用固定版本并存放在 `assets/vendor/`，生产页面不依赖公共 JavaScript CDN。校验脚本会核对文件 SHA-256，防止依赖文件被意外替换。

`/api/status` 可查看 crawler PID、心跳年龄、租约剩余时间、任务积压、到期任务、重试与永久失败数量、最近一次崩溃恢复、各外部服务本次进程触发的限流冷却次数，以及 SQLite/WAL/日志文件大小。crawler 获得租约后会立即把旧进程遗留的 `running` 任务放回重试队列，并终止已经落后于当前热门榜代际的低优先级任务；用户点击产生的高优先级详情任务不会被该清理影响。

SQLite 开启 WAL 模式，读写可以并行；批量采集按批次提交，避免每抓取一个 App 就提交一次。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 可访问 Steam Web API、Steam Store 和所需图片 CDN 的网络
- ITAD 史低功能需要 IsThereAnyDeal API Key
- Steam AppList 目录同步需要 Steam Web API Key

## 快速启动

克隆项目后，安装运行依赖并创建本地配置：

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

根据需要在 `.env` 中填写 `STEAM_API_KEY`、`ITAD_API_KEY` 和代理设置。没有 API Key 时网站仍可启动并读取已有缓存，但 Catalog 同步和 ITAD 史低功能不会运行。

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

`start.ps1` 会读取 `.env`、停止上次由脚本管理的进程、启动 FastAPI Web 和独立 crawler，再在 Web 就绪后打开浏览器。PID 保存在被 Git 忽略的 `data/runtime/`；用于启动的 PowerShell 窗口需要保持打开。`end.ps1` 会核对 crawler 命令行后停止它，并释放 Web 端口。

分别调试两个进程：

```powershell
# 终端 1：只提供页面和缓存 API
python -m backend.main

# 终端 2：执行外部采集和后台任务
python -m backend.crawler_main
```

## 测试与 CI

安装开发依赖并执行本地检查：

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/check_secrets.py
python -m pytest -q
python scripts/check_frontend.py
```

GitHub Actions 会在每次 push 和 pull request 时安装 Chromium，并重复执行密钥/运行时文件检查、Python 编译、pytest、Playwright 前端流程和 JavaScript 语法检查。CI 不读取本地 `.env`，也不会访问正式 SQLite 数据库。

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
| `STEAMKB_ENV` | `development` | 运行环境；公网部署必须设置为 `production` |
| `STEAMKB_HOST` | `127.0.0.1` | Web 监听地址；使用同机 Nginx 时保持本地监听 |
| `STEAMKB_ADMIN_TOKEN` | 空 | 写接口管理令牌；生产环境至少 32 个字符，否则拒绝启动 |
| `STEAMKB_CORS_ALLOWED_ORIGINS` | 空 | 允许跨域的完整 Origin，多个值用逗号分隔；同域部署留空 |
| `STEAMKB_ALLOWED_HOSTS` | 空 | 允许的 Host 名称，多个值用逗号分隔 |
| `STEAMKB_PROXY_VERIFY_TLS` | `true` | 代理 HTTPS 证书校验；生产环境不应关闭 |
| `STEAMKB_PUBLIC_DETAIL_QUEUE_LIMIT` | `60` | 公开详情补全队列的全站 App 上限 |
| `STEAMKB_AUTH_RATE_LIMIT` | `10` | 每个 IP 在认证窗口内可进行的注册、登录、删号请求数 |
| `STEAMKB_AUTH_RATE_WINDOW_SECONDS` | `300` | 认证接口 IP 限流窗口（秒） |
| `STEAMKB_FAVORITES_RATE_LIMIT` | `60` | 每个 IP 在收藏接口窗口内可进行的请求数 |
| `STEAMKB_FAVORITES_RATE_WINDOW_SECONDS` | `60` | 收藏接口 IP 限流窗口（秒） |
| `STEAMKB_PORT` | `8765` | 本地 HTTP 端口 |
| `STEAMKB_CRAWLER_LEASE_SECONDS` | `120` | crawler 单实例租约有效期 |
| `STEAMKB_CRAWLER_HEARTBEAT_SECONDS` | `20` | crawler 续租和状态心跳间隔 |
| `STEAMKB_SCHEDULER_CHECK_SECONDS` | `60` | crawler 调度循环检查间隔 |
| `STEAMKB_DAILY_REFRESH_TIMEZONE` | `Asia/Shanghai` | 每日主页推荐、今日史低与表情包的日切时区；默认北京时间，边界为 `00:10` |
| `STEAMKB_DB` | `data/steamkb.sqlite3` | SQLite 文件路径 |
| `STEAMKB_LOG` | `data/steamkb.log` | 日志路径 |
| `STEAMKB_DB_BACKUP_DIR` | `data/backups` | 迁移前备份和手动备份目录 |
| `STEAMKB_DB_BACKUP_KEEP` | `10` | 自动保留的最近数据库备份数量 |
| `STEAMKB_DAILY_BACKUP_ENABLED` | `true` | crawler 是否每 24 小时创建一致性 SQLite 备份 |
| `STEAMKB_DAILY_BACKUP_KEEP` | `14` | 每日备份保留份数，不影响手动和迁移备份 |
| `STEAMKB_OFFSITE_REMOTE` | 空 | rclone 异地备份目标，例如 `vultr:bucket/steam-kakabase` |
| `STEAMKB_OFFSITE_RETENTION_DAYS` | `30` | 异地 SQLite 备份保留天数 |
| `STEAMKB_ADMIN_OWNER_USERNAME` | 空 | 服主账号名；该账号可查看监控、管理管理员并执行固定运维动作 |
| `STEAMKB_ADMIN_CONTROLS_ENABLED` | `false` | 是否启用服主专用的备份、演练和服务重启控制组件；Ubuntu 安装脚本会启用 |
| `STEAMKB_VISITOR_METRICS_SECRET` | 回退到管理员令牌 | 用于匿名访客 Cookie 的 HMAC；不保存 IP |
| `STEAMKB_PLAYER_REFRESH_MINUTES` | `30` | 在线人数刷新间隔，最小 30 分钟 |
| `STEAMKB_PRICE_REFRESH_HOURS` | `24` | 价格刷新间隔，最小 24 小时 |
| `STEAMKB_HOTLIST_TARGET` | `100` | 本地热门榜目标数量 |
| `STEAMKB_CATALOG_LIMIT` | `0` | 兼容旧配置；非零时仅作为旧版扫描批量回退值，不再限制目录总量 |
| `STEAMKB_CATALOG_SCAN_BATCH_LIMIT` | `10000` | 每天推进的轻量 AppList 条目上限 |
| `STEAMKB_CATALOG_RESCAN_DAYS` | `7` | 全量扫描完成后的校验周期 |
| `STEAMKB_CATALOG_ENRICH_DAILY_LIMIT` | `1500` | 每日目录 enrich 尝试额度 |
| `STEAMKB_CATALOG_ENRICH_BATCH_LIMIT` | `50` | 单轮 enrich 数量 |
| `STEAMKB_NICHE_POOL_LIMIT` | `500` | 小众候选池上限 |
| `STEAMKB_NICHE_MAX_REVIEWS` | `50000` | 小众候选游戏允许的最大 Steam 评测数 |
| `STEAMKB_HOME_REPEAT_DAYS` | `7` | 首页小众宝藏和今日史低的禁止重复天数 |
| `STEAMKB_HOME_POPULAR_MIN_REVIEWS` | `10000` | 今日史低候选的大众游戏最低评测数 |
| `STEAMKB_HOME_POPULAR_MIN_PLAYERS` | `2000` | 今日史低候选的大众游戏最低在线人数（满足人数或评测数之一即可） |
| `STEAMKB_SEARCH_CACHE_TTL_SECONDS` | `900` | 非空搜索结果的内存缓存时间 |
| `STEAMKB_SEARCH_EMPTY_CACHE_TTL_SECONDS` | `30` | 空搜索结果的短缓存时间 |
| `STEAMKB_SEARCH_CACHE_MAX_ENTRIES` | `512` | 搜索 LRU 缓存的最大查询数量 |
| `STEAMKB_DIRECT_COOLDOWN_MINUTES` | `5` | 直连失败后的独立冷却时间 |
| `STEAMKB_STORE_DELAY_MIN_SECONDS` | `1.5` | 商店请求随机延迟下限 |
| `STEAMKB_STORE_DELAY_MAX_SECONDS` | `4.0` | 商店请求随机延迟上限 |
| `STEAMKB_HISTORICAL_LOW_TOLERANCE_CNY` | `0.5` | 当前价判定史低时允许的人民币误差 |
| `STEAMKB_ITAD_HISTORYLOW_REFRESH_DAYS` | `30` | 已缓存 ITAD 史低的详情刷新间隔；失败时保留旧缓存 |

代理回退配置：

```env
USE_PROXY=true
STEAMKB_PROXY_URL=http://127.0.0.1:7890
```

请求优先直连。只有明确开启代理且启动探测确认代理可连接时，直连失败或超时才会回退到代理。直连正常时不会经过代理；代理地址不可用时继续采用直连。

## 数据库迁移与备份

SQLite 结构使用 `PRAGMA user_version` 和 `schema_migrations` 表管理。当前 schema v10：v9 新增管理员权限与匿名访问聚合表，v10 增加每日 5xx 聚合计数以生成健康摘要。Web 和 crawler 启动时都只执行尚未应用的迁移；存在旧数据库且需要升级时，会先使用 SQLite Backup API 在 `data/backups/` 创建一致性备份，再在单个事务中应用全部待执行版本。

迁移中任意一步失败时，事务会整体回滚，服务停止启动，并在错误中给出升级前备份路径。crawler 还会使用 SQLite Backup API 每 24 小时在线创建一次 `daily` 备份，默认保留 14 份；每日备份、手动备份和迁移备份分别轮转，不会互相删除。数据库和备份文件均被 Git 忽略。

## 生产安全配置

建议让 Uvicorn 只监听 `127.0.0.1`，由同机 Nginx 提供 HTTPS 和公网入口。公网部署必须使用 HTTPS：安装脚本会申请证书、强制 HTTP 跳转 HTTPS，并在部署结束前检查 HTTPS `/ready`。生产登录 Cookie 自动带 `Secure`、`HttpOnly` 与 `SameSite=Lax`。生成管理令牌：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

生产环境至少配置：

```env
STEAMKB_ENV=production
STEAMKB_HOST=127.0.0.1
STEAMKB_ADMIN_TOKEN=替换为生成的随机令牌
STEAMKB_ALLOWED_HOSTS=steam.example.com,127.0.0.1,localhost
STEAMKB_CORS_ALLOWED_ORIGINS=
```

前后端同域时 CORS 应保持为空。只有前端确实部署在另一个域名时，才填写类似 `https://www.example.com` 的完整 Origin，不能在生产环境使用 `*`。认证与收藏接口同时受 Nginx 和应用进程按 IP 限流保护；应用层不会将 IP 写入 SQLite。未登录收藏只保留在本机浏览器，登录收藏只归当前账号。

账号页提供删除账号入口，需输入当前密码确认；会永久移除用户名、密码派生值、会话及该账号的同步收藏。当前不收集邮箱，因此没有自助找回或修改密码；管理员确认账号归属后可在服务器交互式执行下列命令重置密码（不会把新密码写入 shell 历史，并会注销该账号全部会话）：

```bash
cd /opt/steam-kakabase
sudo -u steamkb .venv/bin/python scripts/reset_account_password.py USERNAME
```

管理员仍可通过 API 管理全局采集：

```bash
curl -X POST https://steam.example.com/api/games/730/refresh \
  -H "Authorization: Bearer $STEAMKB_ADMIN_TOKEN"
```

未捕获异常只向浏览器返回通用 500 信息，完整异常写入服务端日志。生产状态接口也会移除 crawler PID、主机名和内部错误原文。

发布前请逐项确认：

- Vultr 防火墙和 UFW 只开放 `22`、`80`、`443`，绝不开放 `8765`；Uvicorn 保持监听 `127.0.0.1`。
- `/opt/steam-kakabase/.env` 仅允许 root 和 `steamkb` 组读取，不提交 `.env`、SQLite、日志或备份。
- 定期确认每日异地备份定时器成功运行并检查远端最新备份；本机 `data/backups/` 只能防升级或误操作，不能防 VPS 丢失。
- 每次重要升级前，确认生成了 `pre-deploy` 备份；首次 v7 → v8 会额外生成迁移前备份。
- 不新增第二层反向代理；如确有 CDN/负载均衡，需先正确传递并限制真实客户端 IP，避免破坏 Nginx 限流的 IP 识别。

Ubuntu 24.04 的 Nginx、HTTPS、systemd、UFW、SSH 加固和 rclone 部署步骤见 [`deploy/README.md`](deploy/README.md)。安装脚本会在修改配置后依次执行健康检查；没有已验证的非 root SSH 公钥时会拒绝关闭密码和 root 登录。

管理控制不关闭 Web 服务的 `NoNewPrivileges` 沙箱，也不通过 `sudo` 执行。Web 仅能连接权限为 `0600` 的本机 Unix Socket；root 控制代理只接受代码中固定的五种动作，不接收任意命令、路径或 systemd 服务名。部署脚本会删除旧版 sudo bridge 与对应 sudoers 规则。

常用维护命令：

```powershell
# 查看当前版本
python scripts/manage_database.py status

# 手动创建一致性备份
python scripts/manage_database.py backup

# 单独执行待处理迁移（正常启动时会自动执行）
python scripts/manage_database.py migrate

# 恢复前必须先运行 .\end.ps1
python scripts/manage_database.py restore data/backups/备份文件.sqlite3 --confirm
```

恢复操作会先额外保存一份当前数据库，再校验目标备份并替换数据库。新增结构变更时，应在 `backend/migrations.py` 追加更高版本的 `Migration`，不能修改已经发布的迁移，也不能重新把补列逻辑放回 `ensure_schema()`。

### 隔离恢复演练

恢复演练不会替换正在使用的数据库：将一份本地或异地 SQLite 备份下载到服务器后，恢复到一个**此前不存在**的新目录并验证。该命令会校验备份、应用必要迁移、运行 `PRAGMA quick_check` 并输出游戏数量；目录已经存在时会拒绝执行。

```bash
cd /opt/steam-kakabase
sudo -u steamkb .venv/bin/python scripts/rehearse_database_restore.py \
  /var/lib/steamkb-restore-drill/下载的备份.sqlite3 \
  /var/lib/steamkb-restore-drill/result-YYYYMMDD
```

演练成功后，保留终端输出中的备份时间、文件大小、`quick_check` 和游戏数量作为记录；确认无须保留时再删除整个演练目录。不要对生产库使用此命令的输出路径，也不要在演练中运行 `manage_database.py restore`。

## 冷却与重试

以下服务分别维护限流冷却，不会因一个服务返回 `429` 而暂停其他服务：

- `steam_api`：热门榜、AppList、在线人数。
- `steam_store`：商店详情、价格和评价。
- `itad`：Game ID 与史低。
- `image_cdn`：后端图片缓存。

使用统一代理回退层的请求还会按服务维护直连冷却。冷却期间直接使用已确认可用的代理；到期后只放行一次直连探测。前端状态栏和首页监控区会显示冷却服务和剩余时间。

可以通过以下接口查看状态：

```text
GET /api/status
```

其中 `service_cooldowns` 表示接口限流冷却，`direct_service_cooldowns` 表示直连失败冷却。

## 首页与图片

首页三项内容共用 `00:10` 日界线，并保存 SQLite 每日快照。刷新页面或重启服务不会改变当天选择，快照默认保留两年。今日史低优先排除过去 7 天已经推荐过的游戏，只有候选不足时才允许重复。

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
| `POST` | `/api/games/{appid}/interest` | 请求已收录游戏的详情优先补全；限速且不会重复刷新 |
| `GET` | `/api/search?q=...&limit=12&offset=0` | 分页搜索本地 FTS 索引，不等待 Steam |
| `GET` | `/api/hot-games?limit=100` | 读取本地热门榜缓存 |
| `GET` | `/api/hot-games/version` | 热门榜缓存版本 |
| `GET` | `/api/niche-pool` | 小众池展示数据 |
| `GET` | `/api/home-picks` | 首页三项每日快照 |
| `POST` | `/api/track` | 收藏游戏 |
| `POST` | `/api/untrack` | 取消收藏 |
| `POST` | `/api/games/{appid}/refresh` | 提升并刷新指定游戏 |
| `POST` | `/api/refresh-all` | 刷新全部服务器收藏 |

除 `POST /api/games/{appid}/interest` 外，所有 POST 接口均要求管理令牌（开发模式且未配置令牌时除外）。`interest` 仅允许已收录游戏创建缺失的高优先级任务，不会重复创建、重启已完成任务或等待 Steam；Nginx 还会按 IP 限速。`/api/hot-games`、详情、搜索和普通页面访问只读取已有缓存，不会隐式投递任务或等待 Steam 请求。

## 搜索策略

普通文本搜索只读取本地数据，不在 HTTP 请求内等待 Steam。SQLite 会为游戏名称、AppList 原名和本地化简介建立 FTS5 trigram 索引，并用数据库触发器增量维护；另有独立的本地双语别名索引，保证 `elden` 可找到 `艾尔登法环`、`空洞骑士` 可找到 `Hollow Knight`，不依赖该游戏是否已经完成 Catalog 补全。

搜索结果使用有上限的进程内 LRU 作为一级缓存，FTS5 索引作为二级缓存。非空结果默认缓存 15 分钟，空结果只缓存 30 秒，以便 catalog 新数据较快变得可见。`/api/status` 的 `search` 字段提供请求数、缓存命中率、数据库平均/最大查询耗时、缓存条目数和估算内存占用。SQLite 使用每请求短连接而非传统连接池，该策略也会在状态中明确返回。

点击尚未补全的目录游戏时，页面先显示已有 Catalog 信息，并仅投递一次受限的高优先级详情任务；随后每 10 秒从本地缓存读取更新，最长约 6 分钟。页面刷新会通过 URL 中的 App ID 恢复当前详情。Steam 限流或不可用不会拖慢搜索和详情接口。

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

搜索读取本地 FTS 索引。目录仍在补充时，尚未进入 catalog 且从未查看过的游戏可能暂时搜不到；已收录的 App ID（例如 `730` 或 `570`）也可以直接搜索。页面访问不会等待整个目录 enrich 完成。

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
