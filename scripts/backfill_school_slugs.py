"""Backfill unique local slugs for schools.

Yandex seoname is not unique across branches. This script keeps the plain
seoname when it is unique and appends yandex_id for duplicate groups.

    python -m scripts.backfill_school_slugs
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.models import Base, MotorcycleSchool  # noqa: E402


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "moto.sqlite"


def main() -> None:
    engine = create_engine(f"sqlite:///{DEFAULT_DB}")
    ensure_slug_column(engine)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        updated = backfill_slugs(session)
        session.commit()

    ensure_slug_index(engine)
    print(f"Backfilled school slugs: {updated}")


def ensure_slug_column(engine) -> None:
    inspector = inspect(engine)
    existing = {
        column["name"] for column in inspector.get_columns("motorcycle_schools")
    }
    if "slug" in existing:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE motorcycle_schools ADD COLUMN slug TEXT"))


def ensure_slug_index(engine) -> None:
    inspector = inspect(engine)
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("motorcycle_schools")
    }
    if "ix_motorcycle_schools_slug" in existing_indexes:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_motorcycle_schools_slug "
                "ON motorcycle_schools (slug)"
            )
        )


def backfill_slugs(session: Session) -> int:
    schools = session.scalars(select(MotorcycleSchool).order_by(MotorcycleSchool.id)).all()
    base_counts = Counter(build_base_slug(school) for school in schools)
    updated = 0

    for school in schools:
        base_slug = build_base_slug(school)
        slug = (
            f"{base_slug}-{school.yandex_id}"
            if base_counts[base_slug] > 1
            else base_slug
        )
        if school.slug == slug:
            continue
        school.slug = slug
        updated += 1

    return updated


def build_base_slug(school: MotorcycleSchool) -> str:
    return school.seoname or f"school-{school.yandex_id}"


if __name__ == "__main__":
    main()
