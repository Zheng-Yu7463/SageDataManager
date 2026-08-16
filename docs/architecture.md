# SAGE Data Manager 架构说明

## 产品边界

SAGE Data Manager 是只读优先的科研资产目录，不是网盘、在线编辑器、Git 仓库或模型训练平台。它负责让实验室既有文件变得可发现、可理解和可审计。

## 领域模型

`Asset` 是论文、数据集、文献、项目和模型的统一根实体。负责人、可见性、标签、文件、版本、关系与活动只实现一次；类型差异放在 `details` 中，并在业务稳定后提升为明确的扩展表。

核心关系：

```text
Asset
├── AssetVersion
├── FileRecord
├── Tag
├── AssetRelation
└── Activity
```

这套结构避免按五类资产复制控制器、权限和文件逻辑。数据集与模型从第一天支持版本，论文与文献也可以使用同一版本机制保存 camera-ready 或批注版本。

## 数据所有权

| 数据 | 唯一事实来源 | 说明 |
|---|---|---|
| 标题、负责人、状态、标签 | PostgreSQL | 由管理端和 API 维护 |
| 原始科研文件 | 服务器目录 | 后端受控读写挂载，路径与任务均由服务端校验 |
| 文件名、大小、修改时间、校验状态 | PostgreSQL 派生索引 | 由扫描器重建 |
| CSV/YAML | 导入导出格式 | 不参与持续双向同步 |

## API

当前 API：

```text
GET /api/health
GET /api/ready
GET /api/dashboard
POST /api/assets
GET /api/assets
POST /api/assets/import
POST /api/assets/import/yaml
GET /api/assets/{id}
PATCH /api/assets/{id}
GET /api/assets/archived
POST /api/assets/{id}/archive
POST /api/assets/{id}/versions
POST /api/assets/{id}/relations
DELETE /api/assets/{id}/relations/{relation_id}
POST /api/assets/{id}/restore
GET /api/archive/health
GET /api/auth/setup-status
POST /api/auth/setup
POST /api/auth/login
GET /api/auth/me
GET /api/auth/admin-accounts
POST /api/auth/admin-accounts
PATCH /api/auth/admin-accounts/{username}
GET /api/settings/system-update
POST /api/settings/system-update/check
POST /api/settings/system-update/apply
POST /api/files/{id}/tickets
GET /api/files/{id}/content?ticket=...
POST /api/archive/scans
GET /api/archive/unclaimed
POST /api/archive/unclaimed/{id}/claim
POST /api/archive/upload-command
```

`GET /api/assets` 支持 `asset_type`、`query`、`status`、`visibility`、`has_files`、`page` 和 `page_size`。前端五类页面使用同一个接口和组件；状态、可见范围和有无已索引文件可组合筛选。


资产登记接口校验全局唯一的 slug，并创建或复用标签、初始版本和负责人记录。当前登录管理员会成为默认负责人和活动记录的操作人。

批量导入的 JSON 接口直接接收资产数组；CSV 由浏览器解析并转换为同一结构。YAML 只通过 `yaml.safe_load` 解析资产数组或 `assets` 数组后，再复用同一套 Pydantic 校验与原子导入服务，不会执行 YAML 中的自定义对象或读取文件路径。

资产编辑只允许管理员更新标题、摘要、状态、可见范围、标签和扩展详情；每次更新都会写入活动记录。归档采用软删除：资产从普通列表、搜索和详情接口隐藏，但既有文件索引、关系、元数据和原始文件均不改变。管理员可以查看归档清单并恢复资产，归档和恢复也会写入活动记录。

版本登记为元数据操作：管理员可附版本说明，并选择是否将新版本设为当前版本；设为当前时此前版本仍保留但不再标记为当前。不会复制、移动或删除已扫描文件。

资产关联由当前资产、目标资产和简短关系类型组成（如 `derived_from`、`supports`、`documents`）。只能关联两条不同且未归档的资产，不允许建立相同方向、相同类型的重复关系。用户可从任一资产详情解除已有关系；建立与解除均记录到操作活动中，且不会影响文件内容或文件位置。

`GET /api/auth/setup-status` 是公开的只读启动状态接口，不返回用户信息。只有数据库完全没有用户时，`POST /api/auth/setup` 才能创建首个管理员并将其标记为实例所有者；提交时会在事务内重新检查状态，数据库中的部分唯一索引同时保证所有部署进程最多写入一个实例所有者。数据库已有任何用户但没有管理员时也不会重新开放引导，避免利用异常数据提升权限。

系统不开放公开注册。后续管理员只能由已认证管理员创建，每个账号保存独立的 scrypt 密码哈希。旧版本中没有个人密码哈希的账号可暂时使用 `SAGE_FIXED_ACCOUNT_PASSWORD` 登录一次，成功后立即升级为个人密码哈希；新部署和会话签名使用独立的 `SAGE_AUTH_SESSION_SECRET`。管理员可启用或停用其他账号，当前登录账号不能自行停用。SCP 命令使用当前登录账号同名的服务器用户。

账号生命周期操作统一写入活动记录，包括初始化实例所有者、生成管理员注册链接或密码恢复链接、受邀注册、密码重置，以及账号显示名称和启停状态更新。活动描述保留操作人和目标账号等审计上下文，但不会记录一次性令牌或密码；没有实际字段变化的更新不会产生空活动。密码重置和停用账号会递增账号的会话代次，使此前签发的浏览器会话立即失效。普通管理员可以协助管理同级账号，但服务层禁止其修改、停用实例所有者或签发其实例所有者恢复链接，避免通过账号恢复取得所有者专属权限。

