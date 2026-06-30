from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import cyrtranslit
from fastapi import Depends, FastAPI, HTTPException, Query
from rapidfuzz import fuzz
from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.models import (
    AdministrativeArea,
    Category,
    District,
    MetroLine,
    MetroStation,
    MotorcycleSchool,
    SchoolCategory,
    SchoolMetroStation,
    SchoolSocialLink,
    SocialNetworkType,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "moto.sqlite"
DATABASE_URL = f"sqlite:///{DB_PATH}"
YANDEX_MAPS_ORG_URL = "https://yandex.ru/maps/org"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

app = FastAPI(title="Moto DB API")


def get_session() -> Session:
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Database not found: {DB_PATH}")

    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

SCHOOL_SORT_FIELDS = {
    "title": MotorcycleSchool.title,
    "address": MotorcycleSchool.address,
    "rating_value": MotorcycleSchool.rating_value,
    "rating_count": MotorcycleSchool.rating_count,
    "review_count": MotorcycleSchool.review_count,
    "created_at": MotorcycleSchool.created_at,
    "updated_at": MotorcycleSchool.updated_at,
}
SCHOOL_SORT_FIELD_NAMES = (*SCHOOL_SORT_FIELDS.keys(), "metro_distance")
SORT_ORDERS = ("asc", "desc")
TITLE_SEARCH_MIN_SCORE = 0.72

EN_KEYBOARD = "`qwertyuiop[]asdfghjkl;'zxcvbnm,."
RU_KEYBOARD = "ёйцукенгшщзхъфывапролджэячсмитьбю"
EN_TO_RU_KEYBOARD = str.maketrans(EN_KEYBOARD, RU_KEYBOARD)
RU_TO_EN_KEYBOARD = str.maketrans(RU_KEYBOARD, EN_KEYBOARD)


@app.get("/health")
def health(session: SessionDep) -> dict:
    schools_count = session.scalar(select(func.count()).select_from(MotorcycleSchool))
    return {"status": "ok", "schools_count": schools_count}


@app.get("/autocomplete/metro")
def autocomplete_metro(
    session: SessionDep,
    q: str | None = Query(default=None, min_length=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
) -> dict:
    base_stmt = select(MetroStation).outerjoin(MetroStation.line)
    if q:
        base_stmt = base_stmt.where(MetroStation.name.ilike(f"%{q}%"))

    total = session.scalar(select(func.count()).select_from(base_stmt.subquery()))
    total = total or 0

    offset = (page - 1) * per_page
    stations = session.scalars(
        base_stmt.options(
            selectinload(MetroStation.line),
            selectinload(MetroStation.district).selectinload(District.adm_area),
        )
        .order_by(
            MetroLine.id.asc().nullslast(),
            MetroStation.name.asc(),
            MetroStation.id.asc(),
        )
        .offset(offset)
        .limit(per_page)
    ).all()

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
        "has_next": offset + len(stations) < total,
        "items": group_stations_by_line(stations),
    }


def group_stations_by_line(stations: list[MetroStation]) -> list[dict]:
    groups: list[dict] = []
    current_line_id: int | None | object = object()
    current_group: dict | None = None
    for station in stations:
        line = station.line
        line_id = line.id if line is not None else None
        if line_id != current_line_id:
            current_group = {
                "line": serialize_metro_line(line),
                "stations": [],
            }
            groups.append(current_group)
            current_line_id = line_id
        assert current_group is not None
        current_group["stations"].append(serialize_metro_station_in_group(station))
    return groups


def serialize_metro_line(line: MetroLine | None) -> dict | None:
    if line is None:
        return None
    return {
        "id": line.id,
        "name": line.name,
        "number": line.number,
        "color": line.color,
    }


def serialize_metro_station_in_group(station: MetroStation) -> dict:
    district = station.district
    return {
        "id": station.id,
        "yandex_id": station.yandex_id,
        "name": station.name,
        "type": station.type,
        "longitude": station.longitude,
        "latitude": station.latitude,
        "is_active": station.is_active,
        "district": {
            "id": district.id,
            "name": district.name,
            "adm_area": {
                "id": district.adm_area.id,
                "name": district.adm_area.name,
            },
        }
        if district is not None
        else None,
    }


@app.get("/autocomplete/categories")
def autocomplete_categories(
    session: SessionDep,
    q: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    stmt = select(Category).order_by(Category.name).limit(limit)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Category.name.ilike(like), Category.seoname.ilike(like)))

    return [
        {
            "id": category.id,
            "yandex_id": category.yandex_id,
            "name": category.name,
            "class_name": category.class_name,
            "seoname": category.seoname,
            "plural_name": category.plural_name,
        }
        for category in session.scalars(stmt).all()
    ]


