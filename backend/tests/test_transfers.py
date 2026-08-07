import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, User
from app.domain.schemas import UploadCommandRequest
from app.services.transfers import UploadCommandError, generate_upload_command


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def create_asset(session: Session) -> Asset:
    asset = Asset(
        type=AssetType.DATASET,
        slug="soil-samples-2026",
        title="土壤样本观测数据集",
        summary="用于验证上传命令。",
        status="draft",
        visibility=Visibility.LAB,
        owner=User(name="归档管理员", email="archive-admin@sage.lab"),
    )
    session.add(asset)
    session.flush()
    return asset


def test_generate_upload_command_for_registered_asset() -> None:
    session = make_session()
    asset = create_asset(session)

    result = generate_upload_command(
        session,
        UploadCommandRequest(
            asset_id=asset.id,
            source_path="/mnt/research data/samples.csv",
            target_subdirectory="raw/2026-08",
        ),
        ssh_host="192.168.1.213",
        ssh_user="zhengyu",
        ssh_port=22,
        destination_root="/srv/sage-archive",
    )

    assert result.archive_relative_path == "dataset/soil-samples-2026/raw/2026-08"
    assert "ssh -p 22 zhengyu@192.168.1.213" in result.command
    assert "mkdir -p" in result.command
    assert "scp -P 22 -- '/mnt/research data/samples.csv'" in result.command
    assert (
        "zhengyu@192.168.1.213:/srv/sage-archive/dataset/soil-samples-2026/raw/2026-08/"
        in result.command
    )


def test_generate_upload_command_rejects_directory_escape() -> None:
    session = make_session()
    asset = create_asset(session)

    with pytest.raises(UploadCommandError, match="相对路径"):
        generate_upload_command(
            session,
            UploadCommandRequest(
                asset_id=asset.id, source_path="/tmp/file", target_subdirectory="../escape"
            ),
            ssh_host="192.168.1.213",
            ssh_user="zhengyu",
            ssh_port=22,
            destination_root="/srv/sage-archive",
        )


def test_generate_upload_command_requires_a_type_specific_directory() -> None:
    session = make_session()
    asset = create_asset(session)

    with pytest.raises(UploadCommandError, match="dataset 资产的一级归档目录"):
        generate_upload_command(
            session,
            UploadCommandRequest(
                asset_id=asset.id,
                source_path="/tmp/notes.pdf",
                target_subdirectory="manuscript",
            ),
            ssh_host="192.168.1.213",
            ssh_user="zhengyu",
            ssh_port=22,
            destination_root="/srv/sage-archive",
        )
