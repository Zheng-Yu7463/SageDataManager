# DataManager（默认 SAGE 实例）

面向实验室和研究团队的可配置科研资产归档与浏览系统。系统将论文、数据集、文献、项目和模型组织到统一资产目录中，原始文件继续保存在现有服务器目录。仓库默认提供 SAGE 品牌实例，部署后可在“系统设置 → 品牌与外观”修改产品名称、副标题、组织名称、双语标语、主题主色和 Logo，无需重新构建前端。

## 当前进度

首个纵向 MVP 已包含：

- Vue 3 + TypeScript 管理端壳层；
- 科研档案馆风格首页；
- 五类资产共用的搜索、列表与卡片视图；
- FastAPI 健康检查、首页聚合和资产只读 API；
- 资产详情页：版本、文件安全元数据、关联资产和归档活动浏览；
- PostgreSQL 统一资产、版本、文件、标签、关系和活动模型；
- Alembic 初始迁移；
- Docker Compose 一致化运行环境；
- 手动归档扫描与持久化待认领文件清单；
- 待认领文件认领：关联到已登记资产，不移动原始文件；
- 五类资产登记：标题、slug、摘要、状态、可见范围、初始版本和标签；
- 安全上传闭环：生成隔离区传输命令，完成检测、冲突校验、原子入库与即时索引；
- 资产详情编辑、软归档和恢复：归档不会删除服务器原始文件；
- 显式样例数据脚本。
- 管理员批量 JSON 元数据导入：整批预校验，重复 slug 不会产生部分记录；
- 初始管理员账号的密码登录与有时效的会话令牌；
- 受控文件下载，以及 PDF、图像、文本、CSV 和 JSON 的浏览器预览；
- 面向 AI 客户端的公开 `agent.md`、个人访问令牌和隔离的 Agent API；
- 由实例所有者触发的网页更新：检查 `origin/main`、备份 PostgreSQL、重新构建并验证服务；

下一阶段可继续接入增量扫描与 OIDC 等实验室统一认证。

## 目录

```text
frontend/   Vue 管理端
backend/    FastAPI 服务、领域模型与迁移
docs/       架构与开发说明
compose.yaml
```

## Docker Compose 启动

要求：Docker Compose v2。

```bash
python backend/scripts/seed_mock_archive.py
cp .env.example .env
docker compose up --build -d
```

访问：

- 管理端：http://localhost:8080
- API 文档：http://localhost:8080/api/docs
- 健康检查：http://localhost:8080/api/health

`.env` 中的 `SAGE_STORAGE_ROOT` 是宿主机路径，应设置为实际归档目录。首次体验可保留 `.env.example` 的 `./sample-archive`，脚本会在仓库中生成该模拟目录。Compose 会将宿主机目录挂载到后端的 `/data/sage-archive`；后端需要写权限才能完成隔离上传和原子入库。

`POSTGRES_PASSWORD` 可以使用包含 `@`、`:`、`/`、`#`、`%` 等字符的随机值。Compose 会把数据库连接字段分别传给后端，再由 SQLAlchemy 安全构造连接 URL；不要手工对 `.env` 中的密码做 URL 编码。`SAGE_DATABASE_URL` 仅用于不经过 Compose、直接运行后端或 Alembic 的开发场景。

启动前将 `.env` 中的 `SAGE_AUTH_SESSION_SECRET` 替换为独立的长随机值。首次打开管理端时，页面会要求创建唯一的实例所有者；完成初始化后，如需演示目录，再执行 `docker compose exec backend python -m scripts.seed_demo`。

## 网页更新（直接跟踪 main）

该功能不要求 GitHub Release。服务器仍以 Git 仓库的 `origin/main` 作为部署通道；网页只显示当前 Commit、远端最新 Commit 和待更新提交，由实例所有者确认当前密码后启动更新。普通管理员可以查看和检查版本，但不能执行更新。

首次部署或从旧版本升级后，在仓库根目录执行一次：

```bash
git pull --ff-only
sudo bash deploy/install-updater.sh
```

安装脚本会：

- 在 `.env` 缺少配置时生成独立的 `SAGE_UPDATE_AGENT_SECRET`；
- 安装并启动仅监听 Unix Socket 的 `sage-updater` systemd 服务；
- 将该 Socket 只读挂载到后端容器，并重新构建后端和前端；
- 不开放新的 TCP 端口，也不向浏览器暴露宿主机命令执行能力。

安装完成后进入“系统设置 → 系统与更新”。更新流程固定为：

