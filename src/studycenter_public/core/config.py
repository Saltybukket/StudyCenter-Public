import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = Path(__file__).resolve().parent
VIENNA = ZoneInfo("Europe/Vienna")


def local_profile_path(relative, *, markers=()):
    raise RuntimeError('Public benötigt ausdrücklich instanzgebundene Pfade.')


@dataclass
class Settings:
    data_dir: Path
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    teachcenter_url: str = "https://tc.tugraz.at/main/"
    ocr_lang: str = "eng"
    tesseract: str | None = None
    model_provider: str = "chat-completions"
    oauth_model: str = ""
    oauth_probe_passed: bool = False
    stt_engine: str = 'whisper'
    stt_model: str = 'small'
    stt_device: str = 'auto'
    chatgpt: object = field(default=None, repr=False)

    def __post_init__(self):
        self.data_dir = self.data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "documents").mkdir(exist_ok=True)
        path = self.data_dir / "settings.json"
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.model_provider = saved.get("model_provider", self.model_provider)
            self.oauth_model = saved.get("oauth_model", "")
            self.oauth_probe_passed = saved.get("oauth_probe_passed", False)
            self.stt_engine = saved.get('stt_engine', self.stt_engine)
            self.stt_model = saved.get('stt_model', self.stt_model)
            self.stt_device = saved.get('stt_device', self.stt_device)
        self.validate_speech(self.stt_engine, self.stt_model, self.stt_device)

    @staticmethod
    def validate_speech(engine, model, device):
        if engine not in ('whisper', 'parakeet') or model not in ('base', 'small') or device not in ('auto', 'cpu', 'cuda'):
            raise ValueError('Ungültige Spracherkennung: Engine whisper/parakeet, Modell base/small und Gerät auto/cpu/cuda wählen.')

    @property
    def model_ready(self):
        if self.model_provider == "chatgpt":
            try:
                status = self.chatgpt.status() if self.chatgpt else {}
            except (RuntimeError, OSError, ValueError):
                return False
            return bool(status.get("connected") and status.get("plan_enabled") and self.oauth_model and self.oauth_probe_passed)
        return bool(self.llm_base_url and self.llm_model)

    def save(self):
        self.validate_speech(self.stt_engine, self.stt_model, self.stt_device)
        path = self.data_dir / "settings.json"
        temporary = path.with_suffix(".tmp")
        saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        # Retired scheduling switches remain inert and are removed on the next save.
        for legacy in ('auto_sync','daily_time'):saved.pop(legacy,None)
        saved.update({"model_provider": self.model_provider, "oauth_model": self.oauth_model,
                      "oauth_probe_passed": self.oauth_probe_passed,
                      'stt_engine': self.stt_engine, 'stt_model': self.stt_model, 'stt_device': self.stt_device})
        temporary.write_text(json.dumps(saved), encoding="utf-8")
        temporary.replace(path)


def load_settings():
    raise RuntimeError("Public benötigt ausdrücklich isolierte Einstellungen.")

