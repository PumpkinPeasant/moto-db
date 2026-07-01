"""Seed static motorcycle fleet catalog.

Fill FLEET with known motorcycles when the source list is ready.

    python -m scripts.seed_fleet
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.models import Base, FleetMotorcycle  # noqa: E402


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "moto.sqlite"

FLEET: tuple[dict, ...] = ()


def main() -> None:
    engine = create_engine(f"sqlite:///{DEFAULT_DB}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seeded = seed_fleet(session)
        session.commit()

    print(f"Seeded fleet motorcycles: {seeded}")


def seed_fleet(session: Session) -> int:
    existing = {
        motorcycle.code: motorcycle
        for motorcycle in session.scalars(select(FleetMotorcycle)).all()
    }
    updated = 0
    for position, item in enumerate(FLEET):
        code = item["code"]
        values = {
            "brand": item["brand"],
            "model": item["model"],
            "display_name": item.get("display_name")
            or f"{item['brand']} {item['model']}",
            "category": item.get("category"),
            "engine_cc": item.get("engine_cc"),
            "position": item.get("position", position),
            "is_active": item.get("is_active", True),
        }

        motorcycle = existing.get(code)
        if motorcycle is None:
            session.add(FleetMotorcycle(code=code, **values))
            updated += 1
            continue

        changed = False
        for field, value in values.items():
            if getattr(motorcycle, field) != value:
                setattr(motorcycle, field, value)
                changed = True
        if changed:
            updated += 1

    return updated


if __name__ == "__main__":
    main()
