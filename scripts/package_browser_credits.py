"""Capture actual portable unbranded browser credits using its bundled driver."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def capture(bundle,output):
    bundle=Path(bundle).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    driver=bundle/'_internal'/'playwright'/'driver';script=output/'capture-own-browser-credits.js'
    body='''const fs=require('fs');const {chromium}=require(PACKAGE);
(async()=>{const browser=await chromium.launch({channel:'chromium',headless:true,chromiumSandbox:true,args:['--host-resolver-rules=MAP * ~NOTFOUND']});
const page=await browser.newPage();await page.goto('chrome://credits');
fs.writeFileSync(HTML,await page.content());fs.writeFileSync(TEXT,await page.locator('body').innerText());
fs.writeFileSync(RECEIPT,JSON.stringify({browser_version:browser.version(),channel:'unbranded Chromium',credits:'chrome://credits',models_downloaded:false},null,2));await browser.close();})().catch(error=>{console.error(error);process.exitCode=1;});'''
    for key,value in {'PACKAGE':str(driver/'package'),'HTML':str(output/'unbranded-credits.html'),'TEXT':str(output/'unbranded-credits.txt'),'RECEIPT':str(output/'credits-receipt.json')}.items():body=body.replace(key,json.dumps(value))
    script.write_text(body,encoding='utf-8')
    environment=os.environ.copy()
    for variable in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV'):environment.pop(variable,None)
    environment['PLAYWRIGHT_BROWSERS_PATH']=str(bundle/'components'/'browsers')
    if sys.platform=='win32':
        system=Path(environment.get('SystemRoot','C:/Windows'));environment['PATH']=os.pathsep.join(map(str,(bundle,bundle/'_internal',system/'System32',system)))
    else:
        environment['PATH']=os.pathsep.join(map(str,(bundle,bundle/'_internal',Path('/usr/bin'),Path('/bin'))))
        environment['LD_LIBRARY_PATH']=str(bundle/'components'/'ocr'/'usr/lib/x86_64-linux-gnu')
    node=driver/('node.exe' if sys.platform=='win32' else 'node')
    subprocess.run([str(node),str(script)],check=True,timeout=90,env=environment)
    return json.loads((output/'credits-receipt.json').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('bundle',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(capture(args.bundle,args.output)))
