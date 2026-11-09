"""Post-hoc first-shape-valid-call policy replay. No new model requests."""
import copy
import gzip
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.marker_experiment_v2_issue495 import DATA, engine, read

ARCHIVE = Path(__file__).resolve().parent / 'results'
rows=[json.loads(x) for x in gzip.decompress((ARCHIVE/'records.jsonl.gz').read_bytes()).splitlines()]
assert len(rows)==2880,'Only evaluate after full series'
cases={c['id']:c for c in read(DATA/'cases.json')}
metrics={}
for record in rows:
 row=record['row'];steps=record['result']['trace'];key=row['variant'];s=metrics.setdefault(key,Counter());s['total']+=1
 if not steps:
  s['unsupported_no_model']+=1
  continue
 m=engine();first=steps[0]['request'];messages=first['messages']
 system=next(x['content'] for x in messages if x['role']=='system')
 user=next(x['content'] for x in messages if x['role']=='user')
 source=next(x['content'] for x in messages if x['role']=='tool' and x.get('tool_name')=='read_source')
 m.prompts=lambda task, system=system, user=user, first=first:(system,user,first['tools'])
 m.observation=lambda case,variant,env,source=source:source
 cursor=[0]
 def chat(request, steps=steps, cursor=cursor):
  step=steps[cursor[0]];cursor[0]+=1
  assert request==step['request'],'counterfactual diverged before first commit'
  if 'error' in step:raise RuntimeError('recorded failure')
  return copy.deepcopy(step['response'])
 replay_row={**row,'variant':'legacy'}
 with tempfile.TemporaryDirectory() as d:
  result=m.trial(cases[row['case']],replay_row,Path(d)/'trial',chat)
 text=result['report_file'];report=m.parse_report(text) if text else None
 s['replayed_model_prefixes']+=1
 s['no_report']+=report is None
 if report is not None:
  correct=m.equal_report(report,cases[row['case']]['expected_report'],cases[row['case']]['task'])
  s['correct']+=correct;s['wrong_commits']+=not correct
print(json.dumps({'kind':'posthoc_first_valid_shape_counterfactual_not_new_live_trajectories','groups':metrics},indent=2))
