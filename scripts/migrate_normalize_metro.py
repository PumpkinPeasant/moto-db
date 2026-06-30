"""Одноразовая миграция схемы metro_stations (уже выполнена на текущей БД).

Переносит плоские поля line_name/line_number/district/adm_area/status/color
в нормализованные таблицы metro_lines / districts / administrative_areas,
добавляет FK-колонки + is_active на metro_stations, бэкфилит их и дропает
старые колонки и их индексы. Нужен, только если кто-то поднимает БД
с дораздачей старой схемы — для нового пайплайна (импорт + enrich) уже
не нужен. На всякий случай делает бэкап не сам — рассчитывает, что вы
сначала скопировали moto.sqlite.

Запуск:
    cp moto.sqlite moto.sqlite.bak
    python -m scripts.migrate_normalize_metro
"""
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.models import AdministrativeArea, Base, District, MetroLine  # noqa: E402
from scripts.metro_lines_data import METRO_LINES, strip_district_name  # noqa: E402


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR.parent / "moto.sqlite"


NEW_STATION_COLUMNS = {
    "line_id": "INTEGER REFERENCES metro_lines(id)",
    "district_id": "INTEGER REFERENCES districts(id)",
    "is_active": "BOOLEAN NOT NULL DEFAULT 0",
}

NEW_STATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_metro_stations_line_id ON metro_stations (line_id)",
    "CREATE INDEX IF NOT EXISTS ix_metro_stations_district_id ON metro_stations (district_id)",
)

OLD_STATION_INDEXES = (
    "ix_metro_stations_line_number",
    "ix_metro_stations_district",
)

OLD_STATION_COLUMNS = (
    "color",
    "line_name",
    "line_number",
    "district",
    "adm_area",
    "status",
)


def main() -> None:
    engine = create_engine(f"sqlite:///{DEFAULT_DB}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_lines(session)
        session.commit()

        adm_areas, districts = seed_geography(session)
        session.commit()

    ensure_station_columns(engine)

    with engine.begin() as conn:
        backfill_stations(conn)

    drop_old_columns(engine)

    print("Миграция metro_stations завершена.")


def seed_lines(session: Session) -> None:
    existing = {line.name for line in session.scalars(select(MetroLine)).all()}
    for name, number, color in METRO_LINES:
        if name in existing:
            continue
        session.add(MetroLine(name=name, number=number, color=color))


def seed_geography(
    session: Session,
) -> tuple[dict[str, AdministrativeArea], dict[tuple[str, str], District]]:
    raw_rows = session.execute(
        text(
            "SELECT DISTINCT adm_area, district "
            "FROM metro_stations "
            "WHERE adm_area IS NOT NULL AND district IS NOT NULL"
        )
    ).all()

    adm_areas: dict[str, AdministrativeArea] = {
        area.name: area for area in session.scalars(select(AdministrativeArea)).all()
    }
    for adm_area_name, _ in raw_rows:
        if adm_area_name in adm_areas:
            continue
        area = AdministrativeArea(name=adm_area_name)
        session.add(area)
        adm_areas[adm_area_name] = area
    session.flush()

    districts: dict[tuple[str, str], District] = {}
    for district in session.scalars(select(District)).all():
        adm_area = session.get(AdministrativeArea, district.adm_area_id)
        districts[(district.name, adm_area.name)] = district

    for adm_area_name, district_raw in raw_rows:
        clean_name = strip_district_name(district_raw)
        key = (clean_name, adm_area_name)
        if key in districts:
            continue
        district = District(
            name=clean_name, adm_area_id=adm_areas[adm_area_name].id
        )
        session.add(district)
        districts[key] = district

    return adm_areas, districts


def ensure_station_columns(engine) -> None:
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns("metro_stations")}
    with engine.begin() as conn:
        for column, ddl in NEW_STATION_COLUMNS.items():
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE metro_stations ADD COLUMN {column} {ddl}"))
        for index_sql in NEW_STATION_INDEXES:
            conn.execute(text(index_sql))


def backfill_stations(conn) -> None:
    conn.execute(
        text(
            """
            UPDATE metro_stations
            SET line_id = (
                SELECT id FROM metro_lines
                WHERE metro_lines.name = metro_stations.line_name
            )
            WHERE line_name IS NOT NULL
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE metro_stations
            SET district_id = (
                SELECT d.id
                FROM districts d
                JOIN administrative_areas a ON d.adm_area_id = a.id
                WHERE a.name = metro_stations.adm_area
                  AND d.name = CASE
                      WHEN LOWER(SUBSTR(metro_stations.district, 1, 6)) = 'район '
                          THEN TRIM(SUBSTR(metro_stations.district, 7))
                      WHEN LOWER(SUBSTR(metro_stations.district, LENGTH(metro_stations.district) - 5, 6)) = ' район'
                          THEN TRIM(SUBSTR(metro_stations.district, 1, LENGTH(metro_stations.district) - 6))
                      ELSE metro_stations.district
                  END
            )
            WHERE district IS NOT NULL AND adm_area IS NOT NULL
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE metro_stations
            SET is_active = CASE WHEN status = 'действует' THEN 1 ELSE 0 END
            """
        )
    )


def drop_old_columns(engine) -> None:
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns("metro_stations")}
    indexes = {ix["name"] for ix in inspector.get_indexes("metro_stations")}
    with engine.begin() as conn:
        for index_name in OLD_STATION_INDEXES:
            if index_name in indexes:
                conn.execute(text(f"DROP INDEX {index_name}"))
        for column in OLD_STATION_COLUMNS:
            if column not in existing:
                continue
            conn.execute(text(f"ALTER TABLE metro_stations DROP COLUMN {column}"))


if __name__ == "__main__":
    main()
