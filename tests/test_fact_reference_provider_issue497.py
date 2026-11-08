"""Versioned handling of provider metadata, without changing model instructions."""
import copy

import pytest

from examples import fact_reference_host as v1
from examples import fact_reference_host_v2 as v2
from tests.test_fact_reference_host_issue497 import host, proposal


def test_only_provider_metadata_changes(tmp_path):
    original,_,output=host(tmp_path, host_type=v2.FactReferenceHost)
    assert v1.SYSTEM == v2.SYSTEM
    call={'function':{'index':0,'name':'select_facts','arguments':proposal(original)}}
    before=copy.deepcopy(call)
    assert original.native([call])['status']=='committed'
    assert call==before and (output/'report.json').is_file()


@pytest.mark.parametrize('index', [True,-1,1,'0',None])
def test_malformed_provider_index_rejected(tmp_path,index):
    original,_,output=host(tmp_path, host_type=v2.FactReferenceHost)
    call={'function':{'index':index,'name':'select_facts','arguments':proposal(original)}}
    assert original.native([call])['status']=='blocked'
    assert list(output.iterdir())==[]


def test_extra_model_keys_and_wrong_selection_still_rejected(tmp_path):
    original,_,output=host(tmp_path, host_type=v2.FactReferenceHost)
    for args in ({'scope_id':'request-1','fact_ids':['F1']},
                 dict(proposal(original),operation='other')):
        call={'function':{'index':0,'name':'select_facts','arguments':args}}
        assert original.native([call])['status']=='blocked'
    call={'function':{'index':0,'name':'select_facts','arguments':proposal(original),'other':1}}
    assert original.native([call])['status']=='blocked'
    assert list(output.iterdir())==[]