@app.get("/autocomplete/social-network-types")
def autocomplete_social_network_types(
    session: SessionDep,
    q: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    stmt = select(SocialNetworkType).order_by(SocialNetworkType.code).limit(limit)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(SocialNetworkType.code.ilike(like), SocialNetworkType.name.ilike(like))
        )

    return [
        {"code": social_type.code, "name": social_type.name}
        for social_type in session.scalars(stmt).all()
    ]


@app.get("/autocomplete/schools")
def autocomplete_schools(
    session: SessionDep,
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    schools = session.scalars(select(MotorcycleSchool)).all()
    matched_schools = [
        school
        for school, _score in sorted(
            (
                (school, score_school_title_match(q, school))
                for school in schools
            ),
            key=lambda item: (
                -item[1],
                -(item[0].rating_value or 0),
                item[0].title.casefold(),
                item[0].id,
            ),
        )
        if _score >= title_search_min_score(q)
    ][:limit]

    return [
        {
            "id": school.id,
            "yandex_id": school.yandex_id,
            "title": school.title,
            "address": school.address,
            "avatar_url": school.avatar_url,
            "rating_value": round_float(school.rating_value, 2),
        }
        for school in matched_schools
    ]


@app.get("/schools")
def list_schools(
    session: SessionDep,
    search: str | None = Query(default=None, min_length=1),
    category_id: list[int] | None = Query(default=None),
    metro_station_id: list[int] | None = Query(default=None),
    social_type_code: list[str] | None = Query(default=None),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="rating_value", enum=SCHOOL_SORT_FIELD_NAMES),
    sort_order: str = Query(default="desc", enum=SORT_ORDERS),
) -> dict:
    filters = []

    if category_id:
        filters.append(
            MotorcycleSchool.categories.any(
                SchoolCategory.category_id.in_(category_id)
            )
        )

    if metro_station_id:
        filters.append(
            MotorcycleSchool.metro_stations.any(
                SchoolMetroStation.station_id.in_(metro_station_id)
            )
        )

    if social_type_code:
        filters.append(
            MotorcycleSchool.social_links.any(
                SchoolSocialLink.type_code.in_(social_type_code)
            )
        )

    if min_rating is not None:
        filters.append(MotorcycleSchool.rating_value >= min_rating)

    offset = (page - 1) * per_page
    order_by = build_school_order_by(sort_by, sort_order, metro_station_id)
    base_stmt = select(MotorcycleSchool).where(*filters)

    if search:
        schools = session.scalars(base_stmt.options(*school_load_options())).all()
        matched_schools = filter_schools_by_title_search(search, schools)
        sorted_schools = sort_schools_in_memory(
            matched_schools, sort_by, sort_order, metro_station_id
        )
        total = len(matched_schools)
        schools_page = sorted_schools[offset : offset + per_page]
    else:
        total = session.scalar(select(func.count()).select_from(base_stmt.subquery()))
        schools_page = session.scalars(
            base_stmt.options(*school_load_options())
            .order_by(*order_by)
            .offset(offset)
            .limit(per_page)
        ).all()

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "items": [serialize_school(school) for school in schools_page],
    }


def filter_schools_by_title_search(
    query: str,
    schools: list[MotorcycleSchool],
) -> list[MotorcycleSchool]:
    min_score = title_search_min_score(query)
    scored_schools = [
        (school, score)
        for school in schools
        if (score := score_school_title_match(query, school)) >= min_score
    ]
    return [
        school
        for school, _score in sorted(
            scored_schools,
            key=lambda item: (
                -item[1],
                -(item[0].rating_value or 0),
                -(item[0].review_count or 0),
                item[0].title.casefold(),
                item[0].id,
            ),
        )
    ]


def sort_schools_in_memory(
    schools: list[MotorcycleSchool],
    sort_by: str,
    sort_order: str,
    metro_station_ids: list[int] | None,
) -> list[MotorcycleSchool]:
    reverse = sort_order == "desc"

    return sorted(
        schools,
        key=lambda school: (
            school_sort_null_rank(
                get_school_sort_value(school, sort_by, metro_station_ids)
            ),
            school_sort_value(
                get_school_sort_value(school, sort_by, metro_station_ids),
                reverse,
            ),
            -(school.review_count or 0),
            school.title.casefold(),
            school.id,
        ),
    )


