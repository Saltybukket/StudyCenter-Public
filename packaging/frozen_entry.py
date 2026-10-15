"""Frozen entry calls the same application main in process."""


def show_startup_error(message):
    import sys
    print('StudyCenter-Public: '+message,file=sys.stderr)
    quiet={'--no-browser','--self-test','--component-test','--quit'}
    if sys.platform=='win32' and getattr(sys,'frozen',False) and not quiet.intersection(sys.argv[1:]):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,message,'StudyCenter-Public: Start nicht möglich',0x10)


def browser_launch_options(channel,paths=None):
    result={'headless':True,'channel':channel,'chromium_sandbox':True,'args':['--enable-logging=stderr','--host-resolver-rules=MAP * ~NOTFOUND']}
    if paths:
        import os
        from studycenter_public.platform.paths import owned_path
        environment=os.environ.copy()
        for key,name in (('CHROME_LOG_FILE','component-browser.log'),('XDG_CONFIG_HOME','component-browser-config'),('XDG_CACHE_HOME','component-browser-cache')):
            environment[key]=str(owned_path(paths.cache/name,paths.cache))
        result['env']=environment
    return result


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
    from studycenter_public.platform.paths import AppPaths,owned_path
    args=parser.parse_args(argv);paths=AppPaths.create(args.data_dir);directory=paths.data
    def phase(value):
        owned_path(directory/'component-phase.txt',directory).write_text(value,encoding='utf-8')
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
                browser=await driver.chromium.launch(**browser_launch_options(channel,paths))
                page=await browser.new_page();await page.set_content('<html><title>Synthetic package browser</title><body>Own bundled driver</body></html>')
                if await page.title()!='Synthetic package browser':raise RuntimeError('Bundled browser did not render synthetic content')
                result[name]={'version':browser.version,'rendered':True}
                await browser.close()
        return result
    checks={'speech_runtime_imports':True,'cpu_compute_types':sorted(ctranslate2.get_supported_compute_types('cpu')),'pdf_runtime_import':True,'browsers':asyncio.run(browsers()),'browser_runtime_path':os.environ['PLAYWRIGHT_BROWSERS_PATH'],'models_downloaded':False,'platform':sys.platform}
    owned_path(directory/'component-check.json',directory).write_text(json.dumps(checks,indent=2),encoding='utf-8')
    phase('complete')
    browser_temporary.cleanup()
    return 0

def execute():
    import sys
    refused=getattr(sys,'_studycenter_startup_error',None)
    if refused is not None:
        show_startup_error(str(refused))
        return 2
    try:
        from studycenter_public.__main__ import main
        result=component_check(sys.argv[1:]) if '--component-test' in sys.argv else main()
    except Exception:
        from pathlib import Path
        import traceback
        if '--data-dir' in sys.argv:
            position=sys.argv.index('--data-dir')
            if position+1<len(sys.argv):
                diagnostic=traceback.format_exc()
                try:
                    from studycenter_public.platform.paths import AppPaths,owned_path
                    paths=AppPaths.create(sys.argv[position+1])
                    owned_path(paths.data/'startup-error.txt',paths.data).write_text(diagnostic,encoding='utf-8')
                except Exception:
                    print(diagnostic,file=sys.stderr)
        result=1
    return result


if __name__ == '__main__':
    raise SystemExit(execute())
