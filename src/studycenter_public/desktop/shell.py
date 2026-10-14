"""Windows WebView2 with an owned tray and browser fallback."""
import ctypes
from pathlib import Path
import threading
import webbrowser

def run_shell(runtime,app,start_hidden=False):
    import pystray
    from PIL import Image,ImageDraw
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('StudyCenter.Public')
    icon_image=Image.new('RGBA',(64,64),'#16655b')
    drawing=ImageDraw.Draw(icon_image)
    drawing.rounded_rectangle((12,10,52,54),radius=4,fill='#fafbfc')
    for y in (23,32,41):drawing.line((20,y,44,y),fill='#16655b',width=3)
    state={'window':None,'stopping':False,'webview':False}
    def show(icon=None,item=None):
        if state['window']:
            try:state['window'].show();state['window'].restore();return
            except Exception:pass
        webbrowser.open(runtime.url)
    def quit_app(icon=None,item=None):
        if state['stopping']:return
        state['stopping']=True;runtime.stop()
        if state['window']:
            try:state['window'].destroy()
            except Exception:pass
        tray.stop()
    tray=pystray.Icon('StudyCenter.Public',icon_image,'StudyCenter Public',menu=pystray.Menu(
        pystray.MenuItem('StudyCenter öffnen',show,default=True),
        pystray.MenuItem('Vollständig beenden',quit_app)))
    from ..platform import notifications
    notifications.desktop_notify=lambda item,endpoint:tray.notify(str(item.get('title','Erinnerung')),'StudyCenter Public')
    app.state.stop=quit_app
    threading.Thread(target=tray.run,daemon=True).start()
    try:
        import webview
        window=webview.create_window('StudyCenter Public',runtime.url,width=1220,height=840,min_size=(760,550),hidden=start_hidden)
        state['window']=window
        def closing():
            if app.state.settings.get('background',False) and not state['stopping']:
                window.hide();tray.notify('StudyCenter läuft im Tray. „Vollständig beenden“ beendet diese Instanz.','StudyCenter Public');return False
            quit_app();return True
        window.events.closing+=closing
        state['webview']=True
        webview.start(gui='edgechromium',private_mode=True)
    except Exception:
        state['window']=None;state['webview']=False
        # The common core stays usable when WebView2 cannot be created.
        if not start_hidden:webbrowser.open(runtime.url)
        tray.notify('WebView2 nicht verfügbar. StudyCenter wurde im Standardbrowser geöffnet.','StudyCenter Public')
        runtime.thread.join()
    finally:
        if not app.state.settings.get('background',False) or state['stopping']:quit_app()
        elif runtime.thread.is_alive():runtime.thread.join()
