from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MotorcycleSchool(Base):
    __tablename__ = "motorcycle_schools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yandex_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    additional_address: Mapped[str | None] = mapped_column(Text)
    seoname: Mapped[str | None] = mapped_column(Text)
    slug: Mapped[str | None] = mapped_column(Text, unique=True)
    avatar_url: Mapped[str | None] = mapped_column(Text)

    longitude: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)

    rating_count: Mapped[int | None] = mapped_column(Integer)
    rating_value: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)

    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

    categories: Mapped[list[SchoolCategory]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )
    metro_stations: Mapped[list[SchoolMetroStation]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )
    phones: Mapped[list[SchoolPhone]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )
    social_links: Mapped[list[SchoolSocialLink]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )
    urls: Mapped[list[SchoolUrl]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[SchoolReview]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_motorcycle_schools_title", "title"),
        Index("ix_motorcycle_schools_geo", "longitude", "latitude"),
        Index("ix_motorcycle_schools_rating_value", "rating_value"),
        Index("ix_motorcycle_schools_seoname", "seoname"),
        Index("ix_motorcycle_schools_slug", "slug"),
    )

    @classmethod
    def from_extracted_row(cls, row: dict[str, Any]) -> "MotorcycleSchool":
        coordinates = row.get("coordinates") or []
        rating_data = row.get("ratingData") or {}

        school = cls(
            yandex_id=str(row["id"]),
            title=row["title"],
            address=row.get("address"),
            additional_address=row.get("additionalAddress"),
            seoname=row.get("seoname"),
            avatar_url=build_avatar_url(row.get("businessImages")),
            longitude=_coordinate(coordinates, 0),
            latitude=_coordinate(coordinates, 1),
            rating_count=rating_data.get("ratingCount"),
            rating_value=rating_data.get("ratingValue"),
            review_count=rating_data.get("reviewCount"),
            source_payload=row,
        )
        school.phones = [
            SchoolPhone.from_yandex_phone(position, phone)
            for position, phone in enumerate(row.get("phones") or [])
        ]
        school.urls = [
            SchoolUrl(position=position, url=url)
            for position, url in enumerate(row.get("urls") or [])
        ]
        school.social_links = [
            SchoolSocialLink.from_yandex_social_link(position, link)
            for position, link in enumerate(row.get("socialLinks") or [])
        ]
        return school


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yandex_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    class_name: Mapped[str | None] = mapped_column(Text)
    seoname: Mapped[str | None] = mapped_column(Text)
    plural_name: Mapped[str | None] = mapped_column(Text)

    schools: Mapped[list[SchoolCategory]] = relationship(back_populates="category")

    __table_args__ = (
        Index("ix_categories_name", "name"),
        Index("ix_categories_seoname", "seoname"),
    )

    @classmethod
    def from_yandex_category(cls, category: dict[str, Any]) -> "Category":
        return cls(
            yandex_id=str(category["id"]),
            name=category["name"],
            class_name=category.get("class"),
            seoname=category.get("seoname"),
            plural_name=category.get("pluralName"),
        )


class SchoolCategory(Base):
    __tablename__ = "school_categories"

    school_id: Mapped[int] = mapped_column(
        ForeignKey("motorcycle_schools.id"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), primary_key=True)

    school: Mapped[MotorcycleSchool] = relationship(back_populates="categories")
    category: Mapped[Category] = relationship(back_populates="schools")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="1", default=True
    )

    __table_args__ = (
        Index("ix_services_name", "name"),
        Index("ix_services_position", "position"),
    )


class MetroLine(Base):
    __tablename__ = "metro_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    number: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    color: Mapped[str | None] = mapped_column(String(16))

    stations: Mapped[list[MetroStation]] = relationship(back_populates="line")


class AdministrativeArea(Base):
    __tablename__ = "administrative_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    districts: Mapped[list[District]] = relationship(back_populates="adm_area")