def school_sort_null_rank(value) -> int:
    return 1 if value is None else 0


def school_sort_value(value, reverse: bool):
    if isinstance(value, str):
        value = value.casefold()
        return "".join(chr(0x10FFFF - ord(char)) for char in value) if reverse else value
    if value is None:
        return None
    comparable_value = value.timestamp() if hasattr(value, "timestamp") else value
    return -comparable_value if reverse else comparable_value


def get_school_sort_value(
    school: MotorcycleSchool,
    sort_by: str,
    metro_station_ids: list[int] | None,
):
    if sort_by == "metro_distance":
        return get_school_nearest_metro_distance(school, metro_station_ids)
    return getattr(school, sort_by)


def score_school_title_match(query: str, school: MotorcycleSchool) -> float:
    query_variants = build_search_variants(query)
    if not query_variants:
        return 0

    haystack_variants = build_search_variants(school.title)
    if school.seoname:
        haystack_variants.update(build_search_variants(school.seoname))

    best_score = 0.0
    for query_variant in query_variants:
        for haystack_variant in haystack_variants:
            if not query_variant or not haystack_variant:
                continue
            best_score = max(
                best_score,
                score_search_variant_match(query_variant, haystack_variant),
            )
            if best_score >= 1:
                return best_score
    return best_score


def score_search_variant_match(query: str, haystack: str) -> float:
    if query == haystack:
        return 1
    if query in haystack:
        return 0.95 if haystack.startswith(query) else 0.9
    if len(query) >= 5 and haystack in query:
        return 0.86
    score = fuzz.ratio(query, haystack) / 100
    if query[0] != haystack[0]:
        score *= 0.8
    return score


def title_search_min_score(query: str) -> float:
    query_length = min((len(item) for item in build_search_variants(query)), default=0)
    if query_length <= 3:
        return 0.9
    if query_length <= 5:
        return 0.8
    if query_length >= 8:
        return 0.77
    return TITLE_SEARCH_MIN_SCORE


def build_search_variants(value: str | None) -> set[str]:
    if not value:
        return set()

    base_values = {
        value,
        value.translate(EN_TO_RU_KEYBOARD),
        value.translate(RU_TO_EN_KEYBOARD),
    }
    variants: set[str] = set()
    for base_value in base_values:
        normalized = normalize_search_text(base_value)
        if not normalized:
            continue
        variants.add(normalized)
        variants.add(cyrtranslit.to_latin(normalized, "ru"))
        variants.add(cyrtranslit.to_cyrillic(normalized, "ru"))
    return {variant for variant in variants if variant}


def normalize_search_text(value: str) -> str:
    normalized = value.casefold().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", "", normalized)


@app.get("/schools/{school_id}")
def get_school(school_id: int, session: SessionDep) -> dict:
    school = session.scalar(
        select(MotorcycleSchool)
        .where(MotorcycleSchool.id == school_id)
        .options(*school_load_options())
    )
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")

    return serialize_school(school)


def school_load_options() -> list:
    metro_station = selectinload(MotorcycleSchool.metro_stations).selectinload(
        SchoolMetroStation.station
    )
    return [
        selectinload(MotorcycleSchool.categories).selectinload(SchoolCategory.category),
        metro_station.selectinload(MetroStation.line),
        metro_station.selectinload(MetroStation.district).selectinload(
            District.adm_area
        ),
        selectinload(MotorcycleSchool.phones),
        selectinload(MotorcycleSchool.social_links).selectinload(SchoolSocialLink.type),
        selectinload(MotorcycleSchool.urls),
    ]


