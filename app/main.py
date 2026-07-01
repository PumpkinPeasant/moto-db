from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated

import cyrtranslit
from fastapi import Depends, FastAPI, HTTPException, Query
from rapidfuzz import fuzz
from sqlalchemy import case, create_engine, func, or_, select
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
    SchoolReview,
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

# Оценка MotoGuide (пока = среднее всех оценок отзывов школы) как коррелированный
# подзапрос — используется для фильтрации и сортировки списка.
MOTO_GUIDE_RATING_EXPR = (
    select(func.avg(SchoolReview.rating))
    .where(
        SchoolReview.school_id == MotorcycleSchool.id,
        SchoolReview.rating.is_not(None),
    )
    .correlate(MotorcycleSchool)
    .scalar_subquery()
)

SCHOOL_SORT_FIELDS = {
    "title": MotorcycleSchool.title,
    "address": MotorcycleSchool.address,
    "rating_value": MotorcycleSchool.rating_value,
    "moto_guide_rating": MOTO_GUIDE_RATING_EXPR,
    "rating_count": MotorcycleSchool.rating_count,
    "review_count": MotorcycleSchool.review_count,
    "created_at": MotorcycleSchool.created_at,
    "updated_at": MotorcycleSchool.updated_at,
}
SCHOOL_SORT_FIELD_NAMES = (*SCHOOL_SORT_FIELDS.keys(), "metro_distance")
SORT_ORDERS = ("asc", "desc")
TITLE_SEARCH_MIN_SCORE = 0.72
MOTORCYCLE_SCHOOL_CATEGORY_SEONAME = "motorcycle_school"
DRIVING_SCHOOL_CATEGORY_SEONAME = "driving_school"
POSITIVE_REVIEW_MIN_RATING = 4

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
            "slug": school.slug,
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
    only_moto: bool = Query(default=False),
    metro_station_id: list[int] | None = Query(default=None),
    social_type_code: list[str] | None = Query(default=None),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    max_rating: float | None = Query(default=None, ge=0, le=5),
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

    if only_moto:
        filters.append(
            MotorcycleSchool.categories.any(
                SchoolCategory.category.has(
                    Category.seoname == MOTORCYCLE_SCHOOL_CATEGORY_SEONAME
                )
            )
        )
        filters.append(
            ~MotorcycleSchool.categories.any(
                SchoolCategory.category.has(
                    Category.seoname == DRIVING_SCHOOL_CATEGORY_SEONAME
                )
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
        filters.append(MOTO_GUIDE_RATING_EXPR >= min_rating)

    if max_rating is not None:
        filters.append(MOTO_GUIDE_RATING_EXPR <= max_rating)

    offset = (page - 1) * per_page
    order_by = build_school_order_by(sort_by, sort_order, metro_station_id)
    base_stmt = select(MotorcycleSchool).where(*filters)

    if search:
        schools = session.scalars(base_stmt.options(*school_load_options())).all()
        matched_schools = filter_schools_by_title_search(search, schools)
        if sort_by == "moto_guide_rating":
            ratings = compute_moto_guide_ratings(
                session, [school.id for school in matched_schools]
            )
            for school in matched_schools:
                school.moto_guide_rating = ratings.get(school.id)
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

    moto_guide_ratings = compute_moto_guide_ratings(
        session, [school.id for school in schools_page]
    )

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "items": [
            serialize_school(school, moto_guide_ratings.get(school.id))
            for school in schools_page
        ],
    }


@app.get("/analytics/reviews/by-date")
def reviews_by_date(
    session: SessionDep,
    school_id: list[int] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    only_moto: bool = Query(default=False),
) -> dict:
    filters = build_review_analytics_filters(
        school_id=school_id,
        date_from=date_from,
        date_to=date_to,
        only_moto=only_moto,
    )
    review_date = func.date(SchoolReview.updated_time).label("date")
    rows = session.execute(
        select(
            review_date,
            func.count().label("total"),
            func.sum(
                case_when_review_is_positive(1, 0)
            ).label("positive"),
            func.sum(
                case_when_review_is_negative(1, 0)
            ).label("negative"),
            func.avg(SchoolReview.rating).label("average_rating"),
            func.sum(case((SchoolReview.rating == 5, 1), else_=0)).label("rating_5"),
            func.sum(case((SchoolReview.rating == 4, 1), else_=0)).label("rating_4"),
            func.sum(case((SchoolReview.rating == 3, 1), else_=0)).label("rating_3"),
            func.sum(case((SchoolReview.rating == 2, 1), else_=0)).label("rating_2"),
            func.sum(case((SchoolReview.rating == 1, 1), else_=0)).label("rating_1"),
        )
        .where(*filters, SchoolReview.updated_time.is_not(None))
        .group_by(review_date)
        .order_by(review_date.asc())
    ).all()

    return {
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "school_id": school_id,
        "only_moto": only_moto,
        "items": [
            {
                "date": row.date,
                "total": row.total,
                "positive": row.positive or 0,
                "negative": row.negative or 0,
                "average_rating": round_float(row.average_rating, 2),
                "ratings": {
                    "5": row.rating_5 or 0,
                    "4": row.rating_4 or 0,
                    "3": row.rating_3 or 0,
                    "2": row.rating_2 or 0,
                    "1": row.rating_1 or 0,
                },
            }
            for row in rows
        ],
    }


@app.get("/analytics/reviews/sentiment")
def review_sentiment_distribution(
    session: SessionDep,
    school_id: list[int] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    only_moto: bool = Query(default=False),
) -> dict:
    filters = build_review_analytics_filters(
        school_id=school_id,
        date_from=date_from,
        date_to=date_to,
        only_moto=only_moto,
    )
    row = session.execute(
        select(
            func.count().label("total"),
            func.sum(case_when_review_is_positive(1, 0)).label("positive"),
            func.sum(case_when_review_is_negative(1, 0)).label("negative"),
        ).where(*filters)
    ).one()
    total = row.total or 0
    positive = row.positive or 0
    negative = row.negative or 0

    return {
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "school_id": school_id,
        "only_moto": only_moto,
        "total": total,
        "items": [
            {
                "type": "positive",
                "label": "Положительные",
                "count": positive,
                "share": round_float(positive / total, 4) if total else 0,
            },
            {
                "type": "negative",
                "label": "Отрицательные",
                "count": negative,
                "share": round_float(negative / total, 4) if total else 0,
            },
        ],
    }


@app.get("/analytics/reviews/rating-distribution")
def review_rating_distribution(
    session: SessionDep,
    school_id: list[int] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    only_moto: bool = Query(default=False),
) -> dict:
    filters = build_review_analytics_filters(
        school_id=school_id,
        date_from=date_from,
        date_to=date_to,
        only_moto=only_moto,
    )
    rows = session.execute(
        select(SchoolReview.rating, func.count().label("count"))
        .where(*filters)
        .group_by(SchoolReview.rating)
    ).all()
    counts = {int(row.rating): row.count for row in rows if row.rating is not None}
    total = sum(counts.values())

    return {
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "school_id": school_id,
        "only_moto": only_moto,
        "total": total,
        "items": [
            {
                "rating": star,
                "count": counts.get(star, 0),
                "share": round_float(counts.get(star, 0) / total, 4) if total else 0,
            }
            for star in (5, 4, 3, 2, 1)
        ],
    }


def build_review_analytics_filters(
    school_id: list[int] | None,
    date_from: date | None,
    date_to: date | None,
    only_moto: bool,
) -> list:
    filters = [SchoolReview.rating.is_not(None)]
    if school_id:
        filters.append(SchoolReview.school_id.in_(school_id))
    if date_from:
        filters.append(func.date(SchoolReview.updated_time) >= date_from.isoformat())
    if date_to:
        filters.append(func.date(SchoolReview.updated_time) <= date_to.isoformat())
    if only_moto:
        filters.append(
            SchoolReview.school.has(
                MotorcycleSchool.categories.any(
                    SchoolCategory.category.has(
                        Category.seoname == MOTORCYCLE_SCHOOL_CATEGORY_SEONAME
                    )
                )
            )
        )
        filters.append(
            SchoolReview.school.has(
                ~MotorcycleSchool.categories.any(
                    SchoolCategory.category.has(
                        Category.seoname == DRIVING_SCHOOL_CATEGORY_SEONAME
                    )
                )
            )
        )
    return filters


def case_when_review_is_positive(positive_value: int, negative_value: int):
    return review_rating_case(
        SchoolReview.rating >= POSITIVE_REVIEW_MIN_RATING,
        positive_value,
        negative_value,
    )


def case_when_review_is_negative(positive_value: int, negative_value: int):
    return review_rating_case(
        SchoolReview.rating < POSITIVE_REVIEW_MIN_RATING,
        positive_value,
        negative_value,
    )


def review_rating_case(condition, positive_value: int, negative_value: int):
    return case((condition, positive_value), else_=negative_value)


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


@app.get("/schools/by-seoname/{seoname}")
def get_school_by_seoname(
    seoname: str,
    session: SessionDep,
    yandex_id: str | None = Query(default=None),
) -> dict:
    filters = [MotorcycleSchool.seoname == seoname]
    if yandex_id:
        filters.append(MotorcycleSchool.yandex_id == yandex_id)

    schools = session.scalars(
        select(MotorcycleSchool)
        .where(*filters)
        .options(*school_load_options())
        .order_by(
            MotorcycleSchool.rating_value.desc().nullslast(),
            MotorcycleSchool.review_count.desc().nullslast(),
            MotorcycleSchool.title.asc(),
            MotorcycleSchool.id.asc(),
        )
    ).all()
    if not schools:
        raise HTTPException(status_code=404, detail="School not found")

    result = serialize_school(
        schools[0], moto_guide_rating_for(session, schools[0].id)
    )
    result["matches_total"] = len(schools)
    return result


@app.get("/schools/by-slug/{slug}")
def get_school_by_slug(slug: str, session: SessionDep) -> dict:
    school = session.scalar(
        select(MotorcycleSchool)
        .where(MotorcycleSchool.slug == slug)
        .options(*school_load_options())
    )
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")

    return serialize_school(school, moto_guide_rating_for(session, school.id))


@app.get("/schools/{school_id}")
def get_school(school_id: int, session: SessionDep) -> dict:
    school = session.scalar(
        select(MotorcycleSchool)
        .where(MotorcycleSchool.id == school_id)
        .options(*school_load_options())
    )
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")

    return serialize_school(school, moto_guide_rating_for(session, school.id))


REVIEW_SORT_FIELDS = {
    "date": SchoolReview.updated_time,
    "rating": SchoolReview.rating,
    "likes": SchoolReview.likes,
}


@app.get("/schools/{school_id}/reviews")
def list_school_reviews(
    school_id: int,
    session: SessionDep,
    search: str | None = Query(default=None, min_length=1),
    rating: int | None = Query(default=None, ge=1, le=5),
    sort_by: str = Query(default="date", enum=tuple(REVIEW_SORT_FIELDS.keys())),
    sort_order: str = Query(default="desc", enum=SORT_ORDERS),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=50),
) -> dict:
    filters = [SchoolReview.school_id == school_id]
    if rating is not None:
        filters.append(SchoolReview.rating == rating)
    if search:
        filters.append(SchoolReview.text.ilike(f"%{search}%"))

    sort_column = REVIEW_SORT_FIELDS[sort_by]
    primary_sort = (
        sort_column.asc().nullslast()
        if sort_order == "asc"
        else sort_column.desc().nullslast()
    )
    order_by = [primary_sort, SchoolReview.id.desc()]

    base_stmt = select(SchoolReview).where(*filters)
    total = session.scalar(select(func.count()).select_from(base_stmt.subquery()))
    offset = (page - 1) * per_page
    reviews = session.scalars(
        base_stmt.order_by(*order_by).offset(offset).limit(per_page)
    ).all()

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "items": [serialize_review(review) for review in reviews],
    }


