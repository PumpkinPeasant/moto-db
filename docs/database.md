# Moto DB: сборка базы и схема данных

Этот документ описывает, из каких источников и какими скриптами собирается
текущая SQLite-база `moto.sqlite`, а также фиксирует прикладную схему данных.

## Итоговая база

Основной файл базы: `moto.sqlite`.

База содержит:

- мотошколы и автошколы из Яндекс.Карт;
- категории организаций;
- станции метро, линии, районы и округа;
- связи школ с метро и дистанциями до станций;
- телефоны, сайты и социальные ссылки;
- отзывы Яндекс.Карт;
- локальный каталог услуг;
- локальный справочник мототехники `fleet`;
- локальные уникальные `slug` для страниц школ.

В репозиторий база может не попадать из-за ограничения по весу. Если нужен
полный локальный файл, его надо восстановить пайплайном ниже.

## Источники данных

### Организации

Исходные данные по организациям берутся из Яндекс.Карт.

1. В `scripts/curls.txt` лежат `curl`-команды, скопированные из DevTools.
2. `scripts/get.py` выполняет эти запросы и сохраняет сырые ответы:
   `scripts/responses/response_skip_<N>.json`.
3. `scripts/extract_fields.py` извлекает нужные поля и пишет:
   `scripts/extracted/motorcycle_schools.json`
   и `scripts/extracted/motorcycle_schools.csv`.

### Метро

Первичные данные метро приходят из Яндекс.Карт внутри поля `metro` у школы.

Дополнительное обогащение метро делается по датасету data.mos.ru `1488`
«Станции Московского метрополитена». Скрипт:

```bash
python -m scripts.enrich_metro_from_mos --input path/to/data-1488.json
```

Он проставляет:

- линию метро;
- номер и цвет линии;
- район;
- административный округ;
- статус активности станции.

### Отзывы

Отзывы собираются из Яндекс.Карт через Selenium/CDP.

Основные скрипты:

- `parser/fetch_reviews.py` — собирает отзывы одной школы;
- `parser/fetch_all_reviews.py` — обходит все школы из БД и сохраняет JSON по каждой.

Результат лежит в:

```text
parser/output/<yandex_id>_reviews.json
```

Формат одного отзыва:

```json
{
  "reviewId": "ZV_Eag_Eg2HmKKZpEKWa-UAznXtfua",
  "businessId": "1006588388",
  "author": {
    "name": "Ок",
    "avatarUrl": "https://avatars.mds.yandex.net/get-yapic/47747/0l-7/{size}",
    "publicId": "64y5002x2445nzqnqxqq4bnzkg"
  },
  "text": "Текст отзыва",
  "rating": 1,
  "updatedTime": "2026-06-30T13:09:36.908Z",
  "reactions": {
    "likes": 1,
    "dislikes": 0
  }
}
```

## Полный пайплайн сборки

### 1. Получить сырые ответы Яндекс.Карт

```bash
python scripts/get.py
```

Выход:

```text
scripts/responses/response_skip_*.json
scripts/summary.json
```

### 2. Извлечь поля организаций

```bash
python scripts/extract_fields.py
```

Выход:

```text
scripts/extracted/motorcycle_schools.json
scripts/extracted/motorcycle_schools.csv
```

### 3. Создать SQLite и импортировать организации

```bash
python -m scripts.import_extracted_to_sqlite --reset
```

Без `--reset` скрипт идемпотентно пропускает уже существующие школы по
`yandex_id`.

### 4. Нормализовать и обогатить метро

Если база создана старым способом с плоскими полями метро, сначала можно
выполнить одноразовую миграцию:

```bash
python -m scripts.migrate_normalize_metro
```

Для актуального пайплайна основной шаг:

```bash
python -m scripts.enrich_metro_from_mos --input path/to/data-1488.json
```

### 5. Собрать отзывы

Для одной школы:

```bash
python -m parser.fetch_reviews --school-id 5
```

Для всех школ:

