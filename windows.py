import ctypes
import subprocess
import winreg
from time import sleep

import psutil
import pywintypes
import regex as re
import win32api
import win32con
import win32gui
import win32process
import winxpgui
from PIL import Image

from utils import (
    Client,
    ClientConfig,
    MyFileHandler,
    MyFormatter,
    MyStreamHandler,
    Path,
    Settings,
    Sg,
    box,
    ceil,
    chunks,
    compare,
    copy,
    datetime,
    excepthook,
    floor,
    format_time,
    get_font_name,
    get_menu_pixels,
    get_rotation,
    get_text,
    global_settings,
    hdlr,
    importlib,
    json,
    load_font,
    log,
    logger,
    logging,
    my_emit,
    os,
    requests,
    rfh,
    set_text,
    sys,
    timedelta,
)

BELOW_NORMAL_PRIORITY_CLASS = 0x4000  # Why is this not in win32con??? It has literally all other priority classes..


class NotRespondingError(Exception):
    pass


class SteamExeNotFound(Exception):
    pass


def get_window(title):
    def window_enumeration_handler(hwnd, response):
        if win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            if proc.name() == title:
                response.append((hwnd, proc))

    res = []
    win32gui.EnumWindows(window_enumeration_handler, res)
    return res[0] if res else None


class SteamClient:
    _WIN_REG_SHELL = (winreg.HKEY_CLASSES_ROOT, r"steam\Shell\Open\Command")
    _PROCESS_NAME = "steam.exe"

    def __init__(self):
        self.path = self.find_exe()
        self.cmd = f'"{self.path}" -applaunch 291550 -noeac'

    # yoinked from battlenet extension for gog galaxy
    @staticmethod
    def __search_registry_for_run_cmd(*args):
        try:
            key = winreg.OpenKey(*args)
            for i in range(1024):
                try:
                    _, exe_cmd, _type = winreg.EnumValue(key, i)
                    if exe_cmd and _type == winreg.REG_SZ:
                        return exe_cmd
                except OSError:
                    break
        except FileNotFoundError:
            return None

    def _find_exe_running(self):
        for proc in psutil.process_iter():
            if proc.name() == self._PROCESS_NAME:
                return proc.exe()

    # yoinked from battlenet extension for gog galaxy
    def _find_exe_registry(self):
        shell_reg_value = self.__search_registry_for_run_cmd(*self._WIN_REG_SHELL)
        if shell_reg_value is None:
            return None
        reg = re.compile('"(.*?)"')
        return reg.search(shell_reg_value).groups()[0]

    def find_exe(self):
        res = self._find_exe_running() or self._find_exe_registry()
        if not res:
            raise SteamExeNotFound()
        return res

    def run_brawlhalla(self):
        subprocess.Popen(self.cmd, creationflags=subprocess.DETACHED_PROCESS)


class BrawlhallaProcess:
    def __init__(self, hwnd, proc):
        self.window = hwnd
        self.process = proc

    @classmethod
    def find(cls):
        res = get_window("Brawlhalla.exe") or get_window(
            "BrawlhallaGame.exe"
        )  # support for beta
        if not res:
            return None
        return cls(*res)

    @property
    def responding(self):
        cmd = f'tasklist /FI "PID eq {self.process.pid}" /FI "STATUS eq running"'
        status = subprocess.check_output(cmd, creationflags=subprocess.DETACHED_PROCESS)
        return str(self.process.pid) in str(status)

    def kill(self):
        while self.process.is_running():
            self.process.kill()
            sleep(0.5)

    def get_window_rect(self):
        return win32gui.GetWindowRect(self.window)

    def get_window_size(self):
        left, top, right, bot = self.get_window_rect()
        return right - left, bot - top

    def get_client_size(self):
        left, top, right, bot = win32gui.GetClientRect(self.window)
        return right - left, bot - top

    @property
    def fullscreen(self):
        return self.get_client_size() == self.get_window_size()

    def resize(self):
        window_size = self.get_window_size()
        client_size = self.get_client_size()
        logger.debug("resize", *window_size, *client_size, *Sg.Window.get_screen_size())
        w_border = window_size[0] - client_size[0]
        h_border = window_size[1] - client_size[1]
        while self.get_client_size() != (
            1920,
            1080,
        ):  # getwindowsize or getclientsize or setwindowpos or something else is weird so it sometimes doesnt work first try
            win32gui.SetWindowPos(
                self.window,
                0,
                0,
                0,
                1920 + w_border,
                1080 + h_border,
                win32con.SWP_NOZORDER,
            )

    def move_off_screen(self):
        logger.debug("move_offscreen")
        w, h = Sg.Window.get_screen_size()
        win32gui.SetWindowPos(
            self.window,
            0,
            w * 4,
            h * 4,
            0,
            0,
            win32con.SWP_NOSIZE | win32con.SWP_NOZORDER,
        )

    def make_transparent(self):
        style = win32gui.GetWindowLong(self.window, win32con.GWL_EXSTYLE)
        win32gui.ShowWindow(self.window, win32con.SW_HIDE)
        style |= (
            win32con.WS_EX_COMPOSITED
            | win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
        )
        style &= ~win32con.WS_EX_APPWINDOW
        win32gui.SetWindowLong(self.window, win32con.GWL_EXSTYLE, style)
        sleep(1)
        win32gui.ShowWindow(self.window, win32con.SW_SHOW)
        winxpgui.SetLayeredWindowAttributes(self.window, 0, 0, win32con.LWA_ALPHA)

    def hide(self):
        self.move_off_screen()
        self.make_transparent()

    def make_screenshot(self):
        import win32ui

        w, h = self.get_client_size()

        window_dc = win32gui.GetWindowDC(self.window)
        mfc_dc = win32ui.CreateDCFromHandle(window_dc)
        save_dc = mfc_dc.CreateCompatibleDC()

        save_bit_map = win32ui.CreateBitmap()
        save_bit_map.CreateCompatibleBitmap(mfc_dc, w, h)

        save_dc.SelectObject(save_bit_map)

        ctypes.windll.user32.PrintWindow(self.window, save_dc.GetSafeHdc(), 1)

        bmpinfo = save_bit_map.GetInfo()
        bmpstr = save_bit_map.GetBitmapBits(True)

        im = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        )

        win32gui.DeleteObject(save_bit_map.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(self.window, window_dc)
        return im

    def set_low_priority(self):
        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_INFORMATION, True, self.process.pid
        )
        win32process.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
        win32api.CloseHandle(handle)


class Singleton:
    def __init__(self):
        res = get_window("BHBot.exe")
        if res:
            window, _ = res
            self.set_focus(window)
            sys.exit()

    @staticmethod
    def set_focus(window):
        try:
            win32gui.SetForegroundWindow(window)
        except pywintypes.error:
            pass
