"""Development and frozen launcher use the same in-process backend."""
import argparse
import json
import multiprocessing
import sys
from .desktop.browser import open_system_browser
from .platform.paths import AppPaths
from .platform.runtime import Runtime,AlreadyRunning,request_stop

def main(argv=None):
    multiprocessing.freeze_support()
    parser=argparse.ArgumentParser(prog='StudyCenter-Public')
    parser.add_argument('--data-dir');parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--no-browser',action='store_true');parser.add_argument('--browser',action='store_true')
    parser.add_argument('--background',action='store_true',help='Windows: im Tray ohne sichtbares Startfenster beginnen')
    parser.add_argument('--quit',action='store_true');parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args(argv)
    if args.port<0 or args.port>65535:parser.error('Port muss zwischen 0 und 65535 liegen')
    paths=AppPaths.create(args.data_dir)
    if args.quit:
        print('Shutdown requested' if request_stop(paths) else 'Instance not running')
        return 0
    if args.self_test:
        from .storage.db import Database
        from .services.actions import ActionService
        from uuid import uuid4
        db=Database(paths.database)
        if db.items():raise RuntimeError('Self-test needs an empty dedicated instance')
        actions=ActionService(db)
        receipt=actions.execute([{'type':'create_note','title':'Synthetic installation check'}],str(uuid4()))
        assert len(db.items())==1
        print(json.dumps({'core':True,'instance_id':paths.instance_id,'receipt':receipt,'platform':sys.platform}))
        return 0
    runtime=Runtime(paths,args.port)
    try:
        endpoint=runtime.acquire()
    except AlreadyRunning:
        info=json.loads((paths.data/'runtime.json').read_text(encoding='utf-8'))
        if not args.no_browser:open_system_browser(info['url'])
        print(info['url']);return 0
    try:
        from .web import create_app
        app=create_app(paths)
        app.state.stop=runtime.stop
        runtime.start(app)
        print(endpoint,flush=True)
        if sys.platform=='win32' and not args.no_browser and not args.browser:
            from .desktop.shell import run_shell
            run_shell(runtime,app,start_hidden=args.background)
        else:
            if not args.no_browser:open_system_browser(endpoint)
            runtime.thread.join()
    except KeyboardInterrupt:runtime.stop()
    finally:runtime.close()
    return 0

if __name__=='__main__':sys.exit(main())