YAPIC_AVATAR_SIZE = "islands-68"


def resolve_avatar_url(url: str | None, size: str = YAPIC_AVATAR_SIZE) -> str | None:
    """Подставляет размер в шаблон аватарки Яндекса (…/get-yapic/…/{size})."""
    if not url:
        return url
    return url.replace("{size}", size)


def serialize_review(review: SchoolReview) -> dict:
    return {
        "id": review.id,
        "author_name": review.author_name,
        "author_avatar_url": resolve_avatar_url(review.author_avatar_url),
        "text": review.text,
        "rating": review.rating,
        "updated_time": review.updated_time.isoformat() if review.updated_time else None,
        "likes": review.likes,
        "dislikes": review.dislikes,
        "review_url": review.review_url,
    }


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


def compute_moto_guide_ratings(
    session: Session, school_ids: list[int]
) -> dict[int, float | None]:
    """MotoGuide-оценка (пока = среднее всех оценок отзывов, округление до 3 знаков)."""
    if not school_ids:
        return {}

    rows = session.execute(
        select(
            SchoolReview.school_id,
            func.avg(SchoolReview.rating).label("average_rating"),
        )
        .where(
            SchoolReview.school_id.in_(school_ids),
            SchoolReview.rating.is_not(None),
        )
        .group_by(SchoolReview.school_id)
    ).all()

    return {row.school_id: round_float(row.average_rating, 3) for row in rows}


def moto_guide_rating_for(session: Session, school_id: int) -> float | None:
    return compute_moto_guide_ratings(session, [school_id]).get(school_id)


def serialize_school(
    school: MotorcycleSchool, moto_guide_rating: float | None = None
) -> dict:
    nearest_metro_link = get_nearest_school_metro_link(school.metro_stations)
    return {
        "id": school.id,
        "yandex_id": school.yandex_id,
        "title": school.title,
        "address": school.address,
        "additional_address": school.additional_address,
        "seoname": school.seoname,
        "slug": school.slug,
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
            "moto_guide_value": moto_guide_rating,
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
            for link in sorted(school.metro_stations, key=school_metro_distance_sort_key)
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
