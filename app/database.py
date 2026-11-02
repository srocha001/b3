import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


database_url = os.getenv("DATABASE_URL", "sqlite:///./b3_monitor.db")
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    from app.models import User
    from app.services.auth import hash_password

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.query(User).filter_by(username="admin").first() is None:
            db.add(
                User(
                    username="admin",
                    password_hash=hash_password("admin"),
                    must_change_password=True,
                )
            )
            db.commit()
