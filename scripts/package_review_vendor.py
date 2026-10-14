"""Collect redacted candidate matches for manual upstream-content review."""
import argparse
import hashlib
import json
from pathlib import Path
import re

try:
    from .check_public import RULES,file_chunks
except ImportError:
    from check_public import RULES,file_chunks


def review(bundle,names):
    bundle=Path(bundle).resolve();records=[]
    for name in names:
        path=(bundle/name).resolve()
        if not path.is_relative_to(bundle):raise ValueError('Review file escaped bundle')
        findings=[];seen=set()
        with path.open('rb') as stream:
            for _,raw in file_chunks(name,stream):
                variants=[('bytes',raw)]+[('utf16-'+str(offset),raw[offset:].decode('utf-16-le',errors='replace').encode('utf-8')) for offset in (0,1)]
                for mode,data in variants:
                    for rule,pattern in RULES.items():
                        # The bounded-mail regex is expensive on binary runs;
                        # its literal domain is a necessary condition.
                        if rule=='personal mail' and b'tugraz.at' not in data.lower():continue
                        for match in pattern.finditer(data):
                            context=data[max(0,match.start()-60):match.start()]+b'<MATCH>'+data[match.end():match.end()+60]
                            for other in RULES.values():context=other.sub(b'<REDACTED>',context)
                            value={'rule':rule,'mode':mode,'match_sha256':hashlib.sha256(match.group()).hexdigest(),'match_size':len(match.group()),'redacted_context':re.sub(rb'[^ -~]',b'.',context).decode()}
                            key=json.dumps(value,sort_keys=True)
                            if key not in seen:findings.append(value);seen.add(key)
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        records.append({'file':name,'sha256':digest,'matches':findings})
    return records


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('bundle',type=Path);parser.add_argument('--file-list',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=review(args.bundle,args.file_list.read_text().splitlines());args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
