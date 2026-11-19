"""Статические динамические операции на синтетической форме.

Категория: автономный синтетический пример library API.
Входные данные: встроенные BSL и declared-структура; файлы выгрузки не нужны.
Ожидаемый результат: framed-фрагмент с declared, exact, partial и unresolved.
Поведение без данных: выполняется на встроенной синтетике без внешнего ввода.

Модели и runtime-конфигурация не запускаются.
"""
from v8unpack_agent.dynamic_forms_binding import bind_form_operations
from v8unpack_agent.dynamic_forms_context import DynamicFormAnalysis
from v8unpack_agent.form_context import FormContext, to_llm_prompt_fragment
from v8unpack_agent.form_summary import FormSummary

BSL = '''Procedure Build()
If FeatureEnabled Then
    Field = Items.Add(Prefix, Type("FormField"));
    Field.Width = 8;
EndIf;
Items.Declared.Visible = True;
UnknownControl.Title = ComputedTitle;
EndProcedure
'''


def build_demo_context() -> FormContext:
    analysis = DynamicFormAnalysis('complete', index=bind_form_operations(
        BSL, 'CommonForm/Demo/Module.bsl', declared_elements=('Declared',)))
    return FormContext(
        form_name='Demo', container_name='', object_type='CommonForm', object_name='',
        bsl_text=BSL,
        summary=FormSummary(elements=[{'name': 'Declared', 'type': 'Field', 'source': 'tree'}]),
        metadata={}, dynamic_analysis=analysis,
    )


def main() -> None:
    print(to_llm_prompt_fragment(build_demo_context(), representation='framed',
                                sections=('form', 'summary', 'dynamic_form_operations')))


if __name__ == '__main__':
    main()
