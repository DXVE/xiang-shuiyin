# -*- coding: utf-8 -*-
"""单实例: 已有实例在运行时, 第二个进程通知它激活窗口, 然后自身退出。

`app.py::_ensure_single_instance`)。这里换成 QLocalServer/QLocalSocket, 三个原因:
  - 不依赖窗口标题 —— 标题改一次那套就失效, 而管道名由程序自己定;
  - 能把"由谁去抢前台"安排对, 见下。

**为什么第二个进程也要参与抢前台**: `SetForegroundWindow` 只对"当前前台进程、或被它启动
的进程"放行。主实例待在后台, 自己调 `activateWindow()` 常被 Windows 直接拒绝 —— 用户看到的
就是"点了图标没反应"。第二个进程是用户刚启动的那个, 更可能持有前台权限, 所以两件事一起做:
它先 `AllowSetForegroundWindow(ASFW_ANY)` 放开前台权限(主实例随后的 activateWindow 才生效),
再拿主实例回给它的窗口句柄自己调一次 SetForegroundWindow。主实例这边的 activate() 无论如何
都会执行, 所以即使句柄没拿到, 窗口也会被还原并抬起来。

**第二个进程的副作用必须为零**: 调用方要在构造 AppState 之前调 `claim()` —— AppState 一构造
就读设置、清预览残留, 而"两个进程各写一份 settings.json、后写的那份盖掉先写的"正是单实例
要防的事。
"""
import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# 第二个进程的两次等待(ms): 连接 / 等主实例回句柄。都很短 —— 它通知完就退出,
# 主实例忙(正在渲染)时也不该让用户干等; 等不到就照常退出(主实例仍会自己激活一次)。
_CONNECT_MS = 400
_REPLY_MS = 400

# 主实例侧: 连接要留住引用 —— 不然 Python 一回收对象, 套接字就被毁掉。
# 不主动回收(也没有 deleteLater): 实测"断开"与"读取"会同一批到达, 当场回收会把还没投递的事件
# 一起带走; 而这里的对象数 = 用户重复启动的次数, 量级可忽略(应用退出时一并释放)。
_RETIRED = set()

# 本进程占着的 server(重启自己之前要先放掉, 见 release())
_SERVER = None


def _user():
    """管道名里的用户名片段: 同一台机器多用户登录时, 各自的实例互不干扰。"""
    raw = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in raw) or "default"


def server_name():
    """管道名。`XSY_IPC_NAME` 可覆盖 —— 给工具/自检用(换个名字, 免得跟用户正开着的实例撞车)。"""
    return os.environ.get("XSY_IPC_NAME") or f"ShuiYin-QT6-{_user()}"


def claim(name):
    """检测已有实例: 返回 (本进程是否继续运行, server 或 None)。

    已有实例 -> (False, None), 并且已经通知它激活窗口; 没有 -> 占位成功返回 (True, server),
    由调用方在窗口建好后交给 `wire()`。

    抢不到 / 出错时一律 **(True, None)**: 单实例是锦上添花, 不该挡着程序启动
    """
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(_CONNECT_MS):
        QLocalServer.removeServer(name)      # 清掉上次异常退出留下的残留(Unix 域套接字文件)
        srv = QLocalServer()
        srv.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)   # 只允许本用户连
        if not srv.listen(name):
            logging.warning(f"[单实例] 占位失败，本次不做单实例保护：{srv.errorString()}")
            return True, None
        logging.info(f"[单实例] 已占位：{name}")
        return True, srv

    # 连接成功 = 已有实例在运行: 先放开前台权限, 再发通知
    _allow_foreground()
    sock.write(b"raise\n")
    sock.flush()
    hwnd = 0
    if sock.waitForReadyRead(_REPLY_MS):     # 主实例回的窗口句柄(十进制)
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
    """把 server 接上主窗口: 之后再有实例启动, 就把这个窗口提到前台。窗口建好后调。"""
    global _SERVER
    _SERVER = server
    if server is None:
        return
    server.newConnection.connect(lambda: _accept(server, win))


def release():
    """放掉单实例管道。

    **重启自己之前必须调**: 管道还在我们手里时起新进程, 新进程连得上, 就会被这道闸门当成
    "已有实例在运行"而直接退出 —— 结果是重启后什么都没起来。
    """
    global _SERVER
    if _SERVER is not None:
        _SERVER.close()
        _SERVER = None


def _accept(server, win):
    while server.hasPendingConnections():
        sock = server.nextPendingConnection()
        if sock is None:
            break
        _RETIRED.add(sock)
        once = {"done": False}               # 同一条连接只处理一次(两个信号都可能来)
        sock.readyRead.connect(lambda s=sock: _answer(s, win, once))
        sock.disconnected.connect(lambda s=sock: _answer(s, win, once))
        if sock.bytesAvailable():            # 数据可能在我们接上信号之前就到了
            _answer(sock, win, once)


def _answer(sock, win, once):
    """收到通知: 把窗口提到前台, 再回一个窗口句柄给它(它才抢得动前台, 见模块说明)。

    **不能只等 readyRead**: 第二个进程常常发完通知就立即退出, 主实例轮到处理连接时它已经不
    在了 —— 实测那种时序下 readyRead 根本不触发、缓冲里那几个字节也读不到(断开与读取同一批
    到达, 数据被丢弃, `bytesAvailable()` 是 0)。好在消息内容不重要(只有一种语义: 激活窗口),
    所以连接一有动静就都算数。
    """
    if once["done"]:
        return
    once["done"] = True
    try:
        sock.readAll()
    except Exception:                        # noqa: BLE001 - 连接已经断了时读会报错, 无所谓
        pass
    activate(win)
    try:
        sock.write(str(int(win.winId())).encode())
        sock.flush()
    except Exception as e:                   # noqa: BLE001 - 回不了句柄也已在本地激活过一次
        logging.info(f"[单实例] 回传窗口句柄失败（本实例已自行激活）：{e}")


def activate(win):
    """把主窗口提到前台: 最小化了先还原, 被藏起来也拉回来。

    实测: 清掉 WindowMinimized 位 + show + raise_ + activateWindow 这一套是有效的(窗口还原、
    成为前台); **不要**再补 showNormal() —— 那等于重新 show 一次, 会把刚拿到的激活状态丢掉。
    """
    try:
        if win.isMinimized():
            win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized)
        win.show()
        win.raise_()
        win.activateWindow()
    except Exception as e:                   # noqa: BLE001 - 提不上来不该影响主流程
        logging.warning(f"[单实例] 激活窗口失败：{e}")


def _allow_foreground():
    """Windows: 把"谁可以设置前台窗口"放开给任何进程。

    第二个进程才是用户刚启动的那个(更有前台资格), 但它手上没有主窗口; 于是让它放权,
    主实例随后的 activateWindow() 才能真的把窗口提到前面。
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
    """Windows: 直接 SetForegroundWindow(第二个进程手里有前台权限时才有效)。"""
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
