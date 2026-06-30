from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from models import (
    Category,
    MetroStation,
    MotorcycleSchool,
    SchoolCategory,
    SchoolMetroStation,
    SchoolSocialLink,
    SocialNetworkType,
)


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "moto.sqlite"
DATABASE_URL = f"sqlite:///{DB_PATH}"

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


@app.get("/health")
def health(session: SessionDep) -> dict:
    schools_count = session.scalar(select(func.count()).select_from(MotorcycleSchool))
    return {"status": "ok", "schools_count": schools_count}


@app.get("/autocomplete/metro")
def autocomplete_metro(
    session: SessionDep,
    q: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    stmt = select(MetroStation).order_by(MetroStation.name).limit(limit)
    if q:
        stmt = stmt.where(MetroStation.name.ilike(f"%{q}%"))

    return [
        {
            "id": station.id,
            "yandex_id": station.yandex_id,
            "name": station.name,
            "type": station.type,
            "color": station.color,
            "longitude": station.longitude,
            "latitude": station.latitude,
        }
        for station in session.scalars(stmt).all()
    ]


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
    stmt = (
        select(MotorcycleSchool)
        .where(MotorcycleSchool.title.ilike(f"%{q}%"))
        .order_by(
            MotorcycleSchool.rating_value.desc().nullslast(),
            MotorcycleSchool.title,
        )
        .limit(limit)
    )

    return [
        {
            "id": school.id,
            "yandex_id": school.yandex_id,
            "title": school.title,
            "address": school.address,
            "avatar_url": school.avatar_url,
            "rating_value": school.rating_value,
        }
        for school in session.scalars(stmt).all()
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

    if search:
        filters.append(MotorcycleSchool.title.ilike(f"%{search}%"))

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

    base_stmt = select(MotorcycleSchool).where(*filters)
    total = session.scalar(
        select(func.count()).select_from(base_stmt.subquery())
    )

    offset = (page - 1) * per_page
    order_by = build_school_order_by(sort_by, sort_order, metro_station_id)
    schools = session.scalars(
        base_stmt.options(
            selectinload(MotorcycleSchool.categories).selectinload(
                SchoolCategory.category
            ),
            selectinload(MotorcycleSchool.metro_stations).selectinload(
                SchoolMetroStation.station
            ),
            selectinload(MotorcycleSchool.phones),
            selectinload(MotorcycleSchool.social_links).selectinload(
                SchoolSocialLink.type
            ),
            selectinload(MotorcycleSchool.urls),
        )
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
        "items": [serialize_school(school) for school in schools],
    }


@app.get("/schools/{school_id}")
def get_school(school_id: int, session: SessionDep) -> dict:
    school = session.scalar(
        select(MotorcycleSchool)
        .where(MotorcycleSchool.id == school_id)
        .options(
            selectinload(MotorcycleSchool.categories).selectinload(
                SchoolCategory.category
            ),
            selectinload(MotorcycleSchool.metro_stations).selectinload(
                SchoolMetroStation.station
            ),
            selectinload(MotorcycleSchool.phones),
            selectinload(MotorcycleSchool.social_links).selectinload(
                SchoolSocialLink.type
            ),
            selectinload(MotorcycleSchool.urls),
        )
    )
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")

    return serialize_school(school)


def serialize_school(school: MotorcycleSchool) -> dict:
    return {
        "id": school.id,
        "yandex_id": school.yandex_id,
        "title": school.title,
        "address": school.address,
        "additional_address": school.additional_address,
        "seoname": school.seoname,
        "avatar_url": school.avatar_url,
        "coordinates": {
            "longitude": school.longitude,
            "latitude": school.latitude,
        },
        "rating": {
            "rating_count": school.rating_count,
            "rating_value": school.rating_value,
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
            {
                "id": link.station.id,
                "yandex_id": link.station.yandex_id,
                "name": link.station.name,
                "distance": link.distance,
                "distance_value": link.distance_value,
                "color": link.station.color,
            }
            for link in sorted(school.metro_stations, key=lambda item: item.position)
        ],
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
