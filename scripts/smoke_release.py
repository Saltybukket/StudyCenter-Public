"""Exercise a frozen bundle with synthetic data, real local HTTP and optional OCR."""
import argparse
import http.cookiejar
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from uuid import uuid4


def smoke(executable,output=None,ocr=True):
    executable=Path(executable).resolve()
    root=Path(output).resolve() if output else Path(tempfile.mkdtemp(prefix='studycenter-public-package-smoke-'))
    root.mkdir(parents=True,exist_ok=True)
    environment=os.environ.copy()
    library_cache=root/'diagnostic-library-cache'
    for variable,suffix in (('HF_HOME',''),('HF_HUB_CACHE','hub'),('HF_XET_CACHE','xet'),('HF_ASSETS_CACHE','assets')):environment[variable]=str(library_cache/suffix)
    environment['HF_HUB_DISABLE_TELEMETRY']='1';environment['HF_HUB_OFFLINE']='1';environment['HF_HUB_DISABLE_XET']='1'
    self_data=root/'Self test ä space';self_data.mkdir(exist_ok=True)
    subprocess.run([str(executable),'--self-test','--data-dir',str(self_data),'--no-browser'],check=True,timeout=60,env=environment)
    with sqlite3.connect(self_data/'studycenter-public.sqlite3') as con:
        assert con.execute('SELECT count(*) FROM action_receipts').fetchone()[0]==1
    component_data=root/'Native components ö space'
    subprocess.run([str(executable),'--component-test','--data-dir',str(component_data)],check=True,timeout=90,env=environment)
    component_result=json.loads((component_data/'component-check.json').read_text())
    assert component_result['browsers']['dedicated_headless_shell']['rendered'] and component_result['browsers']['full_chromium']['rendered']
    data=root/'HTTP test ü space';data.mkdir(exist_ok=True)
    process=subprocess.Popen([str(executable),'--data-dir',str(data),'--port','0','--no-browser'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)
    checks={'frozen_self_test':True,'bundled_driver_and_both_browsers':True,'native_speech_imports_cpu':component_result['speech_runtime_imports']}
    try:
        endpoint_file=data/'runtime.json';deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Frozen server exited: '+process.communicate()[1].decode(errors='replace'))
            if endpoint_file.exists():
                try:
                    endpoint=json.loads(endpoint_file.read_text())['url']
                    with urllib.request.urlopen(endpoint+'/api/health',timeout=2) as response:health=json.load(response)
                    break
                except (OSError,json.JSONDecodeError):pass
            time.sleep(.2)
        else:raise RuntimeError('Frozen local server did not become ready')
        assert health['application']=='StudyCenter.Public';checks['http_health']=True
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(endpoint+'/api/session') as response:csrf=json.load(response)['csrf']
        def request(path,payload=None,content_type='application/json'):
            content=json.dumps(payload).encode() if payload is not None and content_type=='application/json' else payload
            headers={'Origin':endpoint,'X-CSRF-Token':csrf,'Content-Type':content_type}
            with opener.open(urllib.request.Request(endpoint+path,data=content,headers=headers),timeout=150) as response:
                body=response.read()
                return json.loads(body) if response.headers.get('Content-Type','').startswith('application/json') else body
        assert request('/api/items')==[]
        actions=[{'type':'create_task','title':'Synthetic package task'}];rid=str(uuid4())
        assert request('/api/actions/preview',{'actions':actions})['preview'] is True
        receipt=request('/api/actions',{'actions':actions,'request_id':rid});assert request('/api/actions',{'actions':actions,'request_id':rid})==receipt
        assert len(request('/api/items'))==1
        request('/api/actions/undo',{'request_id':rid,'undo_id':str(uuid4())});assert request('/api/items')==[]
        checks['action_preview_receipt_undo']=True
        try:
            with urllib.request.urlopen(urllib.request.Request(endpoint+'/api/actions',data=b'{}',headers={'Content-Type':'application/json'})):pass
        except urllib.error.HTTPError as error:assert error.code==403;checks['csrf_countercontrol']=True
        else:raise AssertionError('CSRF-less write was accepted')
        with opener.open(endpoint+'/') as response:assert b'StudyCenter' in response.read();checks['local_ui']=True
        if ocr:
            from PIL import Image,ImageDraw,ImageFont
            image=Image.new('RGB',(1500,500),'white');draw=ImageDraw.Draw(image)
            draw.text((80,150),'SYNTHETIC PACKAGE DOCUMENT 2026',font=ImageFont.load_default(size=42),fill='black')
            raw=io.BytesIO();image.save(raw,format='PDF',resolution=150)
            boundary='synthetic-package-'+uuid4().hex
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic-scan.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+raw.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
            doc=request('/api/documents/import',body,'multipart/form-data; boundary='+boundary)
            assert 'SYNTHETIC' in '\n'.join(page['text'] for page in doc['pages']),doc
            checks['bundled_pdf_ocr']=True
            assert request('/api/documents/'+str(doc['id'])+'/original')==raw.getvalue();checks['original_bytes_preserved']=True
        backup=request('/api/backup',{});assert backup.startswith(b'PK');checks['backup_download']=True
        subprocess.run([str(executable),'--data-dir',str(data),'--quit'],check=True,timeout=15,env=environment)
        process.wait(timeout=30);assert process.returncode==0;checks['cooperative_shutdown']=True
    finally:
        if process.poll() is None:
            subprocess.run([str(executable),'--data-dir',str(data),'--quit'],timeout=15,check=False,env=environment)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
    result={'platform':sys.platform,'checks':checks,'isolated_directory':str(root)}
    (root/'package-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--no-ocr',action='store_true')
    args=parser.parse_args();smoke(args.executable,args.output,not args.no_ocr)
