# Контракт представления недоверенного содержимого

Нормативный документ эпика #455. Основание — ADR
[untrusted_content_contract_issue457.md](research/untrusted_content_contract_issue457.md)
(#457); реализация — #458 (диагностика), #459 (`search_context`), #460
(фрагменты формы и модуля), #461 (CLI). Эталоны прежнего вывода — #456.

## Граница

Контракт задаёт только форму, в которой текст из выгрузки попадает в выводы
для LLM: потребитель может однозначно отделить данные выгрузки от служебной
структуры вывода и от собственных инструкций. Контракт не утверждает
устойчивость какой-либо модели к prompt injection, не защищает от поведения
модели или клиента и не гарантирует корректность ответа модели.

## Режимы

| Поверхность | Как включить `framed` | По умолчанию |
|---|---|---|
| `to_llm_prompt_fragment` | `representation="framed"` | `legacy` |
| `to_llm_module_fragment` | `representation="framed"` (и `bsl_bytes` для `file_bom`) | `legacy` |
| `build_search_context`, `search_with_context` | `representation="framed"` | `legacy` |
| `v8unpack-agent-run`, `RunOptions` | `--representation framed`, `representation="framed"` | `legacy` |
| `search_cli query` | `--representation framed` | `legacy` |

В режиме `legacy` вывод совпадает с эталонами #456 «бит в бит». Единственное
изменение без включения режима — запись параметра `--representation` в
каталоге `--describe-cli` и его `schema_version` 1.1 (MINOR,
[cli_introspection.md](cli_introspection.md)). Коды завершения не меняются.

## Блок недоверенного текста

```
<<<UNTRUSTED_BEGIN n=1 kind=bsl lines=1-2/5 chars=27 final_newline=1 truncated=1 omitted=3-5 sha256=<hex>>>>
<<<UNTRUSTED_DIAG n=1 file_bom=1 cf=1 bidi=1 zero_width=0 soft_hyphen=0 other_cf=0 positions=U+202E@4:13:bidi>>>
| \u{FEFF}// SOURCE TEXT
| ## SUMMARY
<<<UNTRUSTED_END n=1>>>
```

(синтетический пример фикстуры #456)

- Служебные строки начинаются с `<<<`; каждая строка данных начинается с `| `.
  Любая строка исходника, в том числе похожая на служебную, остаётся строкой
  данных.
- Внутри строки данных `\` записывается как `\\`, символы категорий Cf и Cc
  (кроме табуляции) — как `\u{XXXX}`.
- `kind`: `bsl`, `text`, `name`, `path`. `lines=a-b/T` — включённый диапазон и
  последняя строка источника (для `search_context` — координаты строк среза),
  `chars` и `sha256` — длина и SHA-256 включённого канонического текста,
  `final_newline` — завершающий перевод строки.
- Усечение только целыми строками: `truncated=1` и `omitted=b+1-T`. Маркер
  `[TRUNCATED: …]` прежнего режима в `framed` не выводится.
- Строка `<<<UNTRUSTED_DIAG …>>>` (после заголовка блока BSL) — признак
  файлового BOM, счётчики Cf по группам и позиции в форме
  `codepoint@line:column:group` через запятую. При отсутствии Cf — `positions=-`.
- В JSON-карточках `search_context/2` строковые значения экранируются по тем
  же правилам. С `include_text=True` блоки находятся в верхнем поле `text`.

Обратимость: `v8unpack_agent.untrusted_transport.decode_block` возвращает
включённый канонический текст точно и отвергает блок с неверными `sha256`,
`chars`, диапазоном или escape-последовательностью. Из усечённого блока
восстанавливаются только строки `a..b`.

## Каноническое и транспортное представление

- Каноническое: файлы выгрузки, индексы и снимки поиска,
  `SearchDocument.text`, `ModuleEntry`, `FormContext.bsl_text`, `text_sha256`
  срезов, `Document.page_content`, metadata и текст для эмбеддингов. Режим их
  не меняет.
- Транспортное: только итоговый текст для генерационной модели, строится в
  момент вывода и нигде не сохраняется. Бюджеты `max_chars`/`max_tokens`
  применяются к транспортному тексту.
- Для #152 и #290 транспортное представление применяется лишь при отдельной
  сборке входа генерационной модели — ко всем включаемым подконтрольным
  выгрузке значениям, включая значения metadata, — и не передаётся в эмбеддер.
  #291 включает режим явно; новые поля LLM-проекции #211 (#217) следуют
  матрице ADR.

## Диагностика Cf и BOM

`v8unpack_agent.untrusted_diagnostics.detect_untrusted_chars(text, *, raw=None)`
(#458): каждое вхождение категории Cf с `codepoint`, `line`, `column` (с 1, в
кодовых точках), группой `bidi`/`zero_width`/`soft_hyphen`/`other_cf` по
фиксированной таблице; `counts`, `total`; `file_bom` по исходным байтам
(`null`, если байты неизвестны); `unidata_version` интерпретатора отдельным
полем. Символы, позиции, группы и количества одинаковы на Python 3.10 и 3.12;
`unidata_version` может различаться. Текст не изменяется.

Где появляется: строка `<<<UNTRUSTED_DIAG …>>>` во фрагментах формы и модуля,
`ModuleProjection.diagnostics`, `included[].diagnostics` в `search_context/2`
(без `file_bom`: BOM корпуса снят нормализацией #324). Post-run report и
envelope `--json` новых полей не получают.
