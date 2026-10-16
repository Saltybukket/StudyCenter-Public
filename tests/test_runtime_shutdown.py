"""A stop signal must become visible only after its writer has closed it."""
from pathlib import Path
from types import SimpleNamespace

from studycenter_public.platform.runtime import Runtime, request_stop


def test_shutdown_request_is_published_after_writer_closes(tmp_path, monkeypatch):
    paths = SimpleNamespace(data=tmp_path)
    runtime = Runtime(paths)
    runtime.endpoint.write_text('{}')
    original = Path.write_text

    def watcher_runs_while_writer_is_open(path, text, *args, **kwargs):
        if path == runtime.quit_file:
            with path.open('w', encoding='ascii') as writer:
                writer.write(text)
                # Reproduce the real Windows notification/close race. Windows
                # refuses deletion while write_text still owns this handle.
                runtime.close()
                return len(text)
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', watcher_runs_while_writer_is_open)
    assert request_stop(paths)
    assert runtime.quit_file.is_file()
    runtime.close()
    assert not runtime.quit_file.exists() and not runtime.endpoint.exists()
