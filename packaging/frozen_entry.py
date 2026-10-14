"""Frozen entry calls the same application main in process."""
from studycenter_public.__main__ import main


def component_check(argv):
    """Opt-in diagnostic exercises bundled native components with synthetic content."""
    import argparse
    import asyncio
    import json
    import os
    import tempfile
    from pathlib import Path
    import sys
    parser=argparse.ArgumentParser();parser.add_argument('--component-test',action='store_true');parser.add_argument('--data-dir',required=True)
    args=parser.parse_args(argv);directory=Path(args.data_dir).resolve();directory.mkdir(parents=True,exist_ok=True)
    def phase(value):
        (directory/'component-phase.txt').write_text(value,encoding='utf-8')
    # Chromium's Linux singleton socket must fit the 108-byte Unix-socket limit.
    # A short owned directory on ext4 also works for launches from mounted paths.
    browser_temporary=tempfile.TemporaryDirectory(prefix='studycenter-public-build-',dir='/tmp' if sys.platform=='linux' else str(directory))
    for variable in ('TMPDIR','TMP','TEMP'):os.environ[variable]=browser_temporary.name
    phase('import av');import av
    phase('import ctranslate2');import ctranslate2
    phase('import faster_whisper');import faster_whisper
    phase('import onnxruntime');import onnxruntime
    phase('import pymupdf');import pymupdf
    phase('import playwright')
    from playwright.async_api import async_playwright
    async def browsers():
        result={}
        async with async_playwright() as driver:
            for name,channel in (('dedicated_headless_shell',None),('full_chromium','chromium')):
                phase('launch '+name)
                browser=await driver.chromium.launch(headless=True,channel=channel,args=['--no-sandbox','--disable-gpu','--host-resolver-rules=MAP * ~NOTFOUND'])
                page=await browser.new_page();await page.set_content('<html><title>Synthetic package browser</title><body>Own bundled driver</body></html>')
                if await page.title()!='Synthetic package browser':raise RuntimeError('Bundled browser did not render synthetic content')
                result[name]={'version':browser.version,'rendered':True}
                await browser.close()
        return result
    checks={'speech_runtime_imports':True,'cpu_compute_types':sorted(ctranslate2.get_supported_compute_types('cpu')),'pdf_runtime_import':True,'browsers':asyncio.run(browsers()),'browser_runtime_path':os.environ['PLAYWRIGHT_BROWSERS_PATH'],'models_downloaded':False,'platform':sys.platform}
    (directory/'component-check.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    phase('complete')
    browser_temporary.cleanup()
    return 0

if __name__ == '__main__':
    import sys
    try:
        result=component_check(sys.argv[1:]) if '--component-test' in sys.argv else main()
    except Exception:
        from pathlib import Path
        import traceback
        if '--data-dir' in sys.argv:
            position=sys.argv.index('--data-dir')
            if position+1<len(sys.argv):
                directory=Path(sys.argv[position+1]);directory.mkdir(parents=True,exist_ok=True)
                (directory/'startup-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        result=1
    raise SystemExit(result)
