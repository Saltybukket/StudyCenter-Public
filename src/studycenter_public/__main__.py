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
    parser.add_argument('--after-process', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--after-pidfd', type=int, help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.port<0 or args.port>65535:parser.error('Port muss zwischen 0 und 65535 liegen')
    if args.after_pidfd is not None:
        if sys.platform != 'linux' or args.after_pidfd < 0:
            parser.error('Ungueltiger Neustart-Handle')
        import os, select
        try:
            ready, _, _ = select.select([args.after_pidfd], [], [], 60)
            if not ready: raise TimeoutError('Vorherige Instanz laeuft noch; kein Doppelstart.')
        finally:
            os.close(args.after_pidfd)
    if args.after_process is not None:
        from .core.runtime import wait_for_process_exit
        wait_for_process_exit(args.after_process)
    paths=AppPaths.create(args.data_dir)
    if args.quit:
        print('Shutdown requested' if request_stop(paths) else 'Instance not running')
        return 0
    if args.self_test:
        runtime=Runtime(paths,args.port)
        runtime.acquire()
        try:
            from .product import create_app
            app=create_app(paths)
            assert any(getattr(route, 'path', None) == '/board' for route in app.routes)
            print(json.dumps({'core':True,'instance_id':paths.instance_id,'platform':sys.platform}))
        finally:
            runtime.close()
        return 0
    runtime=Runtime(paths,args.port)
    try:
        endpoint=runtime.acquire()
    except AlreadyRunning:
        info=json.loads((paths.data/'runtime.json').read_text(encoding='utf-8'))
        if not args.no_browser:open_system_browser(info['url'])
        print(info['url']);return 0
    try:
        from .product import create_app
        app=create_app(paths)
        app.state.stop_callback=runtime.stop
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
