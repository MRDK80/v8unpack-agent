# PNG/JPEG: реальные маршруты от выгрузки к агенту (#518)

Состояние на roadmap после #517; это инвентаризация и transport-probe,
**не модельная приёмка**. [Протокол этапа](unicode_image_protocol_issue516.md)
и [machine-readable evidence](image_routes_issue518/report.json).
Новых ответов LLM не было. Тестовый HTTP-ответ — заглушка, не запуск модели.

## Вывод

Изображения во входе действительно есть, причём расширение ненадёжно.
Но стандартные package entrypoints не подключают их байты автоматически
к запросам модели. Нельзя ни исключить картинки из модели угроз, ни объявить
любое наличие PNG/JPEG доказанной доставкой пикселей в LLM.

| Граница | Что установлено | Основание/ограничение |
|---|---|---|
| Upstream CF→файл | CommonPicture извлекает base64 bytes и пишет отдельный файл | v8unpack1.2.13, CommonPicture.decode_object/_extract_b64_data; не OCR |
| Имя/расширение | get_extension_from_comment берёт короткий alphanumeric суффикс, иначе `bin` | Это выбор расширения, не MIME/pixel validation |
| package CLI/runner | Сканирует поддержанные формы/модули/СКД, не OCR/пиксели | tests вызывают настоящий cli.main legacy/framed, binary-read запрещён spy |
| form_context | BSL, summary, выбранные object attributes/relations и пути формы | build_form_context/_read_bsl/_build_summary/_build_object_attributes, не произвольный JSON/attachment |
| search corpus/context | Документы из прочитанного BSL, связи и owner cards | collect_search_corpus/read_module_text/_add_form; снимок корпуса тот же после добавления картинки |
| Текстовая ссылка в BSL | Имя картинки/metadata-текст могут попасть как обычный текст источника | Positive control с canary в BSL доходит до tool-content, не system |
| Старый request builder | Собирает text messages/schema; `images` сам не добавляет | final_selection_issue502.request, exact JSON перехвачен перед HTTP |
| Generic HTTP transport | Передаёт images, если доверенный host сам их добавил | TimedLocalOllama.chat не image-discovery и не capability verifier |
| Внешний metadata/OCR reader | Явно извлечённый текст может быть передан в недоверенный контекст | Package по умолчанию этот reader не запускает; интерфейс должен задать потребитель |
| Direct vision | Нужен явно выбранный vision model и `messages[*].images` | Text-only Qwen/Llama зрячими не становятся |
| LSB | Автоматического decoder в package/текстовом клиенте нет | Не доказательство стойкости VLM; явно not_exercised без decoder |

Это карта просмотренных entrypoints и наблюдаемых synthetic случаев, не
формальное доказательство отсутствия любого image-route во всех возможных
плагинах/внешних агентах. Пакет не запрещает пользователю выдать агенту
независимый file/image tool в обход этой цепочки. При этом полномочия на
запись и [validator](../action_validation.md) всё равно задаются вне данных.

## Readonly инвентаризация A/B/C

Каждый обычный файл проверен по первым16байтам, независимо от расширения.
Публично только агрегаты, без имён, пикселей и metadata-значений.

| Выгрузка | Файлов | PNG-signature | JPEG-signature | Остальные заголовки |
|---|---:|---:|---:|---:|
| A |23775|266|0|23509|
| B |720|0|0|720|
| C |50353|259|4|50090|

Всего74848файлов,525PNG-signatures и4JPEG-signatures. Все529имели иные
расширения, не .png/.jpg/.jpeg. Поэтому обход только по этим расширениям
пропустил бы их все. У найденных signature-файлов превышений2MiB нет;
суммарно PNG1681819байт,JPEG37778байт.

Подпись заголовка **не полная валидация картинки** и не доказательство
достижимости модели. Вложенные контейнеры/embedded images не декодировались.
I/O ошибок/unsafe paths/наблюдаемых stat changes0; инвентарь до/после тот же.
Это readonly чтение заголовков, не побайтовая проверка неизменности всех
полных файлов. Агрегаты сохранены в report.json.

## Синтетические probes и их воспроизведение

Зафиксированные [PNG/JPEG fixtures](../../tests/fixtures_issue518/manifest.json)
содержат раздельные `PIXEL_SENTINEL_518` и `METADATA_SENTINEL_518`.
Это маршрутизирующие canaries, не новые acceptance attacks.

```bash
python -m pytest -q tests/test_image_routes_issue518.py
```

12тестов без сети/модели/Pillow:

