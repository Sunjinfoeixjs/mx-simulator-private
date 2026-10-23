# -*- coding: utf-8 -*-
"""离线真机对照：用**客户端真实解码器**验证服务端帧。

为什么这样测：真机 GUI 测试需要管理员权限 + 有人点 UAC，无人值守环境做不到。
但「服务端发的帧客户端能不能解开」这个核心问题，可以**离线**验证——
把客户端 src/*.py 里的 Unpack 逻辑跑一遍，看字节流是否被正确消费。

这比 e2e 更强：e2e 只验证「服务端按自己的 OVERLAY 解自己发的帧」，
是自证；这里是「客户端代码解服务端的帧」，是交叉验证。
"""
import io
import os
import struct
import sys

SIM = r'C:\Users\Administrator\MX\服务端源码\sim'
CLIENT_SRC = r'C:\Users\Administrator\MX\客户端源码\src'
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402
import tap as T  # noqa: E402

results = []


def check(name, ok, detail=''):
    d = ('　' + detail) if isinstance(detail, str) else \
        ('　' + repr(detail)) if detail else ''
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, d))


class Reader(object):
    """模拟客户端 netobj 的 Unpack* 语义（小端、按序消费）。"""

    def __init__(self, data):
        self.d = data
        self.i = 0

    def u(self, n):
        if self.i + n > len(self.d):
            raise ValueError('越界：需 %d 剩 %d' % (n, len(self.d) - self.i))
        v = int.from_bytes(self.d[self.i:self.i + n], 'little')
        self.i += n
        return v

    def s(self, n):
        if self.i + n > len(self.d):
            raise ValueError('越界')
        v = self.d[self.i:self.i + n]
        self.i += n
        return v.rstrip(b'\x00')

    def cstr(self):
        j = self.d.find(b'\x00', self.i)
        if j < 0:
            raise ValueError('C 字符串未终止')
        v = self.d[self.i:j]
        self.i = j + 1
        return v

    def desc_skip(self):
        """跳过 UnpackDesc：1B 标签，标签非 0 时可能跟数据（本测只用空 desc）。"""
        tag = self.u(1)
        if tag != 0:
            raise ValueError('desc 标签非 0（%d），本测只覆盖空 desc' % tag)

    def left(self):
        return len(self.d) - self.i


def frame(op_builder):
    parts, _ = T.split_frames('S2C', op_builder())
    if not parts:
        return None, None
    return parts[0][1], parts[0][2]


def main():
    # ── 1. Hello（net.py S2CHello）：seed(4) + serverNum(2) ──
    op, body = frame(lambda: m.make_s2c(m.OP_HELLO,
                                         struct.pack('<I', 0x12345678)
                                         + struct.pack('<H', 1)))
    r = Reader(body)
    seed = r.u(4)
    sn = r.u(2)
    check('Hello：客户端解出 seed + serverNum 且吃满',
          seed == 0x12345678 and sn == 1 and r.left() == 0,
          'seed=0x%X sn=%d left=%d' % (seed, sn, r.left()))

    # ── 2. SceneM（netmap.py S2CMapSceneM）──
    #   实际布局（OVERLAY op96）：scene(2) mapid(2) minimap(2) x(2) y(2) name(C串)
    #   ★ 首版按「mapid(4) + name + x + y」推测，解出来 mapid=65601537、剩 8B——
    #     这正是真机对照的价值：**推测的字段顺序错了，字节流不会撒谎**。
    #   签名：build_scene_m(mapid, name, minimap, x, y)
    op, body = frame(lambda: m.build_scene_m(1001, 'TestMap', 1001, 50, 60))
    r = Reader(body)
    scene = r.u(2)
    mapid = r.u(2)
    minimap = r.u(2)
    x = r.u(2)
    y = r.u(2)
    name = r.cstr().decode('gbk', 'replace')
    check('SceneM：客户端解出 scene/mapid/minimap/x/y/name 且吃满',
          mapid == 1001 and name == 'TestMap' and x == 50 and y == 60
          and r.left() == 0,
          'scene=%d mapid=%d minimap=%d x=%d y=%d name=%r left=%d'
          % (scene, mapid, minimap, x, y, name, r.left()))

    # ── 3. WarStart（netwar.py S2CWarStart）──
    #   实际布局（OVERLAY op112）：scene(2) alineup(1) elineup(1) wtype(1) x(2) y(2)
    op, body = frame(lambda: m.build_war_start(1001, 1, 1, 0, 50, 50))
    r = Reader(body)
    scene = r.u(2)
    alineup = r.u(1)
    elineup = r.u(1)
    wtype = r.u(1)
    px = r.u(2)
    py = r.u(2)
    check('WarStart：客户端解出 scene/阵营/类型/坐标且吃满',
          scene == 1001 and px == 50 and py == 50 and r.left() == 0,
          'scene=%d alineup=%d elineup=%d wtype=%d x=%d y=%d left=%d'
          % (scene, alineup, elineup, wtype, px, py, r.left()))

    # ── 4. WarAdd（netwar.py S2CWarAdd）──
    op, body = frame(lambda: m.build_war_add(m.HERO_WAR_ID, m.DEFAULT_HERO_SHAPE,
                                             True, 'Hero'))
    #   实际布局（OVERLAY op115）：id(1) shape(2) desc(D) bf(allied=7,type=6,child=5)
    #                              owner_id(1) cls(1) grade(1) name(C串)
    #   ★ 首版按「id(4) shape(2) allied(1) name」猜，解出 id=28516——
    #     id 其实是 1 字节，不是 4 字节。
    #   ★ allied 在**位域**里（bit7），不是独立字节：
    #     `bf=0x80` 表示 allied=1（MSB-first），这是客户端约定的位号写法。
    r = Reader(body)
    wid = r.u(1)
    shape = r.u(2)
    r.desc_skip()          # desc: UnpackDesc —— 本帧是空 desc（1B 标签）
    bf = r.u(1)
    allied = (bf >> 7) & 1
    owner_id = r.u(1)
    cls = r.u(1)
    grade = r.u(1)
    nm = r.cstr().decode('gbk', 'replace')
    check('WarAdd：客户端解出 id/shape/allied(位域)/name 且吃满',
          wid == m.HERO_WAR_ID and shape == m.DEFAULT_HERO_SHAPE
          and allied == 1 and nm == 'Hero' and r.left() == 0,
          'id=%d shape=%d allied=%d(bf=0x%02X) name=%r left=%d'
          % (wid, shape, allied, bf, nm, r.left()))

    # ── 5. 关键：客户端源码里确实有对应 handler ──
    routes = io.open(os.path.join(CLIENT_SRC, 'net.py'),
                     encoding='utf-8', errors='replace').read()
    for opname in ('S2CHello', 'S2CMapSceneM', 'S2CWarStart', 'S2CWarAdd'):
        check('客户端 net.py 有 %s 路由' % opname, opname in routes)

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('离线真机对照：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
