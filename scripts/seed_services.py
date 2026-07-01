"""Seed static service catalog.

The catalog is intentionally hardcoded for now.

    python -m scripts.seed_services
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.models import Base, Service  # noqa: E402


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "moto.sqlite"

SERVICES = (
    ("category_a", "Категория А"),
    ("category_a1", "Категория А1"),
    ("emergency_training", "Контраварийная подготовка"),
    ("motogymkhana", "Мотоджимхана"),
    ("individual_lesson", "Индивидуальное занятие"),
    ("trial_lesson", "Пробное занятие"),
    ("city_lesson", "Занятие в городе"),
)


def main() -> None:
    engine = create_engine(f"sqlite:///{DEFAULT_DB}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seeded = seed_services(session)
        session.commit()

    print(f"Seeded services: {seeded}")


def seed_services(session: Session) -> int:
    existing = {
        service.code: service for service in session.scalars(select(Service)).all()
    }
    updated = 0
    for position, (code, name) in enumerate(SERVICES):
        service = existing.get(code)
        if service is None:
            session.add(
                Service(
                    code=code,
                    name=name,
                    position=position,
                    is_active=True,
                )
            )
            updated += 1
            continue

        if (
            service.name != name
            or service.position != position
            or not service.is_active
        ):
            service.name = name
            service.position = position
            service.is_active = True
            updated += 1

    return updated


if __name__ == "__main__":
    main()