系统更新采用容器内 API 与宿主机特权代理分离的结构。FastAPI 不能执行任意宿主机命令，只能通过只读挂载的 Unix Socket 和独立共享密钥调用三个固定动作：读取状态、检查 `origin/main`、启动更新。代理不监听 TCP；执行更新还要求实例所有者重新验证当前密码。

宿主机代理只接受固定仓库、固定远端和 `main` 分支。“检查更新”取得的完整 Commit SHA 是后续更新请求的一部分，代理在执行前确认 `origin/main` 仍指向该 SHA，并只允许干净、没有额外提交的工作区 fast-forward。更新前先为运行中的前后端镜像创建操作专用回滚标签，再检查磁盘空间、生成并校验 PostgreSQL 自定义格式备份。代理还默认每 24 小时独立执行相同的数据库备份流程；定时备份与检查、更新共用进程内操作锁，繁忙时自动顺延，避免数据库备份和应用升级重叠。备份使用临时文件、`pg_restore --list` 校验和原子改名，有效备份默认保留最近 10 份。

更新镜像带有目标 Commit 标签。代理在重启容器后检查数据库 Alembic 版本、认证签名密钥、归档存储根、后端 readiness 与 Commit、前端入口及脚本资源、容器镜像 Commit，再提交任务。状态、阶段、旧/新 Commit 和回滚镜像持续写入 `/var/lib/sage-updater`，代理重启时会恢复旧应用或复验已提交版本。构建或验证失败时恢复旧 Commit 和受保护镜像，但数据库迁移与备份恢复始终留给管理员确认。


扫描接口只接受服务端配置的存储根，不接受浏览器传入路径。它使用 `资产类型/资产 slug/文件` 约定匹配既有资产，只同步文件大小、类型、修改时间与健康状态；文件大小或修改时间变化时清空旧校验和，避免继续把过期摘要当作当前文件身份。无法匹配的文件只计为待认领；扫描首遍同时生成待认领快照，不会为同一次操作再次遍历存储根。

扫描与上传入库共用进程内非阻塞读写锁，并在 PostgreSQL 上同时获取事务级 advisory 读写锁。扫描持独占锁；上传 finalize 从文件发布、数据库提交到失败文件补偿全程持共享锁，所以多个入库任务可以并行，但扫描不会观察到尚未提交或正在回滚的归档文件。锁繁忙时请求立即返回冲突响应，由调用方稍后重试。

文件预览与下载先由已登录管理员申请仅 120 秒有效的、绑定文件 ID 和用途的签名访问票据。实际内容请求会再次验证票据对应的管理员仍处于启用状态、文件记录仍健康，并确认规范化文件路径仍位于受控存储根内，随后写入审计活动并由后端流式传输原始字节。浏览器和 API 均不会收到服务器文件路径。


上传指令接口不会传输文件。它由资产列表中对应行触发，根据该已登记资产、当前登录账号和服务端配置的 SSH 主机、端口与宿主机归档根目录生成 `ssh mkdir + scp` 命令；源路径只参与命令文本，不会由服务端读取。目标子目录必须位于该资产目录内，且一级目录按资产类型校验：

| 资产类型 | 可用一级目录 |
| --- | --- |
| paper | `manuscript`、`supplementary`、`source`、`reviews` |
| dataset | `raw`、`processed`、`documentation`、`scripts` |
| literature | `original`、`annotations`、`notes` |
| project | `documentation`、`code`、`data`、`outputs` |
| model | `weights`、`checkpoints`、`configs`、`evaluation` |

AI Agent 的 HTTP 流式上传使用存储根内的内部目录 `.sage-upload-locks` 为每个上传任务创建 Linux `flock`。同一任务在多个后端 worker 间只能由一个请求写入、取消或完成入库，冲突请求立即返回 409；该目录不会进入扫描或待认领结果。流式接收期间不持有数据库事务或连接，任务状态只在写入前后短暂校验，文件落盘和 `fsync` 在线程池执行，避免大文件上传耗尽数据库连接或阻塞异步事件循环。Nginx 默认把 API 请求体限制为 2 MB，仅该文件上传路径放宽至 500 MB，后端仍独立执行大小和校验和检查。

认领接口只接受待认领文件 ID 与现有资产 ID。服务端会重新解析并验证文件仍位于配置的存储根内，再建立文件索引和资产关联；不会移动、复制或删除原始科研文件。后续扫描会识别该关联，避免把该路径重新计为待认领。

## 扫描运行记录

每次扫描都会写入 `ScanRun`，保存发现、索引、失效、待认领与跳过文件的计数。第一版由管理员手动触发；待认领文件可由管理员在管理端认领，计划任务留待下一阶段。

## 安全边界

文件接口遵循：

1. 只接受数据库中的文件 ID；
2. 服务端把相对路径解析到预先配置的存储根；
3. 解析后的绝对路径必须仍位于存储根内部；
4. 符号链接不能越出存储根；
5. 权限校验通过后只从已验证的打开文件描述符流式传输；
6. 不向客户端返回服务器路径；
7. 下载受限资产写入审计日志。

## 下一阶段

1. 增量或计划扫描；
2. OIDC 或实验室账户认证；