class District(Base):
    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    adm_area_id: Mapped[int] = mapped_column(
        ForeignKey("administrative_areas.id"), nullable=False
    )

    adm_area: Mapped[AdministrativeArea] = relationship(back_populates="districts")
    metro_stations: Mapped[list[MetroStation]] = relationship(back_populates="district")

    __table_args__ = (
        UniqueConstraint("name", "adm_area_id", name="uq_districts_name_adm_area"),
        Index("ix_districts_name", "name"),
    )


class MetroStation(Base):
    __tablename__ = "metro_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yandex_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(String(64))
    longitude: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)

    line_id: Mapped[int | None] = mapped_column(ForeignKey("metro_lines.id"))
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0", default=False
    )

    line: Mapped[MetroLine | None] = relationship(back_populates="stations")
    district: Mapped[District | None] = relationship(back_populates="metro_stations")
    schools: Mapped[list[SchoolMetroStation]] = relationship(back_populates="station")

    __table_args__ = (
        Index("ix_metro_stations_name", "name"),
        Index("ix_metro_stations_geo", "longitude", "latitude"),
        Index("ix_metro_stations_line_id", "line_id"),
        Index("ix_metro_stations_district_id", "district_id"),
    )

    @classmethod
    def from_yandex_metro(cls, metro: dict[str, Any]) -> "MetroStation":
        coordinates = metro.get("coordinates") or []
        return cls(
            yandex_id=str(metro["id"]),
            name=metro["name"],
            type=metro.get("type"),
            longitude=_coordinate(coordinates, 0),
            latitude=_coordinate(coordinates, 1),
        )


