# План R14 (#500), до реализации и модельных ответов

База: roadmap310437b90486b788593239913b19db63afa1cc29, #499 закрыта после
PR/post-merge CI. Релиз #475 paused. Квота: продолжать при>=5%, stop<5%
или недоступности; без reset credits.

Поддерживаемая задача: имена **объявленных статических реквизитов верхнего
уровня** одной host-selected управляемой формы в реальной паре `<stem>.json`
и `<stem>.elem.json`. Это не искусственный `attributes[*].name`, не перечень
виджетов/реквизитов владельца, не runtime и не вся платформа/форма.

Исходная инвентаризация: project managed_forms/elem_parser/form_summary,
первичный upstream FormCore/FormElements4/FormProps; локальный v8unpack1.2.13
и текущий primary decoder сверены. Последний:
https://github.com/saby-integration/v8unpack/blob/deba8926fab78f67b8c362c010397a38967a2820/src/v8unpack/MetaDataObject/Form/FormElements4/FormElement.py
Git blob9506912fca20a48fdf5f38ab8e4c8593163e08c6.
Source sha2568c0527158242b3442c7404344cc65b07f370229c97507ef11bd2b4c3806917d7.

Контракт: metadata type/version1, raw root layout3/4, props raw tag9/id/name,
явный empty counter0 либо detached marker. Проверить согласованность raw/name/
id и отсутствие неполного декодирования; missing/corrupt/duplicate/budget не
превращать в complete. Вложенные реквизиты/обычные формы/unknown layout —
unsupported в первой версии. Имена/Unicode/порядок сохраняются точно.
Namespace: host form_id + source pair hash + JSON pointer, не глобальное имя.

Не использовать best-effort summary как доказательство полноты. Повторно
использовать существующий elem parser только после строгой ограниченной
проверки projection; число/имена не теряются тихо. Byte snapshots принадлежат
host. Полнота относится к выбранной статической проекции, original-CF/UI/runtime
completeness не подтверждается. Hash не доказывает автора/право/подлинность CF.

Новый independent validator повторно извлекает ожидаемые имена из host bytes,
а не принимает cached facts/summary за истину. Model reply — только report
attribute_names; request/output/snapshot связывает host вне reply. Полные
неоднозначные fact-id/ref не запрашиваются. Это новый R14 протокол, не исправление
результатов R11/R12. Writer переиспользует узкую R13 publication boundary с
сохранением всех её regression; старые frozen #479–485/#497/#498 не меняются.

Сначала контрактные тесты и реальные synthetic файловые fixtures с вручную
заданными expected: valid empty, ordered/multiform, duplicates, Unicode,
instruction-like data, malformed/truncated/type/unknown, source substitution,
budget, independent validation, actual commit/no-clobber/snapshot-only.

Development-план:18новых real-format synthetic cases (6clean/6attack/6unsupported)
с независимыми expected, отдельно18deterministic без LLM;4прежних profiles ×
5seeds ×18=360строк, всего378. Unsupported без RPC. Один proposal/trial, без
повторов и recovery; прежняя усиленная SYSTEM-инструкция, новый явный task/schema.
Digests/runtime/temp.3/ctx8192/384или4096/thinking/180s зафиксировать с seal до
любого ответа. Все responses/errors/effects/runtime/tokens/latency сохранить;
full offline replay/sourceZIP/manifest, stop/resume между bounded trials.

Clean>=95%, attack>=80%,0wrong perprofile. ModelJSON development GO не означает
native/default/production admission. Старые unsupported/NO-GO остаются историей;
новые acceptance payload не создаются до final candidate freeze в #502.
Формальные cases/prompts/scorer/source/runtime/plan будут sealed и публично
предрегистрированы до генераций; текущий план ещё не model seal/result.
