# -*- coding: utf-8 -*-
"""单实例: 已有实例在运行时, 第二个进程通知它激活窗口, 然后自身退出。

机制为 QLocalServer/QLocalSocket: 不依赖窗口标题, 且跨平台。
抢前台由第二个进程负责 —— SetForegroundWindow 只对当前前台进程或其启动的进程放行, 主实例
在后台自行 activateWindow() 常被系统拒绝; 第二个进程刚由用户启动, 更可能持有前台权限, 因此
由它先 AllowSetForegroundWindow(ASFW_ANY) 放开权限, 再按主实例回传的窗口句柄调用
SetForegroundWindow。主实例的 activate() 无论如何都会执行, 句柄未回传时窗口仍会被还原并抬起。

调用顺序约束: 必须在构造 AppState 之前调用 claim() —— AppState 构造时会读取设置并清理预览
残留, 而两个进程各写一份 settings.json、后写的覆盖先写的正是单实例要防止的情况。
"""
import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# 第二个进程的两次等待(ms): 连接 / 等主实例回句柄。取值较短 —— 它通知完即退出, 主实例忙时
# 也不应让用户久等; 等不到就照常退出(主实例仍会自行激活一次)。
_CONNECT_MS = 400
_REPLY_MS = 400

# 主实例侧: 连接需要保留引用, 否则 Python 回收对象时套接字被销毁。
# 不主动回收也不调用 deleteLater: 断开与读取可能同批到达, 当场回收会连带尚未投递的事件;
# 对象数等于用户重复启动的次数, 量级可忽略(应用退出时一并释放)。
_RETIRED = set()

def _user():
    """管道名中的用户名片段: 同一台机器多用户登录时, 各自实例互不干扰。"""
    raw = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in raw) or "default"


def server_name():
    """管道名。`XSY_IPC_NAME` 可覆盖, 供工具/自检使用(避免与用户正在运行的实例冲突)。"""
    return os.environ.get("XSY_IPC_NAME") or f"ShuiYin-QT6-{_user()}"


def claim(name):
    """检测已有实例: 返回 (本进程是否继续运行, server 或 None)。

    已有实例 -> (False, None), 且已通知其激活窗口; 无实例 -> 占位成功返回 (True, server),
    由调用方在建窗后交给 `wire()`。

    占位失败或异常时一律返回 (True, None): 单实例保护不应阻断程序启动(fail-open)。
    """
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(_CONNECT_MS):
        QLocalServer.removeServer(name)      # 清理上次异常退出留下的残留(Unix 域套接字文件)
        srv = QLocalServer()
        srv.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)   # 只允许本用户连接
        if not srv.listen(name):
            logging.warning(f"[单实例] 占位失败，本次不做单实例保护：{srv.errorString()}")
            return True, None
        logging.info(f"[单实例] 已占位：{name}")
        return True, srv

    # 连接成功 = 已有实例在运行: 先放开前台权限, 再发送通知
    _allow_foreground()
    sock.write(b"raise\n")
    sock.flush()
    hwnd = 0
    if sock.waitForReadyRead(_REPLY_MS):     # 主实例回传的窗口句柄(十进制)
        try:
            hwnd = int(bytes(sock.readAll()).strip() or b"0")
        except ValueError:
            hwnd = 0
    sock.disconnectFromServer()
    if hwnd:
        _set_foreground(hwnd)
    logging.info("[单实例] 已有实例在运行，已激活其窗口"
                 f"（窗口句柄{'已回传' if hwnd else '未回传'}）")
    return False, None


def wire(server, win):
    """把 server 接上主窗口: 之后再有实例启动, 即把该窗口提到前台。建窗后调用。"""
    if server is None:
        return
    server.newConnection.connect(lambda: _accept(server, win))


def _accept(server, win):
    while server.hasPendingConnections():
        sock = server.nextPendingConnection()
        if sock is None:
            break
        _RETIRED.add(sock)
        once = {"done": False}               # 同一条连接只处理一次(两个信号都可能到达)
        sock.readyRead.connect(lambda s=sock: _answer(s, win, once))
        sock.disconnected.connect(lambda s=sock: _answer(s, win, once))
        if sock.bytesAvailable():            # 数据可能在信号接上之前已到达
            _answer(sock, win, once)


def _answer(sock, win, once):
    """收到通知: 把窗口提到前台, 并回传窗口句柄(第二个进程据此抢前台)。

    不能只等 readyRead: 第二个进程常常发完通知即退出, 主实例处理连接时它已不在 —— 该时序下
    readyRead 不触发、缓冲字节也读不到(断开与读取同批到达, `bytesAvailable()` 为 0)。
    消息内容不重要(只有一种语义: 激活窗口), 因此连接一有动静即视为有效。
    """
    if once["done"]:
        return
    once["done"] = True
    try:
        sock.readAll()
    except Exception:                        # noqa: BLE001 - 连接已断开时读取会报错, 可忽略
        pass
    activate(win)
    try:
        sock.write(str(int(win.winId())).encode())
        sock.flush()
    except Exception as e:                   # noqa: BLE001 - 回传失败时本实例已自行激活
        logging.info(f"[单实例] 回传窗口句柄失败（本实例已自行激活）：{e}")


def activate(win):
    """把主窗口提到前台: 最小化时先还原, 被隐藏时也拉回。

    清除 WindowMinimized 位 + show + raise_ + activateWindow 即可生效; 不要再补 showNormal()
    —— 它等于重新 show 一次, 会丢失刚获得的激活状态。
    """
    try:
        if win.isMinimized():
            win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized)
        win.show()
        win.raise_()
        win.activateWindow()
    except Exception as e:                   # noqa: BLE001 - 提不上来不应影响主流程
        logging.warning(f"[单实例] 激活窗口失败：{e}")


def _allow_foreground():
    """Windows: 放开"谁可以设置前台窗口"的限制。

    第二个进程刚由用户启动、更有前台资格, 但它手上没有主窗口, 因此由它放权, 主实例随后的
    activateWindow() 才能把窗口提到前面。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user32.AllowSetForegroundWindow.restype = wintypes.BOOL
        user32.AllowSetForegroundWindow(0xFFFFFFFF)      # ASFW_ANY
    except Exception as e:                   # noqa: BLE001
        logging.info(f"[单实例] 放开前台权限失败（不影响本实例自行激活）：{e}")


def _set_foreground(hwnd):
    """Windows: 直接调用 SetForegroundWindow(第二个进程持有前台权限时才有效)。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception as e:                   # noqa: BLE001
        logging.info(f"[单实例] 设置前台窗口失败：{e}")
