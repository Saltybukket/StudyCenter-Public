"""Frozen process uses only its bundled browser components and safe console streams."""
import os
from pathlib import Path
import sys
from studycenter_public.platform.paths import read_owned_identity, reject_links

program=reject_links(Path(sys.executable)).resolve().parent
chosen=None
if '--data-dir' in sys.argv:
    position=sys.argv.index('--data-dir')
    if position+1<len(sys.argv):chosen=reject_links(Path(sys.argv[position+1]).expanduser()).resolve()
chosen=chosen or (reject_links(Path(os.environ['STUDYCENTER_PUBLIC_DATA_DIR']).expanduser()).resolve() if os.environ.get('STUDYCENTER_PUBLIC_DATA_DIR') else None)
if chosen:
    if chosen==program or chosen.is_relative_to(program) or program.is_relative_to(chosen):
        raise ValueError('Nutzerdaten brauchen einen eigenen Ordner außerhalb der Programmdateien.')
    read_owned_identity(chosen)
    cache=chosen/'cache'/'speech-library'
elif sys.platform=='win32':cache=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'StudyCenter-Public'/'Cache'/'speech-library'
else:cache=Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'studycenter-public'/'speech-library'
library_paths={'HF_HOME':cache,'HF_HUB_CACHE':cache/'hub','HF_XET_CACHE':cache/'xet','HF_ASSETS_CACHE':cache/'assets'}
for path in library_paths.values():reject_links(path)
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(reject_links(program/'components'/'browsers'))
os.environ.update({name:str(path) for name,path in library_paths.items()})
os.environ['HF_HUB_DISABLE_XET']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
# The application's explicit, checksummed model downloader owns network requests.
os.environ['HF_HUB_OFFLINE']='1'
if sys.platform=='linux':
    libraries=Path(sys.executable).resolve().parent/'components'/'ocr'/'usr'/'lib'/'x86_64-linux-gnu'
    os.environ['LD_LIBRARY_PATH']=str(libraries)+os.pathsep+os.environ.get('LD_LIBRARY_PATH','')
if sys.stdout is None:sys.stdout=open(os.devnull,'w',encoding='utf-8')
if sys.stderr is None:sys.stderr=open(os.devnull,'w',encoding='utf-8')
