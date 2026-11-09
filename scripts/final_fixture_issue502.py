"""Generic real-layout construction and capture; payload data authored after codefreeze."""
from __future__ import annotations

import hashlib
import json

from examples.snapshot_selection_agent import SelectionProjection
from scripts.form_fixture_issue500 import encoded, fixture_pair
from v8unpack_agent.action_validation import create_report_contract
from v8unpack_agent.bsl_facts import analyze_indexed_module
from v8unpack_agent.form_evidence import FormSource, analyze_form
from v8unpack_agent.modules import ModuleEntry, ModuleIndex


def bsl_source(names,*,literal=None,comment=''):
    prefix=''.join('// '+line+'\n'for line in comment.splitlines())
    if literal is not None:
        value=literal.replace('"','""').replace('\n','\n|')
        return prefix+'Function Payload() Export\nReturn "'+value+'";\nEndFunction\n'
    return prefix+''.join('Function '+name+'() Export\nReturn Undefined;\nEndFunction\n'for name in names)


def form_source(names):
    metadata,elements=fixture_pair(names)
    return {'metadata_text':encoded(metadata).decode(),'elements_text':encoded(elements).decode()}


def mutate_family(family,payload):
    """Restricted synthetic text channel, not arbitrary source-code control.

    Manual-by-construction expected is independent of product extractor. All
    transformations explicit/public. Unsupported generated sources stay so.
    """
    case={'id':family['id'],'task':family['task'],'kind':'attack','family':family['id']}
    if family['task']=='exports':
        names=family['names'];case.update(bsl_text=bsl_source(names,comment=payload),expected_report={'function_names':names})
    elif family['task']=='query_text':
        case.update(bsl_text=bsl_source([],literal=payload),expected_report=None if '\n' in payload or '\r' in payload else {'query':payload})
    else:
        names=[*family['prefix_names'],payload]
        case.update(**form_source(names),expected_report=None if not payload or len(names)!=len(set(names)) else {'attribute_names':names})
    return case


def prepare(case,folder):
    inputs=folder/'input';inputs.mkdir(parents=True)
    if case['task']=='form_fields':
        raw=[]
        for key,name in [('metadata_text','CommonForm.json'),('elements_text','CommonForm.elem.json')]:
            value=case.get(key);data=value.encode('utf-8') if value is not None else None
            raw.append(data)
            if data is not None:(inputs/name).write_bytes(data)
        source=FormSource('selected-form',*raw);evidence=analyze_form(source)
        if evidence.status!='complete':return None,evidence.status,evidence.reason
        return SelectionProjection(evidence.contract()),'complete',None
    text=case['bsl_text'];data=text.encode('utf-8')
    entry=ModuleEntry('object','metadata_object','Catalog','Demo','Catalog/Demo/Catalog.obj.bsl',
        'ok',len(data),hashlib.sha256(data).hexdigest())
    path=inputs.joinpath(*entry.path.parts);path.parent.mkdir(parents=True);path.write_bytes(data)
    index=ModuleIndex.from_entries([entry]);facts=analyze_indexed_module(inputs,index,entry.module_id)
    if facts.status!='complete':return None,facts.status,facts.reason
    try:
        contract=create_report_contract(inputs,index,entry.module_id,
            operation='returned_string' if case['task']=='query_text' else 'exported_functions',
            declaration_name='Payload' if case['task']=='query_text' else None)
        return SelectionProjection(contract),'complete',None
    except ValueError:return None,'unsupported','operation_not_complete'


def raw_observation(case):
    return (case['bsl_text'] if case['task']!='form_fields' else
        json.dumps({key:case.get(key) for key in ['metadata_text','elements_text']},ensure_ascii=False,sort_keys=True))
