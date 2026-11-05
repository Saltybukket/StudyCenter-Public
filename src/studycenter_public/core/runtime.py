"""Process wait used by the Public-owned desktop lifecycle."""
import os
import sys


def wait_for_process_exit(pid,timeout=60):
    """Wait on a Windows handle, never terminate a process or delete its lock."""
    if sys.platform!='win32':raise ValueError('Neustart ist nur unter Windows verfügbar.')
    if not isinstance(pid,int) or pid<=0 or pid==os.getpid():raise ValueError('Ungültige Vorgänger-Prozess-ID.')
    if not 0<timeout<=60:raise ValueError('Wartezeit muss zwischen 0 und 60 Sekunden liegen.')
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE only.
    if not handle:
        error=ctypes.get_last_error()
        if error==87:return  # The parent already exited before the child opened it.
        raise ctypes.WinError(error)
    try:
        result=kernel.WaitForSingleObject(handle,int(timeout*1000))
        if result==258:raise TimeoutError('Vorherige CampusBoard-Instanz läuft nach 60 Sekunden weiterhin; kein Doppelstart.')
        if result!=0:raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)

