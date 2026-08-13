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
- 局域网 SCP 上传指令：从资产列表对应行生成可复制的建目录与传输命令；
- 资产详情编辑、软归档和恢复：归档不会删除服务器原始文件；
- 显式样例数据脚本。
- 管理员批量 JSON 元数据导入：整批预校验，重复 slug 不会产生部分记录；
- 初始管理员账号的密码登录与有时效的会话令牌；
- 受控文件下载，以及 PDF、图像、文本、CSV 和 JSON 的浏览器预览；

下一阶段可继续接入增量扫描与 OIDC 等实验室统一认证。

## 目录

```text
frontend/   Vue 管理端
backend/    FastAPI 服务、领域模型与迁移
docs/       架构与开发说明
images/     原始界面设计参考
compose.yaml
```

## Docker Compose 启动

要求：Docker Compose v2。

```bash
python backend/scripts/seed_mock_archive.py
cp .env.example .env
docker compose up --build -d
docker compose exec backend python scripts/seed_demo.py
```

访问：

- 管理端：http://localhost:8080
- API 文档：http://localhost:8000/api/docs
- 健康检查：http://localhost:8000/api/health

`.env` 中的 `SAGE_STORAGE_ROOT` 应设置为宿主机上的实际归档目录。首次体验可保留默认值，脚本会生成 `sample-archive/` 模拟文件。Compose 会将其只读挂载到后端的 `/data/sage-archive`。

## 本地开发

后端使用 Python 3.12、PostgreSQL 和 Conda：

```bash
conda create -n sage-data-manager python=3.12 -y
conda activate sage-data-manager
cd backend
pip install -e .
pip install pytest ruff
alembic upgrade head
python scripts/seed_demo.py
uvicorn app.main:app --reload
```


### 模拟归档扫描

先执行 `python scripts/seed_demo.py` 建立元数据，再从仓库根目录生成模拟文件并设置存储根：

```bash
python backend/scripts/seed_mock_archive.py
export SAGE_STORAGE_ROOT="$(pwd)/sample-archive"
```

访问“归档健康”页面并运行扫描。扫描器只更新文件名、大小、类型、修改时间和健康状态；不能匹配 `类型/slug/文件` 结构的文件会计入待认领，不会自动创建资产。管理员可在“待认领文件”页面将其关联到已登记资产；原始文件保持在原目录，后续扫描会沿用该关联。

前端需要 Node.js 24 和 pnpm 11：

### 通过 SCP 上传文件

首次使用时，在 `.env` 中确认以下参数指向归档服务器的局域网地址与宿主机目录（示例值适用于当前开发机）：

```bash
SAGE_UPLOAD_SSH_HOST=192.168.1.213
SAGE_FIXED_ACCOUNT_PASSWORD=在本机 .env 中设置，勿提交到 Git
SAGE_UPLOAD_SSH_PORT=22
SAGE_UPLOAD_DESTINATION_ROOT=/home/zhengyu/SageDataManager/sample-archive
```

登录后进入对应分类，先登记资产并填写基础信息。资产会在当前列表显示“暂无数据”，点击该行右侧“上传指令”，填入保存文件的那台电脑上的绝对路径，再复制命令到该电脑终端执行。命令会先创建资产目录再执行 `scp`；完成后回到“归档健康”运行扫描，资产状态将更新为“已有数据”。SCP 用户名会自动使用当前登录账号名。

目标目录的完整结构固定为 `资产类型/资产 slug/一级目录/可选细分目录`。一级目录不可随意命名：

| 资产类型 | 默认目录 | 其他可选目录 |
| --- | --- | --- |
| 论文 | `manuscript` | `supplementary`、`source`、`reviews` |
| 数据集 | `raw` | `processed`、`documentation`、`scripts` |
| 文献 | `original` | `annotations`、`notes` |
| 项目 | `documentation` | `code`、`data`、`outputs` |
| 模型 | `weights` | `checkpoints`、`configs`、`evaluation` |

### 会议论文测试数据

下面的命令会从 ICLR Proceedings 和 ACL Anthology 同步 2026 年各 10 篇论文元数据，并将正式 PDF 下载到 Git 忽略的 `sample-archive/real-fixtures/`：

```bash
cd backend
python -m scripts.seed_conference_papers \
  --venue ICLR ACL \
  --year 2026 \
  --limit 10 \
  --download-pdf
```

ICLR 条目先由虚拟会议页确认收录，再以规范化题名和首位作者匹配 Proceedings 正式版本；ACL 条目直接以 Anthology 页面为准。脚本按 DOI、官方来源标识和标题加第一作者维护同一篇论文，重复运行会更新现有记录。每份下载内容都必须通过 PDF 文件头和 `pdfinfo` 页数校验后才会进入归档；单个来源下载失败不会阻止其他论文元数据写入，命令结束时会汇总失败项。

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

arXiv 使用官方 Atom API，bioRxiv 使用官方 API，PLOS ONE 使用 Crossref 官方元数据与出版社 PDF。脚本会验证已存在的 PDF 并跳过重复下载；官方来源临时限流时保留论文元数据并明确报告失败文件，归档扫描后这些论文显示为“暂无数据”。

论文目录支持按当前筛选结果批量导出 BibTeX；论文卡片可直接复制单篇 BibTeX，详情页可查看、复制并下载 `.bib`。引用统一由结构化论文元数据生成。同步脚本会尽量从官方来源保存引用键、论文集或期刊名称、页码和出版社；历史会议论文缺少可选字段时使用稳定引用键与会议论文集名称回退。

### 管理员账号

系统首次初始化会预置 `yukai`、`zhengyu`、`zhourongyang`、`fengxuehan`、`chenshangyu` 和 `bisheng` 六个管理员账号。登录页需要手动输入账号；注册功能已预留但关闭。已有管理员可在“系统设置”预置其他管理员账号，或停用不再使用的账号；账号名应与服务器 SSH 用户名一致。共享初始密码必须保存在本机 `.env` 的 `SAGE_FIXED_ACCOUNT_PASSWORD` 中，不能提交到 Git。

品牌配置保存在数据库的单实例配置表中。公开页面可读取品牌信息和 Logo，只有已登录管理员可以修改；Logo 仅接受不超过 1 MB 的 PNG、JPEG 或 WebP 文件。`SAGE_` 环境变量前缀和 `X-Sage-Session` 请求头作为部署兼容契约保留，不受界面品牌名称影响。

### 批量导入元数据

侧栏“批量导入”支持 JSON、从 Excel 导出的 CSV 和 YAML。CSV 至少包含 `type`、`slug`、`title` 三列；`tags` 用 `|` 分隔，`details` 以 JSON 对象填写。YAML 可以是资产数组，或包含 `assets:` 数组的对象。CSV 会先在浏览器转换为可审阅 JSON；JSON 与 YAML 均由服务端全批校验，出现重复 slug 或字段错误时整批不会创建记录。

这不是浏览器直传：网页不会读取本机路径或文件内容，也不会代替用户运行命令。

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
