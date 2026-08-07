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
| 原始科研文件 | 服务器目录 | 后端只读挂载 |
| 文件名、大小、修改时间、校验状态 | PostgreSQL 派生索引 | 由扫描器重建 |
| CSV/YAML | 导入导出格式 | 不参与持续双向同步 |

## API

当前 API：

```text
GET /api/health
GET /api/dashboard
POST /api/assets
GET /api/assets
GET /api/assets/{id}
PATCH /api/assets/{id}
GET /api/assets/archived
POST /api/assets/{id}/archive
POST /api/assets/{id}/relations
DELETE /api/assets/{id}/relations/{relation_id}
POST /api/assets/{id}/restore
GET /api/archive/health
POST /api/auth/login
GET /api/auth/me
GET /api/auth/registration-status
GET /api/auth/admin-accounts
POST /api/auth/admin-accounts
PATCH /api/auth/admin-accounts/{username}
POST /api/files/{id}/tickets
GET /api/files/{id}/content?ticket=...
POST /api/archive/scans
GET /api/archive/unclaimed
POST /api/archive/unclaimed/{id}/claim
POST /api/archive/upload-command
```

`GET /api/assets` 支持 `asset_type`、`query`、`page` 和 `page_size`。前端五类页面使用同一个接口和组件。


资产登记接口校验全局唯一的 slug，并创建或复用标签、初始版本和负责人记录。当前登录管理员会成为默认负责人和活动记录的操作人。

资产编辑只允许管理员更新标题、摘要、状态、可见范围、标签和扩展详情；每次更新都会写入活动记录。归档采用软删除：资产从普通列表、搜索和详情接口隐藏，但既有文件索引、关系、元数据和原始文件均不改变。管理员可以查看归档清单并恢复资产，归档和恢复也会写入活动记录。

资产关联由当前资产、目标资产和简短关系类型组成（如 `derived_from`、`supports`、`documents`）。只能关联两条不同且未归档的资产，不允许建立相同方向、相同类型的重复关系。用户可从任一资产详情解除已有关系；建立与解除均记录到操作活动中，且不会影响文件内容或文件位置。

系统初始化会预置六个管理员账号，但它们不是登录白名单。登录页要求手动输入账号；任何已预置、启用且具备管理员角色的账号都可通过本机环境变量中的共享密码登录，并获得有时效的签名会话令牌。系统设置中的管理员可预置、启用或停用账号；当前登录账号不能自行停用。注册状态接口已预留，默认关闭。SCP 命令使用当前登录账号同名的服务器用户。


扫描接口只接受服务端配置的存储根，不接受浏览器传入路径。它使用 `资产类型/资产 slug/文件` 约定匹配既有资产，只同步文件大小、类型、修改时间与健康状态；无法匹配的文件只计为待认领。

文件预览与下载先由已登录管理员申请仅 120 秒有效的、绑定文件 ID 和用途的签名访问票据。实际内容请求会再次验证票据对应的管理员仍处于启用状态、文件记录仍健康，随后写入审计活动。后端仅发送 Nginx 内部重定向；Nginx 在同一只读存储挂载中传输字节。浏览器和 API 均不会收到服务器文件路径。


上传指令接口不会传输文件。它由资产列表中对应行触发，根据该已登记资产、当前登录账号和服务端配置的 SSH 主机、端口与宿主机归档根目录生成 `ssh mkdir + scp` 命令；源路径只参与命令文本，不会由服务端读取。目标子目录必须位于该资产目录内，且一级目录按资产类型校验：

| 资产类型 | 可用一级目录 |
| --- | --- |
| paper | `manuscript`、`supplementary`、`source`、`reviews` |
| dataset | `raw`、`processed`、`documentation`、`scripts` |
| literature | `original`、`annotations`、`notes` |
| project | `documentation`、`code`、`data`、`outputs` |
| model | `weights`、`checkpoints`、`configs`、`evaluation` |

认领接口只接受待认领文件 ID 与现有资产 ID。服务端会重新解析并验证文件仍位于配置的存储根内，再建立文件索引和资产关联；不会移动、复制或删除原始科研文件。后续扫描会识别该关联，避免把该路径重新计为待认领。

## 扫描运行记录

每次扫描都会写入 `ScanRun`，保存发现、索引、失效、待认领与跳过文件的计数。第一版由管理员手动触发；待认领文件可由管理员在管理端认领，计划任务留待下一阶段。
## 安全边界

下一阶段文件接口必须满足：

1. 只接受数据库中的文件 ID；
2. 服务端把相对路径解析到预先配置的存储根；
3. 解析后的绝对路径必须仍位于存储根内部；
4. 符号链接不能越出存储根；
5. 权限校验通过后才交给 Nginx 传输；
6. 不向客户端返回服务器路径；
7. 下载受限资产写入审计日志。

## 下一阶段

1. 增量或计划扫描；
2. OIDC 或实验室账户认证；
3. CSV/YAML 一次性导入。

