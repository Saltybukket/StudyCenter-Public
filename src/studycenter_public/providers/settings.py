"""Non-secret profiles and an explicit active selection."""
from dataclasses import asdict
from pathlib import Path
import json
import os
import uuid
from .contracts import ProviderProfile


class ProviderSettings:
    def __init__(self, path, secrets):
        self.path=Path(path); self.secrets=secrets
        self._data={'profiles':[], 'active':None}
        if self.path.exists():
            data=json.loads(self.path.read_text(encoding='utf-8'))
            profiles=[asdict(ProviderProfile(**p)) for p in data.get('profiles',[])]
            self._data={'profiles':profiles,'active':data.get('active')}

    def _save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temp.write_text(json.dumps(self._data,indent=2),encoding='utf-8')
            os.replace(temp,self.path)
        finally: temp.unlink(missing_ok=True)

    def list_profiles(self):
        return [ProviderProfile(**p) for p in self._data['profiles']]

    def save_profile(self, profile):
        self._data['profiles']=[p for p in self._data['profiles'] if p['profile_id']!=profile.profile_id]+[asdict(profile)]
        if self._data['active'] is None: self._data['active']=profile.profile_id
        self._save()

    def select(self, profile_id):
        if not any(p.profile_id==profile_id for p in self.list_profiles()): raise ValueError('Unknown profile')
        self._data['active']=profile_id; self._save()

    def active_profile(self):
        return next((p for p in self.list_profiles() if p.profile_id==self._data['active']),None)

    def set_api_key(self, profile_id, value):
        if not any(p.profile_id==profile_id for p in self.list_profiles()): raise ValueError('Unknown profile')
        if value: self.secrets.set('provider/'+profile_id,value)
        else: self.secrets.delete('provider/'+profile_id)

    def status(self):
        return {'active':self._data['active'],'storage':self.secrets.status,'profiles':[{**asdict(p),'credential_configured':bool(self.secrets.get('provider/'+p.profile_id))} for p in self.list_profiles()]}
