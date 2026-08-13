from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.domain.models import Asset, PublicationIdentityKey
from scripts import seed_demo


def test_demo_seed_is_repeatable_on_sqlite(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_demo, "SessionLocal", session_factory)

    seed_demo.main()
    seed_demo.main()

    with session_factory() as session:
        assert session.scalar(select(func.count(Asset.id))) == len(seed_demo.ASSETS)
        assert session.scalar(select(func.count(PublicationIdentityKey.id))) == 4