class SchoolMetroStation(Base):
    __tablename__ = "school_metro_stations"

    school_id: Mapped[int] = mapped_column(
        ForeignKey("motorcycle_schools.id"), primary_key=True
    )
    station_id: Mapped[int] = mapped_column(ForeignKey("metro_stations.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    distance: Mapped[str | None] = mapped_column(String(64))
    distance_value: Mapped[float | None] = mapped_column(Float)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    school: Mapped[MotorcycleSchool] = relationship(back_populates="metro_stations")
    station: Mapped[MetroStation] = relationship(back_populates="schools")

    __table_args__ = (
        UniqueConstraint(
            "school_id", "position", name="uq_school_metro_stations_position"
        ),
        Index("ix_school_metro_stations_distance_value", "distance_value"),
    )

    @classmethod
    def from_yandex_metro(
        cls, position: int, metro: dict[str, Any], station: MetroStation
    ) -> "SchoolMetroStation":
        return cls(
            station=station,
            position=position,
            distance=metro.get("distance"),
            distance_value=metro.get("distanceValue"),
            source_payload=metro,
        )


class SocialNetworkType(Base):
    __tablename__ = "social_network_types"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    links: Mapped[list[SchoolSocialLink]] = relationship(back_populates="type")

    @classmethod
    def from_code(cls, code: str) -> "SocialNetworkType":
        return cls(code=code, name=code)


class SchoolSocialLink(Base):
    __tablename__ = "school_social_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("motorcycle_schools.id"))
    type_code: Mapped[str] = mapped_column(ForeignKey("social_network_types.code"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    href: Mapped[str] = mapped_column(Text, nullable=False)
    readable_href: Mapped[str | None] = mapped_column(Text)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    school: Mapped[MotorcycleSchool] = relationship(back_populates="social_links")
    type: Mapped[SocialNetworkType] = relationship(back_populates="links")

    __table_args__ = (
        UniqueConstraint(
            "school_id", "position", name="uq_school_social_links_position"
        ),
        Index("ix_school_social_links_type_code", "type_code"),
    )

    @classmethod
    def from_yandex_social_link(
        cls, position: int, link: dict[str, Any]
    ) -> "SchoolSocialLink":
        return cls(
            type_code=link["type"],
            position=position,
            href=link["href"],
            readable_href=link.get("readableHref"),
            source_payload=link,
        )


class SchoolPhone(Base):
    __tablename__ = "school_phones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("motorcycle_schools.id"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str | None] = mapped_column(String(64))
    info: Mapped[str | None] = mapped_column(Text)
    extra_number: Mapped[str | None] = mapped_column(String(32))
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    school: Mapped[MotorcycleSchool] = relationship(back_populates="phones")

    __table_args__ = (
        UniqueConstraint("school_id", "position", name="uq_school_phones_position"),
        Index("ix_school_phones_value", "value"),
        Index("ix_school_phones_number", "number"),
    )

    @classmethod
    def from_yandex_phone(
        cls, position: int, phone: dict[str, Any]
    ) -> "SchoolPhone":
        return cls(
            position=position,
            number=phone.get("number"),
            value=phone.get("value"),
            type=phone.get("type"),
            info=phone.get("info"),
            extra_number=phone.get("extraNumber"),
            source_payload=phone,
        )


class SchoolUrl(Base):
    __tablename__ = "school_urls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("motorcycle_schools.id"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    school: Mapped[MotorcycleSchool] = relationship(back_populates="urls")

    __table_args__ = (
        UniqueConstraint("school_id", "position", name="uq_school_urls_position"),
        Index("ix_school_urls_url", "url"),
    )


class SchoolReview(Base):
    __tablename__ = "school_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("motorcycle_schools.id"))
    review_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    business_id: Mapped[str] = mapped_column(String(32), nullable=False)

    author_name: Mapped[str | None] = mapped_column(Text)
    author_avatar_url: Mapped[str | None] = mapped_column(Text)
    author_public_id: Mapped[str | None] = mapped_column(String(128))

    review_url: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(Integer)
    updated_time: Mapped[datetime | None] = mapped_column(DateTime)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dislikes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    school: Mapped[MotorcycleSchool] = relationship(back_populates="reviews")

    __table_args__ = (
        Index("ix_school_reviews_school_id", "school_id"),
        Index("ix_school_reviews_business_id", "business_id"),
        Index("ix_school_reviews_rating", "rating"),
        Index("ix_school_reviews_updated_time", "updated_time"),
        Index("ix_school_reviews_author_public_id", "author_public_id"),
    )

    @classmethod
    def from_yandex_review(
        cls,
        school: MotorcycleSchool,
        review: dict[str, Any],
    ) -> "SchoolReview":
        author = review.get("author") or {}
        reactions = review.get("reactions") or {}
        return cls(
            school=school,
            review_id=review["reviewId"],
            business_id=str(review["businessId"]),
            author_name=author.get("name"),
            author_avatar_url=author.get("avatarUrl"),
            author_public_id=author.get("publicId"),
            review_url=build_yandex_review_url(
                str(review["businessId"]), author.get("publicId")
            ),
            text=review.get("text"),
            rating=review.get("rating"),
            updated_time=parse_yandex_datetime(review.get("updatedTime")),
            likes=reactions.get("likes") or 0,
            dislikes=reactions.get("dislikes") or 0,
            source_payload=review,
        )

    def update_from_yandex_review(self, review: dict[str, Any]) -> None:
        author = review.get("author") or {}
        reactions = review.get("reactions") or {}
        self.business_id = str(review["businessId"])
        self.author_name = author.get("name")
        self.author_avatar_url = author.get("avatarUrl")
        self.author_public_id = author.get("publicId")
        self.review_url = build_yandex_review_url(
            str(review["businessId"]), author.get("publicId")
        )
        self.text = review.get("text")
        self.rating = review.get("rating")
        self.updated_time = parse_yandex_datetime(review.get("updatedTime"))
        self.likes = reactions.get("likes") or 0
        self.dislikes = reactions.get("dislikes") or 0
        self.source_payload = review


def parse_yandex_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)


def build_yandex_review_url(
    business_id: str | None,
    author_public_id: str | None,
) -> str | None:
    if not business_id or not author_public_id:
        return None
    return (
        f"https://yandex.ru/maps/org/{business_id}/reviews"
        f"?reviews%5BpublicId%5D={author_public_id}&utm_source=review"
    )


def _coordinate(coordinates: list[Any], index: int) -> float | None:
    if len(coordinates) <= index:
        return None
    return coordinates[index]


def build_avatar_url(business_images: dict[str, Any] | None) -> str | None:
    logo = (business_images or {}).get("logo") or {}
    url_template = logo.get("urlTemplate")
    if not url_template:
        return None
    return url_template.replace("%s", "S_height")
