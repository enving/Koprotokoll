"""Optionaler Zugriff auf das klassische Outlook; COM bleibt im aufrufenden Thread."""

from contextlib import contextmanager
import importlib.util
import sys


def outlook_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        if importlib.util.find_spec("win32com.client") is None:
            return False
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID"):
            return True
    except (ImportError, ValueError, OSError):
        return False


@contextmanager
def outlook_session():
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        yield win32com.client.Dispatch("Outlook.Application")
    finally:
        pythoncom.CoUninitialize()
