"""Import parsed Yandex Maps reviews into moto.sqlite.

By default reads JSON files produced by parser/fetch_all_reviews.py:

    python -m scripts.import_reviews_to_sqlite
    python -m scripts.import_reviews_to_sqlite --input parser/output --clear
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.models import Base, MotorcycleSchool, SchoolReview  # noqa: E402


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "moto.sqlite"
DEFAULT_INPUT_DIR = BASE_DIR / "parser" / "output"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory with *_reviews.json files.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="SQLite database path.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing reviews before import.",
    )
    args = parser.parse_args()

    engine = create_engine(f"sqlite:///{args.db}")
    ensure_review_columns(engine)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        if args.clear:
            deleted = session.query(SchoolReview).delete()
            session.commit()
            print(f"Deleted existing reviews: {deleted}")

        stats = import_reviews(session, args.input)
        session.commit()

    print(
        "Imported reviews: "
        f"files={stats['files']}, seen={stats['seen']}, "
        f"created={stats['created']}, updated={stats['updated']}, "
        f"skipped={stats['skipped']}"
    )


def ensure_review_columns(engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table("school_reviews"):
        return

    existing = {column["name"] for column in inspector.get_columns("school_reviews")}
    with engine.begin() as conn:
        if "review_url" not in existing:
            conn.execute(text("ALTER TABLE school_reviews ADD COLUMN review_url TEXT"))


def import_reviews(session: Session, input_dir: Path) -> dict[str, int]:
    schools_by_yandex_id = {
        school.yandex_id: school
        for school in session.scalars(select(MotorcycleSchool)).all()
    }
    reviews_by_review_id = {
        review.review_id: review
        for review in session.scalars(select(SchoolReview)).all()
    }

    stats = {
        "files": 0,
        "seen": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
    }

    for path in sorted(input_dir.glob("*_reviews.json")):
        stats["files"] += 1
        reviews = load_reviews(path)
        for review_payload in reviews:
            stats["seen"] += 1
            business_id = str(review_payload.get("businessId") or "")
            review_id = review_payload.get("reviewId")
            school = schools_by_yandex_id.get(business_id)
            if not review_id or school is None:
                stats["skipped"] += 1
                continue

            existing_review = reviews_by_review_id.get(review_id)
            if existing_review is None:
                review = SchoolReview.from_yandex_review(school, review_payload)
                session.add(review)
                reviews_by_review_id[review_id] = review
                stats["created"] += 1
                continue

            existing_review.school = school
            existing_review.update_from_yandex_review(review_payload)
            stats["updated"] += 1

    return stats


def load_reviews(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected list in {path}")
    return data


if __name__ == "__main__":
    main()
