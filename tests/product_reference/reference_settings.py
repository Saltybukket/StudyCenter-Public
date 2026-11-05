from studycenter_public.core.config import VIENNA
from pathlib import Path
from studycenter_public.product import make_settings
from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore

def Settings(data_dir, **values):
    paths=AppPaths.create(Path(data_dir)/"public-platform")
    settings,_=make_settings(paths,SecretStore(paths.instance_id,session_only=True))
    settings.data_dir=Path(data_dir).resolve()
    (settings.data_dir/"documents").mkdir(exist_ok=True)
    for key,value in values.items():setattr(settings,key,value)
    return settings
