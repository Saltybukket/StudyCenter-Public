"""Inspect uncompressed frozen Python code, constants and filenames before release."""
import argparse
import hashlib
import json
from pathlib import Path
import types

try:
    from .check_public import RULES
except ImportError:
    from check_public import RULES

# Exact upstream constants reviewed against wheel/stdlib source. A version or
# location change requires review again; filenames and whole modules are never exempt.
REVIEWED_CONSTANTS = {
    # Official Actions CPython 3.12.15 archive fcb672b0437c2d8df5565b7cc250b2b0d7455e2b8d6a4a107cfa6f2efe4f7ed8.
    # Its generated sysconfig source hash is 7fdde55edb7433a11d2993bcb644f37c640455558482fa11f73068153dbabcc6.
    ('_sysconfigdata__linux_x86_64-linux-gnu','code.constant70','personal path','a98ed7bc2ef7e8dca0eba84441b2d3bf9eb2155850f9345268dc42a50f044200'): 'Official Actions CPython 3.12.15 COVERAGE_INFO build metadata',
    ('_sysconfigdata__linux_x86_64-linux-gnu','code.constant74','personal path','9557403f865c4980c90d01a4745ed6363602da31e485d40713d3e3be4f4ed3ba'): 'Official Actions CPython 3.12.15 COVERAGE_REPORT build metadata',
    ('_sysconfigdata__linux_x86_64-linux-gnu','code.constant1082','personal path','e99e30d293af7f15bcd364f3e99b2f125795454959b427514bade4c13db8e789'): 'Official Actions CPython 3.12.15 RUNSHARED build metadata',
    ('_sysconfigdata__linux_x86_64-linux-gnu','code.constant1140','personal path','7a44bbb09c0a9b304cfb844cb8f4a1e26714f6fb87d5abb85fbea1ea4a41ce88'): 'Official Actions CPython 3.12.15 TESTPYTHON build metadata',
    ('_sysconfigdata__linux_x86_64-linux-gnu','code.constant1143','personal path','50f75f73a8f7cacc6b2d3e632f6fb4cae0c813484a790d2c9214ef5d14659d50'): 'Official Actions CPython 3.12.15 TESTRUNNER build metadata',
    ('_sysconfigdata__linux_x86_64-linux-gnu','code.constant1180','personal path','194fcb3e40f9babe0faf8f8b2364d42f36756f923dbde0d8f1618fbb31727c54'): 'Official Actions CPython 3.12.15 absolute source/build-directory metadata',
    ('cryptography.hazmat.primitives.serialization.ssh','code.constant27','credential','03d104c669e3c7b6be7f989db8b12c8b910d3be8c1e2a73c9369d3cc0ba803b5'): 'SSH PEM delimiter in upstream cryptography parser',
    ('numpy.__config__','code.constant26','personal path','ea6bf649aae51e9afe48ff44c8dd92d4139615e773bdfa2ed63420dd609a5ec3'): 'Public NumPy wheel build metadata',
    ('numpy.__config__','code.constant27','personal path','36b6d15032e8dddc3c01de5256aa7e5a827337e579972d87f0a47b10cd1eb219'): 'Public NumPy wheel build metadata',
    ('numpy.__config__','code.constant32','personal path','0fbbc480c90635fde63adc7e6baedb02ac7b1fede2a273a970796d641c7505aa'): 'Public NumPy wheel build metadata',
    ('packaging.licenses._spdx','code.constant1363','credential','bcb7c64ced6f4245b75c64f6f12130b2271a99218df17baccba124fe73e70a93'): 'Published SPDX Asterisk license identifier',
    ('packaging.licenses._spdx','code.constant1364','credential','189340769c3a4bb216d41bffb91d3b51e6fe4436d16c94e863c40cc844025cd6'): 'Published SPDX Asterisk license identifier',
    ('wsgiref.simple_server','code.constant25','private endpoint','83a85524e82947e1948f7d0559f527a2dc8fdfcaeb0b6572b9f24ea7b0a2718d'): 'CPython standard library demonstration constant',
    ('xmlrpc.client','code.constant72','private endpoint','0121783acd70272b07eae5af255f4fbf2362a61e32e0ef38b460878f5159104f'): 'CPython standard library demonstration constant',
}


def classify_findings(findings):
    unresolved=[];reviewed=[]
    for finding in findings:
        key=tuple(finding.get(name) for name in ('module','location','rule','sha256'))
        if key in REVIEWED_CONSTANTS:reviewed.append(dict(finding,review=REVIEWED_CONSTANTS[key]))
        else:unresolved.append(finding)
    return {'findings':unresolved,'raw_findings':findings,'reviewed_findings':reviewed}


def inspect_code(code,module):
    findings=[]
    def inspect(value,location):
        if isinstance(value,types.CodeType):
            inspect(value.co_filename,location+'.filename')
            for index,constant in enumerate(value.co_consts):inspect(constant,location+'.constant'+str(index))
        elif isinstance(value,(tuple,list,frozenset)):
            for index,member in enumerate(value):inspect(member,location+'.member'+str(index))
        elif isinstance(value,(str,bytes)):
            raw=value.encode('utf-8',errors='replace') if isinstance(value,str) else value
            for name,pattern in RULES.items():
                if pattern.search(raw):findings.append({'module':module,'location':location,'rule':name,'sha256':hashlib.sha256(raw).hexdigest(),'constant_size':len(raw)})
    inspect(code,'code')
    return findings


def audit(executable):
    from PyInstaller.archive.readers import CArchiveReader
    import marshal
    reader=CArchiveReader(str(executable));findings=[];count=0
    for name,record in reader.toc.items():
        if record[-1]=='s':
            code=marshal.loads(reader.extract(name));findings.extend(inspect_code(code,name));count+=1
        elif record[-1]=='z':
            pyz=reader.open_embedded_archive(name)
            for module in pyz.toc:
                code=pyz.extract(module)
                if isinstance(code,types.CodeType):findings.extend(inspect_code(code,module));count+=1
    return {'modules_checked':count,**classify_findings(findings)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=audit(args.executable)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));raise SystemExit(bool(result['findings']))
