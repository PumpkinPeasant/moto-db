"""
Обходит все мотошколы из БД и сохраняет отзывы каждой в отдельный JSON.
Пропускает школы, для которых файл уже существует (возобновляемый запуск).
Прогресс пишется и на экран, и в parser/output/fetch_all.log.

Запуск:
    python -m parser.fetch_all_reviews
    python -m parser.fetch_all_reviews --delay 8 --refetch
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import MotorcycleSchool
from parser.fetch_reviews import build_reviews_url, fetch_all_reviews

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

DB_PATH = BASE_DIR.parent / "moto.sqlite"
LOG_FILE = OUTPUT_DIR / "fetch_all.log"


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # в файл
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # на экран
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # selenium и urllib3 слишком болтливые
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_all_schools() -> list[MotorcycleSchool]:
    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        schools = session.scalars(
            select(MotorcycleSchool).order_by(MotorcycleSchool.id)
        ).all()
        session.expunge_all()
        return list(schools)


def output_path(school: MotorcycleSchool) -> Path:
    return OUTPUT_DIR / f"{school.yandex_id}_reviews.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Пауза между школами в секундах (default: 5)")
    parser.add_argument("--refetch", action="store_true",
                        help="Перезагружать даже если файл уже есть")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    setup_logging()
    log = logging.getLogger(__name__)

    schools = get_all_schools()
    todo = [s for s in schools if args.refetch or not output_path(s).exists()]
    skipped = len(schools) - len(todo)

    log.info("=" * 60)
    log.info(f"Старт. Школ всего: {len(schools)} | к обработке: {len(todo)} | пропущено: {skipped}")
    log.info(f"Пауза между школами: {args.delay}s | Лог: {LOG_FILE}")
    log.info("=" * 60)

    ok = 0
    errors: list[str] = []
    start_total = time.time()

    for i, school in enumerate(todo, 1):
        elapsed_total = time.time() - start_total
        avg = elapsed_total / i if i > 1 else 0
        eta_min = avg * (len(todo) - i + 1) / 60 if avg else 0

        log.info(
            f"[{i}/{len(todo)}] {school.title} "
            f"(id={school.yandex_id}, ~{school.review_count or '?'} отзывов) "
            f"| ETA ~{eta_min:.0f} мин"
        )

        t0 = time.time()
        try:
            url = build_reviews_url(school)
            reviews, _ = fetch_all_reviews(url)

            out = output_path(school)
            out.write_text(
                json.dumps(reviews, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            elapsed = time.time() - t0
            log.info(
                f"[{i}/{len(todo)}] OK — {len(reviews)} отзывов за {elapsed:.0f}с "
                f"→ {out.name}"
            )
            ok += 1

        except Exception as e:
            elapsed = time.time() - t0
            log.error(f"[{i}/{len(todo)}] ОШИБКА за {elapsed:.0f}с: {e}")
            errors.append(f"{school.title} ({school.yandex_id}): {e}")

        if i < len(todo):
            log.info(f"Пауза {args.delay}с перед следующей школой...")
            time.sleep(args.delay)

    total_min = (time.time() - start_total) / 60
    log.info("=" * 60)
    log.info(f"Готово за {total_min:.1f} мин | OK: {ok} | Ошибок: {len(errors)} | Пропущено: {skipped}")
    if errors:
        log.info("Школы с ошибками:")
        for e in errors:
            log.info(f"  - {e}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
