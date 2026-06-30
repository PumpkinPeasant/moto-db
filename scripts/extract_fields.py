import csv
import json
import re
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
RESPONSES_DIR = BASE_DIR / "responses"
OUT_DIR = BASE_DIR / "extracted"
OUT_DIR.mkdir(exist_ok=True)

FIELDS = [
    "title",
    "address",
    "coordinates",
    "id",
    "metro",
    "additionalAddress",
    "seoname",
    "ratingData",
    "categories",
    "phones",
    "socialLinks",
    "urls",
    "businessImages",
]


def response_sort_key(path: Path) -> int:
    match = re.search(r"response_skip_(\d+)\.json$", path.name)
    return int(match.group(1)) if match else -1


def csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def main() -> None:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for response_file in sorted(RESPONSES_DIR.glob("response_skip_*.json"), key=response_sort_key):
        payload = json.loads(response_file.read_text(encoding="utf-8"))
        items = payload.get("data", {}).get("items", [])

        for item in items:
            yandex_id = str(item.get("id", ""))
            if not yandex_id or yandex_id in seen_ids:
                continue

            seen_ids.add(yandex_id)
            rows.append({field: item.get(field) for field in FIELDS})

    json_path = OUT_DIR / "motorcycle_schools.json"
    csv_path = OUT_DIR / "motorcycle_schools.csv"

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field)) for field in FIELDS})

    print(f"Готово: {len(rows)} уникальных организаций")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
