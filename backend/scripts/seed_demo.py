from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.db.session import SessionLocal
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import Activity, Asset, AssetVersion, FileRecord, Tag, User

ASSETS = [
    {
        "type": AssetType.PAPER,
        "slug": "sage-r1-scientific-qa",
        "title": "SAGE-R1: A Reasoning Model for Scientific QA",
        "summary": "面向科学问答的推理模型研究，包含论文、审稿材料与复现实验记录。",
        "status": "accepted",
        "version": "camera-ready",
        "tags": ["科学问答", "推理模型", "大模型"],
        "details": {"venue": "ACL 2026", "year": 2026, "authors": ["李明", "王雪", "张伟"]},
        "size": 18_400_000,
    },
    {
        "type": AssetType.DATASET,
        "slug": "climatebench-v2",
        "title": "ClimateBench v2.1 数据集",
        "summary": "全球气候变量长时间序列数据，覆盖温度、降水与风速等观测指标。",
        "status": "available",
        "version": "v2.1.0",
        "tags": ["气候科学", "观测数据", "时间序列"],
        "details": {"format": "NetCDF", "samples": 32456, "license": "Research use"},
        "size": 1_180_000_000_000,
    },
    {
        "type": AssetType.LITERATURE,
        "slug": "transformer-survey",
        "title": "Transformer: A Survey",
        "summary": "实验室共享的 Transformer 架构综述与批注版参考资料。",
        "status": "collected",
        "version": "2025-05",
        "tags": ["Transformer", "综述", "深度学习"],
        "details": {"venue": "IEEE TPAMI", "year": 2024, "authors": ["张伟", "陈晨"]},
        "size": 11_700_000,
    },
    {
        "type": AssetType.PROJECT,
        "slug": "multimodal-understanding",
        "title": "多模态理解项目",
        "summary": "围绕科学图表、文本与视觉数据联合理解的实验室长期项目。",
        "status": "active",
        "version": "2026-Q3",
        "tags": ["多模态", "计算机视觉"],
        "details": {"members": 6, "started_at": "2025-09-01"},
        "size": 262_000_000_000,
    },
    {
        "type": AssetType.MODEL,
        "slug": "sage-vision-7b",
        "title": "SAGE-Vision-7B",
        "summary": "面向科学图像理解的视觉语言模型及其评测归档。",
        "status": "available",
        "version": "v1.1.0",
        "tags": ["视觉语言模型", "多模态", "科学图像"],
        "details": {"base_model": "Llama-3.2-8B", "parameters": "7B", "primary_metric": 76.7},
        "size": 68_400_000_000,
    },
]


def main() -> None:
    with SessionLocal.begin() as session:
        session.execute(delete(Activity))
        session.execute(delete(FileRecord))
        session.execute(delete(AssetVersion))
        session.execute(delete(Asset))
        session.execute(delete(Tag))
        session.execute(delete(User))

        owner = User(name="李明", email="liming@sage.lab")
        session.add(owner)
        session.flush()
        tag_index: dict[str, Tag] = {}
        now = datetime.now(UTC)

        for index, record in enumerate(ASSETS):
            tags = []
            for name in record["tags"]:
                tag = tag_index.setdefault(name, Tag(name=name))
                tags.append(tag)
            updated_at = now - timedelta(hours=index * 7)
            asset = Asset(
                type=record["type"],
                slug=record["slug"],
                title=record["title"],
                summary=record["summary"],
                status=record["status"],
                visibility=Visibility.LAB,
                owner=owner,
                details=record["details"],
                tags=tags,
                updated_at=updated_at,
            )
            asset.versions.append(AssetVersion(version=record["version"], is_current=True))
            asset.files.append(
                FileRecord(
                    relative_path=f"{record['type'].value}/{record['slug']}/README.md",
                    file_name="README.md",
                    file_kind="documentation",
                    mime_type="text/markdown",
                    file_size=record["size"],
                    health_status=HealthStatus.HEALTHY,
                )
            )
            session.add(asset)
            session.flush()
            session.add(
                Activity(
                    asset=asset,
                    actor=owner,
                    action="archived" if index > 1 else "updated",
                    description="更新了归档元数据" if index else "提交了最终论文版本",
                    created_at=updated_at,
                )
            )

    print("Seeded SAGE demo catalogue.")


if __name__ == "__main__":
    main()
