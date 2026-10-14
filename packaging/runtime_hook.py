"""Frozen process uses only its bundled browser components and safe console streams."""
import os
from pathlib import Path
import sys

os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(sys.executable).resolve().parent/'components'/'browsers')
chosen=None
if '--data-dir' in sys.argv:
    position=sys.argv.index('--data-dir')
    if position+1<len(sys.argv):chosen=Path(sys.argv[position+1]).expanduser().resolve()
chosen=chosen or (Path(os.environ['STUDYCENTER_PUBLIC_DATA_DIR']).expanduser().resolve() if os.environ.get('STUDYCENTER_PUBLIC_DATA_DIR') else None)
if chosen:cache=chosen/'cache'/'speech-library'
elif sys.platform=='win32':cache=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'StudyCenter-Public'/'Cache'/'speech-library'
else:cache=Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'studycenter-public'/'speech-library'
os.environ['HF_HOME']=str(cache)
os.environ['HF_HUB_CACHE']=str(cache/'hub')
os.environ['HF_XET_CACHE']=str(cache/'xet')
os.environ['HF_ASSETS_CACHE']=str(cache/'assets')
os.environ['HF_HUB_DISABLE_XET']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
# The application's explicit, checksummed model downloader owns network requests.
os.environ['HF_HUB_OFFLINE']='1'
if sys.platform=='linux':
    libraries=Path(sys.executable).resolve().parent/'components'/'ocr'/'usr'/'lib'/'x86_64-linux-gnu'
    os.environ['LD_LIBRARY_PATH']=str(libraries)+os.pathsep+os.environ.get('LD_LIBRARY_PATH','')
if sys.stdout is None:sys.stdout=open(os.devnull,'w',encoding='utf-8')
if sys.stderr is None:sys.stderr=open(os.devnull,'w',encoding='utf-8')