- Проверяют hashes, размеры, сигнатуры и metadata canary фиксированных байтов.
- Вызывают реальные form/search/CLI entrypoints legacy/framed. Spy запрещает
  открывать picture bytes. Corpus и CLI-report побайтно совпадают с baseline;
  ни metadata canary, ни base64 image не попадают автоматически в context.
- Перехватывают `urllib.request.Request.data` настоящего TimedLocalOllama
  перед fake HTTP boundary. Default builder не содержит images. Если host
  добавил images явно, байты доходят до transport без изменений.
- Положительный контроль: перенесённый в BSL/явно переданный текст доходит
  до недоверенного tool-content, а не становится system-инструкцией.

Fake metadata verify проверяет только путь кода транспорта, а не настоящую
capability модели. Из наличия base64 в HTTP-body не следует, что скрытая
metadata/LSB информация попала в vision encoder или была понята моделью.

Необязательный [генератор canaries](image_routes_issue518/make_canaries.py)
требует отдельного Pillow13/Python≥3.11; в CI и package не импортируется.
Команда принимает **новый** каталог, существующий не перезаписывает:

```bash
python docs/research/image_routes_issue518/make_canaries.py new-canaries
```

Авторитетны committed bytes/manifest. На другой JPEG backend регенерация
не обязана совпасть побайтно; это не повод переснять fixtures после ответов.

## Внешний потребитель для #520: явный opt-in, без default

1. Host выбирает разрешённый локальный файл/снимок вне model response,
   проверяет принадлежность корню/тип/размер/ссылки. Не расширять полномочия
   по имени, MIME/metadata или ответу модели. Extension не источник доверия.
2. Для synthetic phase проверить actual image bytes≤2MiB, pixels≤4MP,
   malformed/oversize/bomb входы отклонить до декодирования/передачи. Пределы
   header-inspection сами по себе не являются sandbox стороннего decoder.
3. Direct vision: host регистрирует отдельный pinned multimodal профиль,
   проверяет capabilities/digest/server и добавляет точные base64 bytes в
   `messages[*].images`. Base64 не превращает text-only модель в VLM.
4. OCR: отдельный локальный extractor со своими version/languages/hash,
   конечным timeout/output limit. Сохранить извлечённый текст/ошибки и actual
   следующий request. OCR-текст передавать как данные, не как system/developer.
5. Metadata: только явно выбранные bounded поля/форматы. Binary metadata
   внутри image request не считать уже доставленным текстом. Без reader —
   отсутствующий канал. В #520 перед ответами зафиксировать, какие readers
   действительно реализованы, route-mask и payload hashes.
6. LSB: decoder в этой цепочке не найден; без специально добавленного и
   зарегистрированного decoder статус not_exercised. Не приписывать VLM
   автоматическое извлечение битов. Alpha/JPEG и изменения preprocessing
   также отмечать как incompatible/lost channel, а не устойчивость.
7. Source/provenance/request/output и единственный writer принадлежат host.
   Изображение/OCR/metadata не заменяют истинные BSL/form факты. Все proposals
   проходят прежнюю проверку, включая downstream; нет raw writer/shell/network.

## Локальный preflight, без generation

В начальном окружении были только text-only Qwen2.5/Llama3.1/Qwen3, без
Tesseract; системный Pillow не включён в dev-venv. Для synthetic работы
подготовлены изолированные Pillow13.0.0 и Tesseract5.3.4 с eng/rus.
APT-пакеты распакованы в отдельный префикс, система/pyproject не менялись.
Версии binary/languages и hashes — в report.json; это не модельный ответ.

Заранее выбранный S1 vision-кандидат — Qwen2.5-VL3B, отдельный от Qwen2.5-7B
text-профиля. Загрузка завершена:3200627168байт, capabilities `completion,vision`,
digest `fb90415cde1ef08aa669ae74b082d49b158729b6db1ab183c941417d507e71a1`.
Метаданные остальных text-only профилей не содержат vision; все digests
и preflight сохранены в report.json. Не вызов /api/chat и не
probe качества перед регистрацией. Если preflight не проходит, S1 требует
явный статус и новую предрегистрацию до любого другого кандидата.

Один generation за раз,≤6GiB новых весов,≥10GiB свободного диска и остальные
[лимиты S1](unicode_image_protocol_issue516.md). Ни OCR, ни VLM не добавлены
в зависимости/default/CLI. В #519/#520 сначала exact code/input/runtime seal
и публичная регистрация, затем новые ответы. Это исследование внешнего
потребителя, а не объявление готового product image API.

См. [итоговые измерения OCR и файлового эффекта](unicode_image_acceptance_issue523.md).
