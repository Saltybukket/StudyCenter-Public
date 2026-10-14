"""Only non-secret settings live on disk."""
import json
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from .platform.paths import atomic_json

VIENNA=ZoneInfo('Europe/Vienna')

@dataclass
class Settings:
    paths: object
    def __post_init__(self):
        self.path=self.paths.config/'settings.json'
        self.values=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def get(self,key,default=None): return self.values.get(key,default)
    def set(self,key,value):
        if key not in {'onboarding_step','onboarding_complete','theme','compact','direct_mode','cloud_consent','mcp_enabled','mcp_write','speech_enabled','speech_model','background','autostart'}:
            raise ValueError('Unbekannte Einstellung')
        self.values[key]=value;atomic_json(self.path,self.values)
