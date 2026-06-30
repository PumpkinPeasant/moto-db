"""Шаг 1 пайплайна сборки БД.

Читает curls.txt (curl-команды, скопированные из DevTools для запросов
к Яндекс.Картам по мотошколам Москвы), последовательно выполняет их и
складывает сырые ответы в responses/response_skip_<N>.json. Параллельно
пишет summary.json с http-кодами и количеством элементов в каждом
ответе — удобно глянуть, что ничего не отвалилось.

Запуск:
    python scripts/get.py
"""
import json
import re
import shlex
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "curls.txt"
OUT_DIR = BASE_DIR / "responses"
OUT_DIR.mkdir(exist_ok=True)

text = Path(INPUT_FILE).read_text(encoding="utf-8")

commands = re.split(r"\n(?=curl )", text.strip())

summary = []

for i, cmd in enumerate(commands, start=1):
    skip_match = re.search(r"[?&]skip=(\d+)", cmd)
    skip = skip_match.group(1) if skip_match else "0"

    out_file = OUT_DIR / f"response_skip_{skip}.json"

    print(f"Запрос {i}/{len(commands)} | skip={skip}")

    args = shlex.split(cmd.replace("\\\n", " "))
    args.extend([
        "--silent",
        "--show-error",
        "--compressed",
        "--output",
        str(out_file),
        "--write-out",
        "%{http_code}",
    ])

    result = subprocess.run(
        args,
        text=True,
        capture_output=True
    )

    http_code = result.stdout.strip()

    item = {
        "index": i,
        "skip": skip,
        "http_code": http_code,
        "file": str(out_file),
        "error": result.stderr.strip() or None,
    }

    try:
        data = json.loads(out_file.read_text(encoding="utf-8"))
        item["totalResultCount"] = data.get("data", {}).get("totalResultCount")
        item["items_count"] = len(data.get("data", {}).get("items", []))
    except Exception:
        item["items_count"] = None

    summary.append(item)

(BASE_DIR / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print("Готово.")
print("Ответы лежат в папке responses/")
print("Сводка: summary.json")