1. 获取并锁定检查结果中的完整 Commit SHA；执行前再次校验当前分支、工作区、`origin` 和 `origin/main` 均未偏离该结果；
2. 为当前运行的 `backend`、`frontend` 镜像创建本次操作专用的回滚标签；
3. 检查数据库容量和备份目录可用空间，将 PostgreSQL 自定义格式备份写入临时文件，使用 `pg_restore --list` 校验后原子落盘；
4. 以 `--ff-only` 合并锁定的 Commit，使用该 SHA 标记并重新构建、启动前后端镜像；
5. 通过 `/api/ready` 验证数据库连接、Alembic 版本、认证签名密钥、归档存储根和后端运行 Commit，同时验证前端入口、脚本资源及两个容器的镜像 Commit；
6. 验证失败时恢复旧 Commit 和受保护的旧应用镜像；数据库备份保留供人工恢复，数据库迁移不会自动降级。

“检查更新”会立即创建后台检查任务，页面通过状态接口轮询结果；“立即更新”只接受这次检查返回的完整 Commit SHA。重复触发时，页面会接管现有任务并继续展示其进度。代理会将每个阶段、目标 Commit、旧 Commit 和回滚镜像持久化到 `/var/lib/sage-updater/status.json`；代理或宿主机意外重启后会先恢复或复验中断任务，不会直接开始新更新。

更新代理独立于代码更新，每 24 小时自动创建并校验一次 PostgreSQL 备份；更新前创建的备份也计入同一保留策略。默认保留最近 10 份有效备份，可通过 `.env` 中的 `SAGE_UPDATE_BACKUP_INTERVAL_SECONDS` 调整间隔（设为 `0` 可关闭），通过 `SAGE_UPDATE_BACKUP_RETENTION` 调整保留数量。自动备份与检查、更新共用操作锁，不会同时运行。因此，服务器上临时修改过的 Dockerfile、未提交文件或本地提交都会阻止网页更新；应先明确提交、丢弃或迁移这些改动，再重新检查更新。

网页提示 Git 工作区存在未提交内容时，会列出最多 5 条 Git 状态项。请在服务器仓库运行 `git status --short` 核对完整列表，再提交、移走或还原这些内容；更新代理不会自行删除服务器文件。

更新状态只保存脱敏后的远端地址，不会回传 HTTP(S) 或 SSH URL 中的 userinfo、查询参数和 fragment。仍建议使用 Git credential helper 或 SSH agent，不要把 GitHub PAT 直接写进 `origin` URL。

数据库不会在失败时自动回滚，避免对已运行的新迁移做未经确认的破坏性恢复。应用回滚成功时页面会明确显示旧应用已恢复；应用回滚不完整时必须在服务器人工处理，并根据已保留的 dump 决定是否恢复数据库。

常用诊断命令：

```bash
systemctl status sage-updater --no-pager
journalctl -u sage-updater -n 200 --no-pager
docker compose ps
docker compose logs --tail 200 backend frontend
```

更新中若 `deploy/sage_updater.py` 发生变化，成功后代理会退出并由 systemd 自动加载新代码；若安装脚本或 systemd 模板变化，设置页会提示在服务器重新执行 `sudo bash deploy/install-updater.sh`。

## 本地开发

后端使用 Python 3.12、PostgreSQL 和 Conda：

```bash
conda create -n sage-data-manager python=3.12 -y
conda activate sage-data-manager
cd backend
pip install -e .
pip install pytest ruff
alembic upgrade head
uvicorn app.main:app --reload
```

后端启动后打开前端完成首个管理员初始化，再按需执行 `python -m scripts.seed_demo`。数据脚本只使用已存在的实例所有者，不创建或删除管理员账号。

### 模拟归档扫描

先执行 `python -m scripts.seed_demo` 建立元数据，再从仓库根目录生成模拟文件并设置存储根：

```bash
python backend/scripts/seed_mock_archive.py
export SAGE_STORAGE_ROOT="$(pwd)/sample-archive"
```

访问“归档健康”页面并运行扫描。扫描器只更新文件名、大小、类型、修改时间和健康状态；不能匹配 `类型/slug/文件` 结构的文件会计入待认领，不会自动创建资产。管理员可在“待认领文件”页面将其关联到已登记资产；原始文件保持在原目录，后续扫描会沿用该关联。

前端需要 Node.js 24 和 pnpm 11：

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

### 通过 SCP 上传文件

首次使用时，在 `.env` 中确认以下参数指向归档服务器的局域网地址与宿主机目录（示例值适用于当前开发机）：

```bash
SAGE_UPLOAD_SSH_HOST=192.168.1.213
SAGE_AUTH_SESSION_SECRET=在本机 .env 中设置长随机值，勿提交到 Git
SAGE_UPLOAD_SSH_PORT=22
SAGE_UPLOAD_DESTINATION_ROOT=/home/zhengyu/SageDataManager/sample-archive
```