```bash
python -m parser.fetch_all_reviews
```

Повторный запуск пропускает уже существующие файлы. Чтобы перекачать заново:

```bash
python -m parser.fetch_all_reviews --refetch
```

### 6. Импортировать отзывы в SQLite

```bash
python -m scripts.import_reviews_to_sqlite
```

Скрипт:

- создает таблицу `school_reviews`, если ее нет;
- добавляет недостающую колонку `review_url`, если база уже существовала;
- связывает отзывы со школами по `businessId == motorcycle_schools.yandex_id`;
- вставляет новые отзывы;
- обновляет существующие по `reviewId`.

Для полной пересборки таблицы отзывов:

```bash
python -m scripts.import_reviews_to_sqlite --clear
```

### 7. Сгенерировать уникальные slug школ

```bash
python -m scripts.backfill_school_slugs
```

Яндекс `seoname` не уникален: один и тот же slug может быть у нескольких
филиалов. Поэтому локальный `slug` строится так:

- если `seoname` уникальный: `slug = seoname`;
- если `seoname` повторяется: `slug = {seoname}-{yandex_id}`.

Примеры:

```text
yaguar-137596292455
yaguar-150942837607
alyans-1293185190
```

### 8. Создать локальные справочники

Услуги хранятся в таблице `services` и пока задаются в коде:

```bash
python -m scripts.seed_services
```

Справочник мототехники хранится в таблице `fleet`. Сейчас исходный список
мотиков не задан, поэтому `scripts.seed_fleet` создает таблицу и готов к
идемпотентной загрузке данных после заполнения `FLEET`:

```bash
python -m scripts.seed_fleet
```

## Основные таблицы

### motorcycle_schools

Главная таблица школ и организаций.

Ключевые поля:

- `id` — внутренний integer primary key;
- `yandex_id` — id организации в Яндекс.Картах;
- `title` — название;
- `address`, `additional_address`;
- `seoname` — slug из Яндекса, не уникальный;
- `slug` — локальный уникальный slug;
- `avatar_url`;
- `longitude`, `latitude`;
- `rating_count`, `rating_value`, `review_count`;
- `source_payload` — исходный payload организации;
- `created_at`, `updated_at`.

Индексы:

- `ix_motorcycle_schools_title`;
- `ix_motorcycle_schools_geo`;
- `ix_motorcycle_schools_rating_value`;
- `ix_motorcycle_schools_seoname`;
- `ix_motorcycle_schools_slug`.

### categories

Справочник категорий Яндекс.Карт.

Поля:

- `id`;
- `yandex_id`;
- `name`;
- `class_name`;
- `seoname`;
- `plural_name`.

### school_categories

Many-to-many связь школ и категорий.

Поля:

- `school_id -> motorcycle_schools.id`;
- `category_id -> categories.id`.

### services

Локальный справочник услуг.

Поля:

- `id`;
- `code` — стабильный код услуги;
- `name`;
- `position`;
- `is_active`.

Индексы:

- `ix_services_name`;
- `ix_services_position`.

### fleet

Локальный справочник мототехники.

Поля:

- `id`;
- `code` — стабильный код мотоцикла;
- `brand`;
- `model`;
- `display_name`;
- `category` — категория прав или внутренний тип, если понадобится;
- `engine_cc`;
- `position`;
- `is_active`.

Индексы:

- `ix_fleet_brand`;
- `ix_fleet_model`;
- `ix_fleet_display_name`;
- `ix_fleet_position`.

### metro_lines

Справочник линий метро.

Поля:

- `id`;
- `name`;
- `number`;
- `color`.

### administrative_areas

Справочник административных округов.

Поля:

- `id`;
- `name`.

### districts

Справочник районов.

Поля:

- `id`;
- `name`;
- `adm_area_id -> administrative_areas.id`.

### metro_stations

Станции метро.

Поля:

- `id`;
- `yandex_id`;
- `name`;
- `type`;
- `longitude`, `latitude`;
- `line_id -> metro_lines.id`;
- `district_id -> districts.id`;
- `is_active`.

