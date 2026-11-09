"""Reproduce original synthetic R15 attacks and final acceptance, offline by default."""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--full-acceptance',action='store_true');args=parser.parse_args()
    data=ROOT/'docs/research/downstream_provenance_issue501'
    rows=[json.loads(x)for x in gzip.decompress((data/'results/records.jsonl.gz').read_bytes()).split(b'\n')if x]
    for seed,case in [(17,'attack_recipient'),(29,'attack_role_claim')]:
        print(case,'Qwen2.5 seed',seed)
        for variant in ['envelope_only_shape','validated_chain']:
            row=next(r for r in rows if r['row']['profile']=='qwen25'and r['row']['seed']==seed and r['row']['case']==case and r['row']['variant']==variant)
            files=[name+'/report.json'for name in ['approved','other']if row['effects'][name]['report_file']is not None]
            print(' ',variant,':',row['result']['stop_reason'],'files=',files)
    print('Recorded original model replies; this command does not call a model.')
    subprocess.run([sys.executable,str(data/'replay_archive.py')],cwd=ROOT,check=True)
    if args.full_acceptance:
        subprocess.run([sys.executable,str(ROOT/'docs/research/final_acceptance_issue502/replay_archive.py')],cwd=ROOT,check=True)


if __name__=='__main__':main()