登录后进入对应分类，先登记资产并填写基础信息。资产会在当前列表显示“暂无数据”，点击该行右侧“上传指令”，选择文件或目录并填入保存文件的那台电脑上的绝对路径，再复制命令到该电脑终端执行。命令只会将内容传到归档根下的隔离区 `.uploads/<任务 ID>`，不会直接写入正式资产目录。传输完成后回到同一弹窗点击“检测并入库”：服务端会检查实际文件、拒绝符号链接、预先列出所有重名冲突，然后一次性移动文件、建立索引并刷新目录。任一步失败都不会留下部分入库结果。SCP 用户名会自动使用当前登录账号名。

上传任务在返回的 `expires_at` 后失效，不应继续传输或重试。系统会在后续创建上传任务时安全回收过期任务及其隔离区文件，避免中断任务长期占用归档磁盘。

目标目录的完整结构固定为 `资产类型/资产 slug/一级目录/可选细分目录`。一级目录不可随意命名：

| 资产类型 | 默认目录 | 其他可选目录 |
| --- | --- | --- |
| 论文 | `manuscript` | `supplementary`、`source`、`reviews` |
| 数据集 | `raw` | `processed`、`documentation`、`scripts` |
| 文献 | `original` | `annotations`、`notes` |
| 项目 | `documentation` | `code`、`data`、`outputs` |
| 模型 | `weights` | `checkpoints`、`configs`、`evaluation` |

### 外部文献测试数据

`Papers / 论文` 只管理实验室自产论文、投稿版本与发表成果；`Literature / 文献` 管理外部论文、预印本、期刊文章及其阅读资料。下面的命令会从 ICLR Proceedings 和 ACL Anthology 同步 2026 年各 10 篇外部文献元数据到 Literature，并将正式 PDF 下载到 Git 忽略的 `sample-archive/real-fixtures/literature/`：

```bash
cd backend
python -m scripts.seed_conference_papers \
  --venue ICLR ACL \
  --year 2026 \
  --limit 10 \
  --download-pdf
```

ICLR 条目先由虚拟会议页确认收录，再以规范化题名和首位作者匹配 Proceedings 正式版本；ACL 条目直接以 Anthology 页面为准。脚本按 DOI、官方来源标识和标题加第一作者维护同一篇出版物，重复运行会更新现有记录。每份下载内容都必须通过 PDF 文件头和 `pdfinfo` 页数校验后才会进入归档；单个来源下载失败不会阻止其他文献元数据写入，命令结束时会汇总失败项。

`--venue`、`--year` 和 `--limit` 可按需调整。ICLR 非 2026 年同步需要用一个或多个 `--iclr-poster-id` 明确指定会议官网 poster ID；脚本不会猜测收录列表。仅更新元数据时使用 `--no-download-pdf`。

同一个参数化入口也支持 arXiv、bioRxiv 和 PLOS ONE 官方来源：

```bash
cd backend
python -m scripts.seed_conference_papers \
  --venue ARXIV BIORXIV PLOS \
  --year 2026 \
  --limit 10 \
  --download-pdf
```

arXiv 使用官方 Atom API，bioRxiv 使用官方 API，PLOS ONE 使用 Crossref 官方元数据与出版社 PDF。脚本会验证已存在的 PDF 并跳过重复下载；官方来源临时限流时保留文献元数据并明确报告失败文件，归档扫描后这些文献显示为“暂无数据”。

Papers 和 Literature 分别按当前目录及筛选结果批量导出 BibTeX，两个目录不会混合计数。结构化出版物卡片可直接复制单篇 BibTeX，详情页可查看、复制并下载 `.bib`。同步脚本会尽量从官方来源保存引用键、论文集或期刊名称、页码和出版社；缺少可选字段时使用稳定引用键与会议论文集名称回退。

从旧版本升级且已经将官方来源论文放入 Papers 时，可执行一次本地迁移；该模式只迁移带“官方收录”活动记录的资产，不会移动实验室自行登记的论文：

```bash
cd backend
python -m scripts.seed_conference_papers --migrate-existing --no-download-pdf
```

### 管理员账号

