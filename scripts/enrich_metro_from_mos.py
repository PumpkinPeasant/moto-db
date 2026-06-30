"""Шаг 4 пайплайна сборки БД (опционально, обновляет справочные поля).

Берёт JSON-выгрузку датасета 1488 с data.mos.ru («Станции Московского
метрополитена») и проставляет у уже импортированных станций line_id,
district_id и is_active. Линию определяем по цвету, который Яндекс
отдал в source_payload (для жёлтых 8/8А — доразрешение по названию).
Округа/районы создаются на лету через get_or_create; имя района
сохраняется без слова «район». Скрипт идемпотентен — повторный запуск
даёт 0 изменений.

Запуск:
    python -m scripts.enrich_metro_from_mos --input path/to/data-1488.json
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.models import (  # noqa: E402
    AdministrativeArea,
    Base,
    District,
    MetroLine,
    MetroStation,
    SchoolMetroStation,
)
from scripts.metro_lines_data import (  # noqa: E402
    LINE_COLOR_BY_NAME,
    METRO_LINES,
    strip_district_name,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR.parent / "moto.sqlite"


def normalize(name: str) -> str:
    return name.strip().lower().replace("ё", "е")


def seed_lines(session: Session) -> dict[str, MetroLine]:
    existing = {line.name: line for line in session.scalars(select(MetroLine))}
    for name, number, color in METRO_LINES:
        if name in existing:
            continue
        line = MetroLine(name=name, number=number, color=color)
        session.add(line)
        existing[name] = line
    session.flush()
    return existing


def build_reference(rows: list[dict]) -> dict[str, list[dict]]:
    reference: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        reference[normalize(row["Station"])].append(
            {
                "line_name": row["Line"],
                "district_raw": row.get("District"),
                "adm_area": row.get("AdmArea"),
                "status": row.get("ObjectStatus"),
            }
        )
    return reference


def get_or_create_adm_area(
    session: Session, cache: dict[str, AdministrativeArea], name: str
) -> AdministrativeArea:
    if name in cache:
        return cache[name]
    area = session.scalar(select(AdministrativeArea).where(AdministrativeArea.name == name))
    if area is None:
        area = AdministrativeArea(name=name)
        session.add(area)
        session.flush()
    cache[name] = area
    return area


def get_or_create_district(
    session: Session,
    cache: dict[tuple[str, int], District],
    name: str,
    adm_area: AdministrativeArea,
) -> District:
    key = (name, adm_area.id)
    if key in cache:
        return cache[key]
    district = session.scalar(
        select(District).where(
            District.name == name, District.adm_area_id == adm_area.id
        )
    )
    if district is None:
        district = District(name=name, adm_area_id=adm_area.id)
        session.add(district)
        session.flush()
    cache[key] = district
    return district


def color_for_station(station: MetroStation, session: Session) -> str | None:
    payload = session.scalar(
        select(SchoolMetroStation.source_payload).where(
            SchoolMetroStation.station_id == station.id
        )
    )
    if not payload:
        return None
    color = payload.get("color")
    return color.lower() if color else None


def pick_candidate(
    candidates: list[dict], color: str | None
) -> tuple[dict | None, str | None]:
    if len(candidates) == 1:
        return candidates[0], None

    if not color:
        return None, "нет цвета у станции"

    matched = [
        c for c in candidates if LINE_COLOR_BY_NAME.get(c["line_name"]) == color
    ]
    if not matched:
        return None, f"цвет {color} не соответствует ни одному кандидату"
    if len(matched) == 1:
        return matched[0], None
    return matched[0], None


def enrich(session: Session, reference: dict[str, list[dict]]) -> dict[str, list]:
    lines_by_name = {line.name: line for line in session.scalars(select(MetroLine))}
    adm_cache: dict[str, AdministrativeArea] = {
        a.name: a for a in session.scalars(select(AdministrativeArea))
    }
    district_cache: dict[tuple[str, int], District] = {}
    for d in session.scalars(select(District)):
        district_cache[(d.name, d.adm_area_id)] = d

    stations = session.scalars(select(MetroStation)).all()

    report: dict[str, list] = {
        "updated": [],
        "unchanged": [],
        "unmatched": [],
        "ambiguous": [],
    }

    for station in stations:
        candidates = reference.get(normalize(station.name)) or []

        if not candidates:
            report["unmatched"].append(station.name)
            continue

        info, reason = pick_candidate(candidates, color_for_station(station, session))
        if info is None:
            report["ambiguous"].append(
                {
                    "name": station.name,
                    "yandex_id": station.yandex_id,
                    "reason": reason,
                    "candidates": [
                        f"{c['line_name']} ({c['district_raw']})" for c in candidates
                    ],
                }
            )
            continue

        line = lines_by_name.get(info["line_name"])
        adm_area = get_or_create_adm_area(session, adm_cache, info["adm_area"])
        district = get_or_create_district(
            session, district_cache, strip_district_name(info["district_raw"]), adm_area
        )
        is_active = info["status"] == "действует"

        changed = False
        if station.line_id != (line.id if line else None):
            station.line = line
            changed = True
        if station.district_id != district.id:
            station.district = district
            changed = True
        if station.is_active != is_active:
            station.is_active = is_active
            changed = True

        report["updated" if changed else "unchanged"].append(station.name)

    session.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="JSON-выгрузка датасета 1488 с data.mos.ru",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    engine = create_engine(f"sqlite:///{args.db}")
    Base.metadata.create_all(engine)

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    reference = build_reference(rows)

    with Session(engine) as session:
        seed_lines(session)
        session.commit()
        report = enrich(session, reference)

    total = sum(len(v) for v in report.values())
    print(f"Всего станций в БД: {total}")
    print(f"  обновлено:        {len(report['updated'])}")
    print(f"  без изменений:    {len(report['unchanged'])}")
    print(f"  без матча:        {len(report['unmatched'])}")
    print(f"  неоднозначные:    {len(report['ambiguous'])}")

    if report["unmatched"]:
        print("\nНе нашли в справочнике:")
        for name in sorted(set(report["unmatched"])):
            print(f"  - {name}")

    if report["ambiguous"]:
        print("\nДубли имён (не удалось разрулить):")
        for item in report["ambiguous"]:
            print(f"  - {item['name']} [yandex_id={item['yandex_id']}] — {item['reason']}")
            for candidate in item["candidates"]:
                print(f"      → {candidate}")


if __name__ == "__main__":
    main()
