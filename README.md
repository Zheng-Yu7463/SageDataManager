# SAGE Data Manager

SAGE 实验室内部科研资产归档与浏览系统。系统将论文、数据集、文献、项目和模型组织到统一资产目录中，原始文件继续保存在实验室现有服务器目录。

## 当前进度

首个纵向 MVP 已包含：

- Vue 3 + TypeScript 管理端壳层；
- 科研档案馆风格首页；
- 五类资产共用的搜索、列表与卡片视图；
- FastAPI 健康检查、首页聚合和资产只读 API；
- PostgreSQL 统一资产、版本、文件、标签、关系和活动模型；
- Alembic 初始迁移；
- Docker Compose 一致化运行环境；
- 显式样例数据脚本。

下一阶段是存储根配置、增量文件扫描、资产详情和真实权限认证。当前页面中的登记、预览和下载按钮只呈现后续入口，尚未开放写操作。

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
cp .env.example .env
docker compose up --build -d
docker compose exec backend python scripts/seed_demo.py
```

访问：

- 管理端：http://localhost:8080
- API 文档：http://localhost:8000/api/docs
- 健康检查：http://localhost:8000/api/health

`.env` 中的 `SAGE_STORAGE_ROOT` 应设置为宿主机上的实际归档目录。Compose 会将它只读挂载到后端的 `/data/sage-archive`。

## 本地开发

后端需要 Python 3.12 和 PostgreSQL：

```bash
cd backend
uv sync --locked
alembic upgrade head
python scripts/seed_demo.py
uvicorn app.main:app --reload
```

前端需要 Node.js 24 和 pnpm 11：

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

