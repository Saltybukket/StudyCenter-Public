"""Single owned process, reserved loopback socket and cooperative shutdown."""
import json
from pathlib import Path
import socket
import threading
import time
import os
import portalocker
from .paths import atomic_json, owned_path

class AlreadyRunning(RuntimeError): pass

class Runtime:
    def __init__(self,paths,preferred_port=8767):
        self.paths=paths; self.preferred_port=preferred_port
        self.server=None;self.sock=None;self.lock=None;self.url=None
        self.quit_file=paths.data/'shutdown.request'
        self.endpoint=paths.data/'runtime.json'

    def acquire(self):
        for path in (self.paths.data/'instance.lock',self.quit_file,self.endpoint):owned_path(path,self.paths.data)
        self.lock=portalocker.Lock(str(self.paths.data/'instance.lock'),timeout=0)
        try:self.lock.acquire()
        except portalocker.exceptions.LockException:raise AlreadyRunning('Diese Public-Instanz läuft bereits.') from None
        self.quit_file.unlink(missing_ok=True)
        self.sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        if os.name=='nt': self.sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:self.sock.bind(('127.0.0.1',self.preferred_port))
        except OSError:self.sock.bind(('127.0.0.1',0))
        self.sock.listen(128)
        self.url=f'http://127.0.0.1:{self.sock.getsockname()[1]}'
        atomic_json(self.endpoint,{'url':self.url,'pid':os.getpid(),'instance_id':self.paths.instance_id})
        return self.url

    def start(self,app):
        import uvicorn
        app.state.endpoint=self.url
        self.app=app
        self.server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False,log_config=None))
        self.thread=threading.Thread(target=self.server.run,kwargs={'sockets':[self.sock]},daemon=False)
        self.thread.start()
        for _ in range(200):
            if self.server.started:break
            if not self.thread.is_alive():raise RuntimeError('Lokaler Serverstart fehlgeschlagen')
            time.sleep(.05)
        else:raise RuntimeError('Lokaler Serverstart hat zu lange gedauert')
        def watch():
            while self.thread.is_alive():
                if self.quit_file.exists():self.stop();break
                time.sleep(.3)
        threading.Thread(target=watch,daemon=True).start()

    def stop(self):
        if self.server:self.server.should_exit=True

    def close(self):
        purged=False
        self.stop()
        if getattr(self,'thread',None): self.thread.join(timeout=20)
        if getattr(self,'thread',None) and self.thread.is_alive():
            raise RuntimeError('Public-Prozess beendet sich noch; Daten werden nicht entfernt und die Instanzsperre bleibt erhalten.')
        if getattr(getattr(self,'app',None),'state',None) and getattr(self.app.state,'purge_requested',False):
            from .cleanup import purge_public_data
            purge_public_data(self.paths,self.app.state.secrets,confirmed=True)
            purged=True
        if self.sock:self.sock.close()
        self.endpoint.unlink(missing_ok=True);self.quit_file.unlink(missing_ok=True)
        if self.lock:self.lock.release()
        if purged:
            (self.paths.data/'instance.lock').unlink(missing_ok=True)
            if self.paths.data.exists() and not any(self.paths.data.iterdir()):self.paths.data.rmdir()

def request_stop(paths):
    owned_path(paths.data/'runtime.json',paths.data)
    owned_path(paths.data/'shutdown.request',paths.data)
    if not (paths.data/'runtime.json').exists():return False
    # Publish only a closed file. The watcher may stop immediately when it
    # appears; Windows cannot delete a file still held by its signal writer.
    atomic_json(paths.data/'shutdown.request', {'request': 'stop'})
    return True
