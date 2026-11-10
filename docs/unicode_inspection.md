# Opt-in Unicode inspection (#517)

Контракт `unicode_inspection/1` из подмодуля
`v8unpack_agent.unicode_inspection`. Это отдельная диагностика, не изменение
прежнего [Cf/BOM и framed](untrusted_content_contract.md). Root exports,
CLI/default/индексы и сериализация `UntrustedDiagnostics` не расширяются.
Решение и контракт фиксируются до реализации; это не detector злого умысла.

## Почему отдельный API

Прежняя функция `detect_untrusted_chars` ищет Unicode category `Cf`.
Tags относятся к Cf и уже отражаются как `other_cf`. Variation selectors
имеют category `Mn`: их отсутствие в Cf total корректно и не должно
исправляться переименованием Mn в Cf. Старый framed экранирует Cf/Cc,
но не начинает автоматически экранировать Mn. При этом его декодирование
сохраняет точные значения. Новое API отдельно сообщает выбранные символы,
не переписывает транспорт и не удаляет содержимое исходника.

## Матрица покрытия

| Символы/диапазоны | Новый group | Старый Cf API |
|---|---|---|
| Assigned Tags:U+E0001,U+E0020–U+E007F | `tag` | `other_cf` |
| U+FE00–U+FE0F,U+E0100–U+E01EF | `variation_selector` | Не Cf |
| Mongolian FVS:U+180B–U+180D,U+180F | `variation_selector` | Не Cf |
| CGJ:U+034F | `combining_grapheme_joiner` | Не Cf |
| Bidi marks/embeddings/overrides/isolates из старой таблицы | `bidi` | `bidi` |
| U+200B,U+200C,U+200D,U+2060,U+FEFF внутри текста | `zero_width` | `zero_width` |
| U+00AD | `soft_hyphen` | `soft_hyphen` |
| Остальные runtime-Cf | `other_cf` | `other_cf` |
| Surrogate U+D800–U+DFFF в Python str | `invalid_scalar` | Не Cf |
| BOM из raw UTF-8 | Отдельный `file_bom` | Прежняя семантика |

Группы не пересекаются: Tags классифицируются до общего Cf. Диапазоны
выбранных Mn/Tags фиксированы по Unicode17.0; category/прочие Cf берутся
из `unicodedata` runtime, версия записывается в результат. Резервные gaps
в блоке Tags не объявляются назначенными Tag Characters.

Это **не полный Default_Ignorable и не все невидимые символы**. Hangul
fillers U+115F/U+1160 не включены; обычные combining marks, похожие буквы,
пробелы и Cc не расширяют эту выборку. Отсутствие occurrence не доказывает
безопасность. Законные emoji/CJK variants, арабский/монгольский текст и
форматирование также дают occurrences; автоматически блокировать их нельзя.
Основание: [Unicode17, глава23](https://www.unicode.org/versions/Unicode17.0.0/core-spec/chapter-23/)
и [протокол этапа](research/unicode_image_protocol_issue516.md).

## Вызов и ограниченная полнота

```python
from v8unpack_agent.unicode_inspection import inspect_unicode

result = inspect_unicode("Привет\u200b ❤️", max_chars=10000, max_occurrences=100)
payload = result.to_dict()
# codepoint/line/column — метаданные, без копирования исходного значения.
# status != complete: нельзя выдавать частичную проверку за полную.
```

Параметры: `text:str`, опционально `raw:bytes|bytearray|None` только для
признака UTF-8 BOM, `max_chars` от0 до1000000, `max_occurrences` от0 до1000.
Boolean/отрицательные/сверхлимитные значения отклоняются ValueError;
неверный тип text/raw — TypeError, без неявного декодирования. API не читает
файлы. Raw не является доказательством соответствия text и не даёт полномочий.

`UnicodeInspection` и `UnicodeOccurrence` — frozen dataclasses, occurrences
и внутренние счётчики immutable. `to_dict()` возвращает независимые копии.
Result содержит schema, status/reasons, file_bom, unidata_version, границы
сканирования и метаданные. Positions: строки разделены только LF; столбцы
с1 считаются codepoints, не bytes/UTF-16/display widths. CR — обычная позиция.
Если известный raw BOM соответствует начальному U+FEFF, он не считается
повторно occurrence; внутренний U+FEFF сохраняется. При raw=None file_bom
неизвестен, начальный U+FEFF считается обычным Cf.

Сканируется только ограниченный префикс, occurrences сохраняются до лимита;
после лимита retention сканирование префикса продолжается для счётчиков.
`counts` и `observed_count` относятся **только к scanned prefix**.
`scanned_chars`, `unscanned_chars`, `omitted_occurrences` показывают пропуски.
`counts_scope="scanned_prefix_only"` не превращается в оценку всего текста.

- `complete`: весь вход просмотрен, occurrences не отброшены, нет invalid scalar.
- `truncated`: превышен scan/retention budget. Reasons enum:
  `char_budget`, `occurrence_budget`; порядок именно такой.
- `invalid`: в просмотренной части есть surrogate, reason `invalid_scalar`
  идёт первым, остальные budgets сохраняются. Это невалидная Unicode scalar
  string, а не доказательство prompt injection. Невиденный хвост неизвестен.

`InspectionReason.UNKNOWN` зарезервирован для расширений; текущий алгоритм
не скрывает исключения общим catch. Никаких verdict `safe/attack`, удаления,
нормализации, декодирования скрытых сообщений или автоматического допуска.
Для проверки эффекта нужны [отдельные полномочия и validator](action_validation.md).
Correct report может содержать инструкцию как точное значение.
