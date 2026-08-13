# SageDataManager Agent Instructions

## 最高原则

一切代码和文档都像第一次写出来一样：从根因解决问题，保持代码自解释，不保留备份、死代码或中间产物，部署内容必须与仓库源码一致。

## 论文更新

当用户要求更新、同步或补充论文时：

1. 只使用会议官网、正式 proceedings、ACL Anthology、OpenReview、arXiv、bioRxiv、Crossref 或出版社等官方来源确认收录信息。
2. 使用 `backend/scripts/seed_conference_papers.py` 的参数化入口，不另写一次性下载脚本，不直接修改数据库。
3. 明确来源、年份、每个来源的数量以及是否下载 PDF。用户未给数量时，沿用最近请求的数量；没有可靠上下文时默认每个来源 10 篇。
4. ICLR 非 2026 年同步必须先从官方会议页面取得 poster ID，再通过 `--iclr-poster-id` 传入；不得猜测或虚构 ID。
5. 下载 PDF 时必须通过文件头和 `pdfinfo` 页数校验，失败文件不得进入正式归档目录。
6. 同步以 DOI、官方来源 ID、规范化标题和首位作者去重；重复执行更新已有记录，不创建重复论文。
7. 每篇论文必须保留生成 BibTeX 所需的结构化元数据。可从官方来源取得时，写入引用键、论文集、页码、出版社、卷期和月份；无法确认的字段留空，不推断。
8. 同步后运行相关后端测试、归档扫描和前端构建，并确认论文目录与 BibTeX 导出数量一致。
9. 最终报告新增、更新、跳过、PDF 成功、PDF 失败和 BibTeX 可导出数量。

标准命令：

```bash
cd backend
python -m scripts.seed_conference_papers \
  --venue ICLR ACL \
  --year 2026 \
  --limit 10 \
  --download-pdf
```

补充开放来源测试论文时使用：

```bash
cd backend
python -m scripts.seed_conference_papers \
  --venue ARXIV BIORXIV PLOS \
  --year 2026 \
  --limit 10 \
  --download-pdf
```

只更新元数据时使用 `--no-download-pdf`。不得使用 `--skip-database` 交付正式更新；该参数仅用于解析与清单测试。
