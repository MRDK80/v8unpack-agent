# Локальная модель эмбеддингов (#407)

Опциональный адаптер `v8unpack_agent.search_local_model` на
`sentence-transformers`. Production default не меняется: без явного
`--embedder sentence-transformers` используется прежнее поведение.

## Установка

```bash
pip install "v8unpack-agent[local-model]"   # sentence-transformers и numpy
```

PyTorch с поддержкой GPU устанавливается отдельно по инструкции PyTorch для
вашей платформы. Веса модели адаптер **не скачивает**: каталог модели
(например, снимок `BAAI/bge-m3` из локального кэша Hugging Face) готовится
заранее и передаётся явно через `--model`.

## Без сети и без шума

До импорта библиотеки выставляются `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`; модель открывается с
`local_files_only=True`. Индикаторы загрузки и предупреждения библиотек
подавлены (`HF_HUB_DISABLE_PROGRESS_BARS`, `TRANSFORMERS_VERBOSITY`), чтобы
stderr CLI оставался однострочным. Нет каталога — код 2; нет extra — `error.code`
`embedder_unavailable` (код 4); сбой загрузки — `model_load_failed` (код 4).
Тексты ошибок не содержат путей.

## Идентичность снимка

`EmbedderIdentity`: имя `st:<--model-name>`, версия `--model-revision`
(или `unspecified`), размерность модели, `config_hash` — sha256 параметров
(`--max-tokens`, `--precision`, нормализация, версия адаптера) и файлов модели:
конфигурационные (`.json`, `.txt`, `.model`) — по содержимому, веса — по
размеру, скрытые каталоги не учитываются. Путь к модели в идентичность не
входит: тот же каталог в другом месте даёт тот же снимок.

## Векторизация

- `--device auto|cpu|cuda` (auto — cuda при наличии); устройство печатается
  с `--progress`.
- `--precision fp32|fp16` (default fp32) — fp16 только на `cuda`; входит в
  идентичность снимка, кэш у fp32 и fp16 раздельный.
- `--max-tokens` — предел длины фрагмента модели (`max_seq_length`), default 512.
- `--batch-size` — размер пакета, default 32. Тексты перед разбиением на
  пакеты сортируются по длине (меньше дополнения до самого длинного текста);
  на векторы и порядок результата это не влияет.
- `--progress` — строки прогресса в stderr: сколько посчитать, сколько из кэша,
  `done/total`, скорость и оценка оставшегося времени. Без флага stderr при
  успехе пуст, как прежде.
- `--cache-dir` — возобновляемый кэш: после каждого пакета векторы
  сохраняются частью `part_NNNNNN.npz` в подкаталоге идентичности; при
  повторном запуске посчитанные тексты (по sha256) не пересчитываются.
  Повреждённая часть пропускается и пересчитывается.

## Два уровня и слияние рангов

Решение владельца (2B): `build --object-snapshot PATH` дополнительно строит
снимок уровня объектов — первый фрагмент каждого артефакта
(`search_index.object_level`). `query --snapshot S --object-snapshot O`
сливает ранги артефактов двух уровней (`query_two_level`): каждый уровень
ранжирует до `max(5·top_k, 50)` артефактов, оценка — сумма
`1/(60 + ранг)` по уровням (RRF, k = 60). Это `similarity` попадания — шкала
слияния, не косинус. Попадание указывает лучший фрагмент уровня фрагментов,
а если артефакт найден только на уровне объектов — его фрагмент. В JSON
`query` появляется `object_snapshot: {loaded, error_code, compatibility,
freshness}`; несовместимый или устаревший снимок любого уровня даёт
`incompatible_index`.

## Пример

```bash
python -m v8unpack_agent.search_cli build EXPORT_ROOT \
  --snapshot S.json --object-snapshot O.json \
  --embedder sentence-transformers --model MODEL_DIR \
  --model-name BAAI/bge-m3 --model-revision REV --cache-dir CACHE --progress
python -m v8unpack_agent.search_cli query EXPORT_ROOT --query "расчет себестоимости" \
  --snapshot S.json --object-snapshot O.json \
  --embedder sentence-transformers --model MODEL_DIR \
  --model-name BAAI/bge-m3 --model-revision REV
```

Для локальной модели снимок по умолчанию пишется в `search_index/2`
(`--snapshot-format 1` — прежний формат).

## Ограничения

- Префиксы запроса/документа (instruction prefixes) не поддерживаются:
  один вызов `embed` и для фрагментов, и для запроса.
- Тесты пакета проверяют адаптер подменой библиотеки без весов; настоящая
  модель проверяется запуском владельца.
