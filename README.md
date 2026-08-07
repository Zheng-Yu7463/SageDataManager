# SAGE Data Manager

SAGE 实验室内部科研资产归档与浏览系统。系统将论文、数据集、文献、项目和模型组织到统一资产目录中，原始文件继续保存在实验室现有服务器目录。

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
- 局域网 SCP 上传准备器：为已有资产生成可复制的建目录与传输命令；
- 显式样例数据脚本。
- 六个固定管理员账号的密码登录与有时效的会话令牌；

下一阶段是增量扫描、OIDC 等实验室统一认证和受控文件访问。资产登记与管理员文件认领已开放；预览和下载按钮仍只呈现后续入口。

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

登录后打开管理端的“上传准备”，选择已登记资产，填入保存文件的那台电脑上的绝对路径，然后复制生成的命令到该电脑终端执行。命令会先创建资产目录再执行 `scp`；完成后回到“归档健康”运行扫描。SCP 用户名会自动使用当前登录账号名。

### 固定管理员账号

当前只开放 `yukai`、`zhengyu`、`zhourongyang`、`fengxuehan`、`chenshangyu` 和 `bisheng` 六个管理员账号。注册功能已预留但关闭；共享初始密码必须保存在本机 `.env` 的 `SAGE_FIXED_ACCOUNT_PASSWORD` 中，不能提交到 Git。

这不是浏览器直传：网页不会读取本机路径或文件内容，也不会代替用户运行命令。


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