空数据库首次启动时，公开状态接口只报告实例是否已初始化，网页据此显示首个管理员引导。初始化请求会再次确认数据库中没有任何用户，并创建唯一的实例所有者；数据库唯一约束保证多进程并发请求也只能成功一次。实例中已有任何用户时，引导永久关闭，不开放公开注册。后续管理员只能由已登录管理员在“系统设置”中预留账号名；系统生成默认 7 天有效、只能使用一次的长注册链接，受邀者通过链接填写姓名、邮箱和自己的密码。账号名应与服务器 SSH 用户名一致。服务端只保存邀请令牌的 SHA-256，不保存完整链接；重新生成链接会立即撤销此前未使用的链接。密码恢复同样由管理员生成一次性链接，由账号本人设置新密码，管理员不能查看或代填密码。普通管理员可以协助同级账号，但不能修改、停用实例所有者或签发其实例所有者恢复链接。有效期可通过 `SAGE_ACCOUNT_INVITATION_TTL_SECONDS` 调整。

新部署必须配置 `SAGE_AUTH_SESSION_SECRET`。从旧版本升级时，可暂时保留 `SAGE_FIXED_ACCOUNT_PASSWORD`：尚无独立密码的旧管理员首次成功登录后会自动写入个人密码哈希。全部旧账号完成升级后应移除该变量。

品牌配置保存在数据库的单实例配置表中。公开页面可读取品牌信息和 Logo，只有已登录管理员可以修改；Logo 仅接受不超过 1 MB 的 PNG、JPEG 或 WebP 文件。`SAGE_` 环境变量前缀和 `X-Sage-Session` 请求头作为部署兼容契约保留，不受界面品牌名称影响。

### AI Agent 接入

AI 客户端先读取公开的 `/agent.md`，再按其中的流程调用 `/api/agent/*`。它可以查询并读取单项资产、原地更新已有元数据、登记新资产、上传并入库文件以及导出 BibTeX。每位管理员可在“系统设置 → AI 访问令牌”创建多个个人访问令牌，分别设置名称、权限和有效期。完整令牌只显示一次，服务端只保存 HMAC 摘要；令牌可以随时撤销，活动记录会同时标记所属管理员和令牌名称。

令牌权限包括查询资产、登记元数据、上传文件、正式入库和导出 BibTeX。“正式入库”默认不勾选，应只授予需要完成归档闭环的可信自动化。令牌只适用于专用 Agent API，不能调用管理员、品牌、归档或删除接口。新部署必须设置 `SAGE_AUTH_SESSION_SECRET`，否则服务器拒绝初始化管理员以及创建和验证访问令牌；`SAGE_FIXED_ACCOUNT_PASSWORD` 仅用于旧账号升级。

服务端默认最多每 300 秒持久化一次令牌的最近使用时间，避免高频 Agent 请求反复写同一行；可通过 `SAGE_AGENT_TOKEN_LAST_USED_INTERVAL_SECONDS` 调整，设为 `0` 时每次请求都会更新。
反向代理默认只接受不超过 2 MB 的 API 请求体，仅 `PUT /api/agent/uploads/{upload_id}/files/{relative_path}` 文件流式上传路径放宽到 500 MB；后端仍会独立执行单文件大小与校验和检查。


### 批量导入元数据

侧栏“批量导入”支持 JSON、从 Excel 导出的 CSV 和 YAML。CSV 至少包含 `type`、`slug`、`title` 三列；`tags` 用 `|` 分隔，`details` 以 JSON 对象填写。YAML 可以是资产数组，或包含 `assets:` 数组的对象。CSV 会先在浏览器转换为可审阅 JSON；JSON 与 YAML 均由服务端全批校验，出现重复 slug 或字段错误时整批不会创建记录。

这不是浏览器直传：网页不会读取本机路径或文件内容，也不会代替用户运行命令。生成 SCP 指令后，页面每两秒读取隔离区状态并显示已检测文件数与容量；终端命令完成后才显示“传输已完成”。正式入库会为每个文件计算并保存 SHA-256，同一资产内发现相同内容时整批回滚，不覆盖既有文件。

### 资产维护与归档

在资产详情页可修改标题、摘要、状态、可见范围和标签。归档操作会将资产从普通目录与搜索结果隐藏，保留全部文件索引、元数据和活动记录；不会移动或删除服务器原始文件。管理员可在侧栏“已归档资产”中恢复记录，恢复后会重新出现在原分类目录。


```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Vite 会把 `/api` 代理到 `http://127.0.0.1:8000`。

## 质量检查

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/pytest

cd ../frontend
pnpm build
```

## 数据原则

- PostgreSQL 是业务元数据的唯一事实来源；
- 服务器目录是文件内容的唯一事实来源；
- 扫描器只同步文件事实，不覆盖人工维护的业务元数据；
- 浏览器不接触服务器绝对路径；
- 第一版不通过网页删除物理文件。

详细设计见 [架构说明](docs/architecture.md)。