Индексы:

- `ix_metro_stations_name`;
- `ix_metro_stations_geo`;
- `ix_metro_stations_line_id`;
- `ix_metro_stations_district_id`.

### school_metro_stations

Связь школы со станциями метро.

Поля:

- `school_id -> motorcycle_schools.id`;
- `station_id -> metro_stations.id`;
- `position` — исходная позиция из Яндекса;
- `distance` — текстовая дистанция;
- `distance_value` — числовая дистанция;
- `source_payload`.

Индексы:

- `ix_school_metro_stations_distance_value`.

### social_network_types

Справочник типов социальных сетей.

Поля:

- `code`;
- `name`.

### school_social_links

Социальные ссылки школы.

Поля:

- `id`;
- `school_id -> motorcycle_schools.id`;
- `type_code -> social_network_types.code`;
- `position`;
- `href`;
- `readable_href`;
- `source_payload`.

### school_phones

Телефоны школы.

Поля:

- `id`;
- `school_id -> motorcycle_schools.id`;
- `position`;
- `number`;
- `value`;
- `type`;
- `info`;
- `extra_number`;
- `source_payload`.

### school_urls

Сайты школы.

Поля:

- `id`;
- `school_id -> motorcycle_schools.id`;
- `position`;
- `url`.

### school_reviews

Отзывы Яндекс.Карт.

Поля:

- `id`;
- `school_id -> motorcycle_schools.id`;
- `review_id` — id отзыва в Яндексе, уникальный;
- `business_id` — id организации в Яндексе;
- `author_name`;
- `author_avatar_url`;
- `author_public_id`;
- `review_url` — ссылка на отзыв в Яндекс.Картах;
- `text`;
- `rating`;
- `updated_time`;
- `likes`;
- `dislikes`;
- `source_payload` — полный исходный JSON отзыва.

Формат `review_url`:

```text
https://yandex.ru/maps/org/{business_id}/reviews?reviews%5BpublicId%5D={author_public_id}&utm_source=review
```

Если у отзыва нет `author.publicId`, `review_url` остается `NULL`.

Индексы:

- `ix_school_reviews_school_id`;
- `ix_school_reviews_business_id`;
- `ix_school_reviews_rating`;
- `ix_school_reviews_updated_time`;
- `ix_school_reviews_author_public_id`.

## API, завязанные на схему

### Школы

- `GET /schools` — список школ, фильтры, сортировка, пагинация;
- `GET /schools/{school_id}` — школа по внутреннему id;
- `GET /schools/by-slug/{slug}` — школа по локальному уникальному slug;
- `GET /schools/by-seoname/{seoname}` — fallback по неуникальному Яндекс slug.

Для страниц на фронте предпочтительно использовать `slug`, а для связанных
данных и аналитики — внутренний `id`.

### Аналитика отзывов

- `GET /analytics/reviews/by-date`;
- `GET /analytics/reviews/sentiment`.

Общие фильтры:

- `school_id`;
- `date_from`;
- `date_to`;
- `only_moto`.

Правило тональности:

- `rating >= 4` — positive;
- `rating < 4` — negative.

### Локальные справочники

- `GET /services`;
- `GET /autocomplete/services`;
- `GET /fleet`;
- `GET /autocomplete/fleet`.

## Локальные изменения базы

Если `moto.sqlite` не коммитится из-за размера, кодовые миграции/бэкфиллы
остаются в репозитории, а сам файл базы восстанавливается запуском:

```bash
python -m scripts.import_extracted_to_sqlite --reset
python -m scripts.enrich_metro_from_mos --input path/to/data-1488.json
python -m scripts.import_reviews_to_sqlite
python -m scripts.backfill_school_slugs
python -m scripts.seed_services
python -m scripts.seed_fleet
```

Если `parser/output` уже содержит JSON-отзывы, повторно скачивать отзывы не
нужно.
