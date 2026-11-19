"""Bounded-char prefix selection avoids quadratic encoding; bytes stay stable."""
import hashlib

import pytest

from v8unpack_agent import form_context as fc
from v8unpack_agent.form_summary import FormSummary
from v8unpack_agent.untrusted_diagnostics import detect_untrusted_chars, diag_line
from v8unpack_agent.untrusted_transport import decode_block, encode_block


def context(source):
    return fc.FormContext('Demo','','CommonForm','',source,FormSummary(),{})


def render(value,cap=-1,**kwargs):
    return fc.to_llm_prompt_fragment(value,cap,sections=('form','bsl'),representation='framed',**kwargs)


def test_large_source_has_sublinear_encoding_attempts_and_frozen_output(monkeypatch):
    value=context('Demo();\n'*2000);calls=0
    original=fc.encode_block
    def counted(*args,**kwargs):
        nonlocal calls
        calls+=1
        return original(*args,**kwargs)
    monkeypatch.setattr(fc,'encode_block',counted)
    result=render(value,1000)
    assert calls<=20
    assert hashlib.sha256(result.encode()).hexdigest()=='fdcd1ca5ced2d5a99267a3edd1bee7ab85730fc7f1b10db2d8648b9fef866849'
    assert decode_block(result.split('## BSL\n',1)[1]).last_line==48


@pytest.mark.parametrize('count',[0,1,9,10,11,99,100,101])
@pytest.mark.parametrize('line',['\n','Demo();\n','Demo("\u202e");\n'])
def test_char_search_matches_exhaustive_reference_at_header_boundaries(count,line):
    source=line*count;value=context(source);full=render(value)
    prefix=full.split('## BSL\n',1)[0]
    lines=fc._split_complete_lines(source)
    diag=diag_line(detect_untrusted_chars(source, raw=b""))
    for cap in sorted({len(prefix),len(full)-1,len(full),len(full)+1,len(prefix)+300}):
        expected=prefix
        for n in range(len(lines),-1,-1):
            piece='## BSL\n'+encode_block(''.join(lines[:n]),n=2,kind='bsl',total_lines=len(lines),diag=diag)
            if len(prefix+piece)<=cap:
                expected=prefix+piece;break
        assert render(value,cap)==expected
    # Full blocks omit the omitted-range field; they may be shorter than n-1.
    assert render(value,len(full))==full


def test_nonmonotonic_token_counter_keeps_original_descending_candidate_order():
    value=context('Demo();\n'*2000);seen=[]
    def tokens(text):
        if '## BSL\n' not in text:return 0
        count=decode_block(text.split('## BSL\n',1)[1]).last_line
        seen.append(count)
        return 0 if count%5==2 else 100
    result=render(value,1000,max_tokens=1,count_tokens=tokens)
    assert seen==[48,47]
    assert decode_block(result.split('## BSL\n',1)[1]).last_line==47


def test_invalid_counter_fallback_preserves_character_budget_output():
    value=context('Demo();\n'*2000)
    assert render(value,1000,max_tokens=1,count_tokens=lambda _:True)==render(value,1000)
