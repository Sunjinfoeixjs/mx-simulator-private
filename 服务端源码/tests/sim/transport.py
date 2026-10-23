# -*- coding: utf-8 -*-
"""单机架构 · 传输层抽象（把「客户端 + 模拟服」缝合成「单机游戏」的第一块砖）

三种连接实现，全部鸭子类型兼容 `m4_server` 对 conn 的既有用法
（`sendall` / `recv` / `settimeout` / `close`，见 `recv_exact` / `recv_c2s`）：

  LoopbackListener  127.0.0.1 回环监听器；`port=0` 由内核挑选空闲端口。
                    真实客户端（mx.exe）走这条，仍是 TCP，但**只绑回环、
                    端口随机**，不再有「3134 被占用 → 连不上」的单机故障。
  InMemoryConn      纯进程内双向管道，零 socket。用于无客户端自测 / CI：
                    同一个 M4Server 内核可以直接喂 C2S、收 S2C。
  RejectingConn     占位拒绝连接（单连接保护语义的显式表达）。
  CryptoConn        透明加解密包装（原版 mxmain.dll 开启加密时用）：包在原始
                    socket 之外，`sendall` 加密（走客户端 RX 方向密钥流）、
                    `recv` 解密（走客户端 TX 方向密钥流），其余方法透传。

设计约束（有意为之）：
  · **不引入新协议**。帧格式仍由 `m4_server.make_s2c` / `recv_c2s` 定义，
    传输层只负责搬字节（CryptoConn 仅做字节级 XOR，不解析、不改写帧结构）。
  · **不改变单角色单会话语义**。单机游戏天然一人一角色，`M4Server` 的
    实例状态就是唯一会话状态；并发第二个连接由 `M4Server.run()` 拒绝。
  · **加密可选**。`0x43cc50` 已还原（`mx_crypto.RealCipher`），但默认仍走
    明文通道（补丁 DLL）；原版 DLL 客户端需在 `M4Server.handle()` 握手后
    用 `CryptoConn` 包一层（见 `m4_server.handle`）。
"""
import queue
import socket
import threading
import time


class LoopbackListener:
    """127.0.0.1 回环监听器。port=0 → 操作系统挑选空闲端口。"""

    def __init__(self, host='127.0.0.1', port=0, backlog=2):
        self.host = host
        self._requested_port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(backlog)
        self.port = self._sock.getsockname()[1]

    def accept(self):
        """接受一个连接，返回 (conn, addr)。conn 就是标准 socket，可直接交给
        `M4Server.handle()`。"""
        return self._sock.accept()

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _PipeEnd:
    """进程内管道的一端：从 `_q` 读字节，写进的字节进对端的 `_peer_q`。"""

    def __init__(self, q, peer_q):
        self._q = q
        self._peer_q = peer_q
        self._buf = bytearray()
        self._timeout = None
        self._closed = False

    def settimeout(self, t):
        self._timeout = t

    def gettimeout(self):
        return self._timeout

    def recv(self, n):
        # 与 socket.recv 语义一致：返回至多 n 字节；对端关闭返回 b''
        while not self._buf:
            if self._closed:
                return b''
            try:
                chunk = self._q.get(timeout=self._timeout) if self._timeout \
                    else self._q.get()
            except queue.Empty:
                raise socket.timeout()
            if chunk is None:          # 对端 close() 投递的哨兵
                self._closed = True
                return b''
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def sendall(self, data):
        if self._closed:
            raise ConnectionError('连接已关闭')
        self._peer_q.put(bytes(data))

    def close(self):
        self._closed = True
        try:
            self._peer_q.put(None)
        except Exception:  # noqa: BLE001  对端可能已经消失
            pass

    def fileno(self):
        raise OSError('InMemoryConn 没有 fileno（不可用于 select/poll）')


def make_memory_pair():
    """返回 (server_end, client_end)：server_end 交给 M4Server.handle()，
    client_end 由测试代码当作「客户端 socket」收发帧。"""
    q_c2s = queue.Queue()   # 客户端 → 服务端
    q_s2c = queue.Queue()   # 服务端 → 客户端
    server_end = _PipeEnd(q_c2s, q_s2c)
    client_end = _PipeEnd(q_s2c, q_c2s)
    return server_end, client_end


# 兼容旧名：早期设计稿里叫 InMemoryConn
InMemoryConn = _PipeEnd


class RejectingConn:
    """单连接保护用的占位连接：接受后立即关闭，不发任何字节。

    用于把「单机模式拒绝并发第二个客户端」这条语义写成可测的对象。
    """

    def __init__(self, reason='单机模式：同一时刻只允许一个客户端'):
        self.reason = reason

    def settimeout(self, t):
        pass

    def recv(self, n):
        raise ConnectionError(self.reason)

    def sendall(self, data):
        raise ConnectionError(self.reason)

    def close(self):
        pass


class LoopbackBridge:
    """把 LoopbackListener 的一次 accept 适配成可复用的连接提供者。

    目前单机架构下 `M4Server.run()` 自己持有 listener，本类保留给
    「宿主想自己管理 accept 时机」的场景（例如先等客户端真正连上再放行）。
    """

    def __init__(self, listener):
        self.listener = listener
        self._lock = threading.Lock()

    def accept_one(self, timeout=None):
        deadline = None if timeout is None else time.time() + timeout
        self.listener._sock.settimeout(
            max(0.01, deadline - time.time()) if deadline else None)
        try:
            return self.listener.accept()
        except socket.timeout:
            return None
        finally:
            with self._lock:
                self.listener._sock.settimeout(None)


class CryptoConn:
    """透明加解密连接包装：在原始 socket 外做字节级 XOR，其余方法透传。

    方向映射（与 CNet 对象一致，见 mx_crypto.RealCipher）：
      · 客户端**发送**用 TX 流（+0x1090）→ 服务端**收**用 TX 流解密
      · 客户端**接收**用 RX 流（+0x80）   → 服务端**发**用 RX 流加密

    因为 XOR 流密码是字节级、游标线性推进，`recv(n)` 返回多少字节就解多少，
    分片读与连续读结果一致（`recv_exact` 的循环读语义不受影响）。

    用法：
      conn = CryptoConn(raw_socket, mx_crypto.RealCipher(seed))
      conn.sendall(frame)   # frame 会被 RX 流加密后发出
      body = conn.recv(n)   # 收到的密文被 TX 流解密
    """

    def __init__(self, raw, cipher):
        self._raw = raw
        self._cipher = cipher

    def sendall(self, data):
        return self._raw.sendall(self._cipher.crypt_rx(bytes(data)))

    def recv(self, n):
        chunk = self._raw.recv(n)
        if not chunk:
            return b''
        return self._cipher.crypt_tx(chunk)

    def settimeout(self, t):
        return self._raw.settimeout(t)

    def gettimeout(self):
        return getattr(self._raw, 'gettimeout', lambda: None)()

    def close(self):
        return self._raw.close()

    def fileno(self):
        return self._raw.fileno()