def serialize_school(school: MotorcycleSchool) -> dict:
    nearest_metro_link = get_nearest_school_metro_link(school.metro_stations)
    return {
        "id": school.id,
        "yandex_id": school.yandex_id,
        "title": school.title,
        "address": school.address,
        "additional_address": school.additional_address,
        "seoname": school.seoname,
        "avatar_url": school.avatar_url,
        "map_url": build_yandex_maps_org_url(school),
        "reviews_url": build_yandex_maps_reviews_url(school),
        "coordinates": {
            "longitude": school.longitude,
            "latitude": school.latitude,
        },
        "rating": {
            "rating_count": school.rating_count,
            "rating_value": round_float(school.rating_value, 2),
            "review_count": school.review_count,
        },
        "categories": [
            {
                "id": link.category.id,
                "yandex_id": link.category.yandex_id,
                "name": link.category.name,
                "seoname": link.category.seoname,
            }
            for link in school.categories
        ],
        "metro": [
            serialize_school_metro_link(link)
            for link in sorted(school.metro_stations, key=school_metro_name_sort_key)
        ],
        "nearest_metro": serialize_school_metro_link(nearest_metro_link)
        if nearest_metro_link is not None
        else None,
        "phones": [
            {
                "number": phone.number,
                "value": phone.value,
                "info": phone.info,
                "extra_number": phone.extra_number,
            }
            for phone in sorted(school.phones, key=lambda item: item.position)
        ],
        "social_links": [
            {
                "type_code": link.type_code,
                "type_name": link.type.name,
                "href": link.href,
                "readable_href": link.readable_href,
            }
            for link in sorted(school.social_links, key=lambda item: item.position)
        ],
        "urls": [
            url.url for url in sorted(school.urls, key=lambda item: item.position)
        ],
    }


def build_yandex_maps_org_url(school: MotorcycleSchool) -> str | None:
    if not school.seoname or not school.yandex_id:
        return None
    return f"{YANDEX_MAPS_ORG_URL}/{school.seoname}/{school.yandex_id}/"


def build_yandex_maps_reviews_url(school: MotorcycleSchool) -> str | None:
    org_url = build_yandex_maps_org_url(school)
    if org_url is None:
        return None
    return f"{org_url}reviews/"


def round_float(value: float | None, ndigits: int) -> float | None:
    return round(value, ndigits) if value is not None else None


def get_nearest_school_metro_link(
    links: list[SchoolMetroStation],
    metro_station_ids: list[int] | None = None,
) -> SchoolMetroStation | None:
    matching_links = [
        link
        for link in links
        if metro_station_ids is None or link.station_id in metro_station_ids
    ]
    return min(matching_links, key=school_metro_distance_sort_key, default=None)


def get_school_nearest_metro_distance(
    school: MotorcycleSchool,
    metro_station_ids: list[int] | None = None,
) -> float | None:
    link = get_nearest_school_metro_link(school.metro_stations, metro_station_ids)
    return link.distance_value if link is not None else None


def school_metro_distance_sort_key(link: SchoolMetroStation) -> tuple:
    return (
        link.distance_value is None,
        link.distance_value if link.distance_value is not None else 0,
        link.station.name.casefold(),
        link.station.id,
    )


def school_metro_name_sort_key(link: SchoolMetroStation) -> tuple:
    station = link.station
    line = station.line
    return (
        station.name.casefold(),
        line.number if line is not None else "",
        line.name.casefold() if line is not None else "",
        station.id,
    )


def serialize_school_metro_link(link: SchoolMetroStation) -> dict:
    station = link.station
    line = station.line
    district = station.district
    return {
        "id": station.id,
        "yandex_id": station.yandex_id,
        "name": station.name,
        "distance": link.distance,
        "distance_value": link.distance_value,
        "is_active": station.is_active,
        "line_number": line.number if line is not None else None,
        "line_name": line.name if line is not None else None,
        "line_color": line.color if line is not None else None,
        "line": {
            "id": line.id,
            "name": line.name,
            "number": line.number,
            "color": line.color,
        }
        if line is not None
        else None,
        "district": {
            "id": district.id,
            "name": district.name,
            "adm_area": {
                "id": district.adm_area.id,
                "name": district.adm_area.name,
            },
        }
        if district is not None
        else None,
    }


def build_school_order_by(
    sort_by: str,
    sort_order: str,
    metro_station_ids: list[int] | None,
) -> list:
    sort_expression = get_school_sort_expression(sort_by, metro_station_ids)
    primary_sort = (
        sort_expression.asc().nullslast()
        if sort_order == "asc"
        else sort_expression.desc().nullslast()
    )

    return [
        primary_sort,
        MotorcycleSchool.review_count.desc().nullslast(),
        MotorcycleSchool.title.asc(),
        MotorcycleSchool.id.asc(),
    ]


def get_school_sort_expression(
    sort_by: str,
    metro_station_ids: list[int] | None,
):
    if sort_by != "metro_distance":
        return SCHOOL_SORT_FIELDS[sort_by]

    distance_stmt = select(func.min(SchoolMetroStation.distance_value)).where(
        SchoolMetroStation.school_id == MotorcycleSchool.id
    )
    if metro_station_ids:
        distance_stmt = distance_stmt.where(
            SchoolMetroStation.station_id.in_(metro_station_ids)
        )

    return distance_stmt.scalar_subquery()
