import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parent.parent))

from models import (
    Base,
    Category,
    MetroStation,
    MotorcycleSchool,
    SchoolCategory,
    SchoolMetroStation,
    SocialNetworkType,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "extracted" / "motorcycle_schools.json"
DEFAULT_DB = BASE_DIR.parent / "moto.sqlite"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if args.reset and args.db.exists():
        args.db.unlink()

    engine = create_engine(f"sqlite:///{args.db}")
    Base.metadata.create_all(engine)

    rows = json.loads(args.input.read_text(encoding="utf-8"))

    with Session(engine) as session:
        categories = load_categories(session)
        metro_stations = load_metro_stations(session)
        social_types = load_social_types(session)

        for row in rows:
            yandex_id = str(row["id"])
            existing_school = session.scalar(
                select(MotorcycleSchool).where(MotorcycleSchool.yandex_id == yandex_id)
            )
            if existing_school is not None:
                continue

            school = MotorcycleSchool.from_extracted_row(row)

            for category_payload in row.get("categories") or []:
                category = get_or_create_category(session, categories, category_payload)
                school.categories.append(SchoolCategory(category=category))

            for position, metro_payload in enumerate(row.get("metro") or []):
                station = get_or_create_metro_station(
                    session, metro_stations, metro_payload
                )
                school.metro_stations.append(
                    SchoolMetroStation.from_yandex_metro(
                        position, metro_payload, station
                    )
                )

            for social_link in school.social_links:
                get_or_create_social_type(session, social_types, social_link.type_code)

            session.add(school)

        session.commit()

    print(f"SQLite DB: {args.db}")
    print(f"Imported schools: {len(rows)}")


def load_categories(session: Session) -> dict[str, Category]:
    return {
        category.yandex_id: category
        for category in session.scalars(select(Category)).all()
    }


def load_metro_stations(session: Session) -> dict[str, MetroStation]:
    return {
        station.yandex_id: station
        for station in session.scalars(select(MetroStation)).all()
    }


def load_social_types(session: Session) -> dict[str, SocialNetworkType]:
    return {
        social_type.code: social_type
        for social_type in session.scalars(select(SocialNetworkType)).all()
    }


def get_or_create_category(
    session: Session,
    categories: dict[str, Category],
    payload: dict,
) -> Category:
    yandex_id = str(payload["id"])
    category = categories.get(yandex_id)
    if category is None:
        category = Category.from_yandex_category(payload)
        session.add(category)
        categories[yandex_id] = category
    return category


def get_or_create_metro_station(
    session: Session,
    metro_stations: dict[str, MetroStation],
    payload: dict,
) -> MetroStation:
    yandex_id = str(payload["id"])
    station = metro_stations.get(yandex_id)
    if station is None:
        station = MetroStation.from_yandex_metro(payload)
        session.add(station)
        metro_stations[yandex_id] = station
    return station


def get_or_create_social_type(
    session: Session,
    social_types: dict[str, SocialNetworkType],
    code: str,
) -> SocialNetworkType:
    social_type = social_types.get(code)
    if social_type is None:
        social_type = SocialNetworkType.from_code(code)
        session.add(social_type)
        social_types[code] = social_type
    return social_type


if __name__ == "__main__":
    main()
