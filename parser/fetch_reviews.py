"""
Парсит отзывы с Яндекс.Карт для первой школы в БД (или по --school-id).
Перехватывает fetchReviews через Chrome CDP (Network.getResponseBody),
сохраняет в parser/output/<yandex_id>_reviews.json.

Запуск:
    python -m parser.fetch_reviews
    python -m parser.fetch_reviews --school-id 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import MotorcycleSchool

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

DB_PATH = BASE_DIR.parent / "moto.sqlite"
YANDEX_MAPS_ORG_URL = "https://yandex.ru/maps/org"

PAGE_SIZE = 50
SCROLL_PAUSE = 2.5
MAX_IDLE_SCROLLS = 10


def build_reviews_url(school: MotorcycleSchool) -> str:
    return f"{YANDEX_MAPS_ORG_URL}/{school.seoname}/{school.yandex_id}/reviews/"


def get_school(school_id: int | None) -> MotorcycleSchool:
    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        if school_id is not None:
            school = session.scalar(
                select(MotorcycleSchool).where(MotorcycleSchool.id == school_id)
            )
        else:
            school = session.scalar(select(MotorcycleSchool).order_by(MotorcycleSchool.id))
        if school is None:
            raise SystemExit("Shkola ne naidena")
        session.expunge(school)
        return school


def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=ru")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # включаем performance log для перехвата сетевых событий
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=options)


def get_perf_log_entries(driver: webdriver.Chrome) -> list[dict]:
    entries = []
    for entry in driver.get_log("performance"):
        try:
            msg = json.loads(entry["message"])["message"]
            entries.append(msg)
        except Exception:
            pass
    return entries


def extract_reviews_request_ids(entries: list[dict]) -> dict[str, str]:
    """requestId -> url для всех fetchReviews ответов."""
    result = {}
    for msg in entries:
        if msg.get("method") != "Network.responseReceived":
            continue
        params = msg.get("params", {})
        url = params.get("response", {}).get("url", "")
        if "fetchReviews" in url:
            result[params["requestId"]] = url
    return result


def get_response_body(driver: webdriver.Chrome, request_id: str) -> bytes | None:
    try:
        resp = driver.execute_cdp_cmd(
            "Network.getResponseBody", {"requestId": request_id}
        )
        body = resp.get("body", "")
        if resp.get("base64Encoded"):
            import base64
            return base64.b64decode(body)
        return body.encode("utf-8")
    except Exception:
        return None


def scroll_reviews_panel(driver: webdriver.Chrome) -> None:
    scrolled = driver.execute_script("""
        var containers = document.querySelectorAll('.scroll__container');
        var best = null, bestH = 0;
        for (var c of containers) {
            if (c.scrollHeight > bestH) { best = c; bestH = c.scrollHeight; }
        }
        if (best) {
            best.scrollTop = best.scrollHeight;
            return best.scrollHeight;
        }
        window.scrollBy(0, 3000);
        return -1;
    """)
    _ = scrolled  # можно логировать если нужно


def _trigger_sort(driver: webdriver.Chrome) -> None:
    """Кликает по дропдауну сортировки, чтобы спровоцировать fetchReviews."""
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".rating-ranking-view"))
        )
        current_text = btn.text.strip()
        btn.click()
        print(f"  Sort dropdown opened (was: {current_text!r})")
        time.sleep(1)

        # Ждём опции и кликаем на первую, которая не текущая
        options = WebDriverWait(driver, 5).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, ".rating-ranking-view__popup-line")
            )
        )
        for opt in options:
            if opt.text.strip() and opt.text.strip() != current_text:
                print(f"  Selecting sort option: {opt.text.strip()!r}")
                opt.click()
                time.sleep(0.5)
                return
        # Если вдруг все совпали — кликаем первую
        if options:
            options[0].click()
    except Exception as ex:
        print(f"  Sort trigger error: {ex}")


def fetch_all_reviews(url: str) -> tuple[list[dict], int]:
    driver = make_driver()
    try:
        # скрываем webdriver до загрузки страницы
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
        )
        driver.execute_cdp_cmd("Network.enable", {})

        print(f"Otkryvayu: {url}")
        driver.get(url)

        time.sleep(4)
        print(f"  URL: {driver.current_url}")

        # Дёргаем комбобокс сортировки — без этого первая страница
        # отдаётся через SSR и fetchReviews не вызывается
        _trigger_sort(driver)

        collected: dict[int, list[dict]] = {}
        total_reviews = 0
        idle_scrolls = 0
        seen_request_ids: set[str] = set()

        # ждём первого ответа fetchReviews
        deadline = time.time() + 60
        print("Zhdu pervuyu stranitsu...")
        while time.time() < deadline:
            entries = get_perf_log_entries(driver)
            new_ids = extract_reviews_request_ids(entries)
            if new_ids:
                print(f"  Naideno {len(new_ids)} fetchReviews zaprosov")
                for rid, rurl in new_ids.items():
                    if rid in seen_request_ids:
                        continue
                    seen_request_ids.add(rid)
                    body = get_response_body(driver, rid)
                    if not body:
                        continue
                    try:
                        data = json.loads(body)
                    except Exception:
                        continue
                    page_num = _page_from_url(rurl)
                    reviews = data.get("data", {}).get("reviews", [])
                    if page_num not in collected:
                        collected[page_num] = reviews
                        inner = data.get("data", {})
                        params = inner.get("params") or {}
                        pager = inner.get("pager") or inner.get("pageInfo") or {}
                        total_reviews = (
                            params.get("totalReviews")
                            or params.get("total")
                            or pager.get("total")
                            or inner.get("total")
                            or total_reviews
                        )
                        print(f"  Stranitsa {page_num}: {len(reviews)} otzyvov (vsego: {total_reviews})")
                break
            time.sleep(0.5)
        else:
            print("Timeout: reviews ne zagruzilis")
            return [], 0

        last_page_size = PAGE_SIZE  # если страница вернула < PAGE_SIZE — конец

        # скроллим и собираем оставшиеся страницы
        while True:
            # Останавливаемся если последняя страница была неполной
            if last_page_size < PAGE_SIZE:
                loaded = sum(len(v) for v in collected.values())
                print(f"Zagruzheno vse: {loaded} (posled. str. {last_page_size} < {PAGE_SIZE})")
                break

            scroll_reviews_panel(driver)
            time.sleep(SCROLL_PAUSE)

            entries = get_perf_log_entries(driver)
            new_ids = extract_reviews_request_ids(entries)
            new_found = False

            for rid, rurl in new_ids.items():
                if rid in seen_request_ids:
                    continue
                seen_request_ids.add(rid)
                body = get_response_body(driver, rid)
                if not body:
                    continue
                try:
                    data = json.loads(body)
                except Exception:
                    continue
                page_num = _page_from_url(rurl)
                reviews = data.get("data", {}).get("reviews", [])
                if page_num not in collected:
                    collected[page_num] = reviews
                    last_page_size = len(reviews)
                    print(f"  Stranitsa {page_num}: {len(reviews)} otzyvov")
                    new_found = True

            if not new_found:
                idle_scrolls += 1
                if idle_scrolls >= MAX_IDLE_SCROLLS:
                    loaded = sum(len(v) for v in collected.values())
                    print(f"Zagruzheno {loaded}, bolshe ne podgruzhayetsya")
                    break
            else:
                idle_scrolls = 0

        all_reviews = [
            review
            for _, reviews in sorted(collected.items())
            for review in reviews
        ]
        return all_reviews, total_reviews

    finally:
        driver.quit()


def _page_from_url(url: str) -> int:
    for part in url.split("&"):
        if part.startswith("page=") or "?page=" in part:
            try:
                return int(part.split("=")[1])
            except ValueError:
                pass
    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--school-id", type=int, default=None)
    args = parser.parse_args()

    school = get_school(args.school_id)
    print(f"Shkola: {school.title} (yandex_id={school.yandex_id})")

    if not school.seoname:
        raise SystemExit("U shkoly net seoname")

    url = build_reviews_url(school)
    reviews, total = fetch_all_reviews(url)

    out_path = OUTPUT_DIR / f"{school.yandex_id}_reviews.json"
    out_path.write_text(
        json.dumps(reviews, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Done: {len(reviews)} reviews saved to {out_path}")


if __name__ == "__main__":
    main()
