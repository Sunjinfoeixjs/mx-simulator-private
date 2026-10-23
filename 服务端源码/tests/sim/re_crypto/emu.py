# -*- coding: utf-8 -*-
"""Emulate mxmain.dll 0x43cc50 (VMProtect-virtualized keystream) under Unicorn.

The original DLL has no relocations -> it is designed to load at 0x400000.
We map the image there exactly as the Windows loader would (sections at
their RVA, zero-filled to VirtualSize), so all absolute VAs inside the
code are valid.

We then call:
   FUN_00430a60(CNet* this, uint32 seed)     -> SetSeed
   FUN_0043cc50(Ctx* ctx, void* buf, uint len) -> in-place stream transform

For an all-zero buffer the output IS the keystream.
"""
import os
import struct
import sys

import pefile
from unicorn import (Uc, UcError, UC_ARCH_X86, UC_MODE_32,
                     UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_READ,
                     UC_HOOK_MEM_WRITE)
from unicorn.x86_const import *

DLL = r'D:\mxsj2013\client\mxmain.dll.orig_backup'
IMAGE_BASE = 0x400000

# A few scratch mappings well away from the image.
STACK_BASE = 0x00100000
STACK_SIZE = 0x00100000
HEAP_BASE = 0x01000000
HEAP_SIZE = 0x01000000
OUT_BASE = 0x02000000
OUT_SIZE = 0x00100000


class Dll:
    def __init__(self, path=DLL, trace=False):
        self.path = path
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        assert self.base == IMAGE_BASE, 'expected ImageBase 0x400000'
        self.image = self.pe.get_memory_mapped_image(ImageBase=self.base)
        self.size = len(self.image)
        self.trace = trace
        self.log = []
        self.uc = None
        self._setup()

    # ------------------------------------------------------------------ setup
    def _setup(self):
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc = uc
        # map the image (aligned down/up to 4K)
        img_start = self.base
        img_end = self.base + self.size
        uc.mem_map(img_start & ~0xFFF, ((img_end - img_start) + 0xFFF) & ~0xFFF)
        uc.mem_write(img_start, self.image)
        # stack
        uc.mem_map(STACK_BASE, STACK_SIZE)
        self.esp_top = STACK_BASE + STACK_SIZE - 0x1000
        # heap (CNet object + buffers)
        uc.mem_map(HEAP_BASE, HEAP_SIZE)
        uc.mem_map(OUT_BASE, OUT_SIZE) if False else None
        self.heap_ptr = HEAP_BASE
        self.aux = []
        uc.reg_write(UC_X86_REG_ESP, self.esp_top)
        uc.reg_write(UC_X86_REG_EBP, self.esp_top)
        uc.reg_write(UC_X86_REG_EFLAGS, 0x202)
        if self.trace:
            uc.hook_add(UC_HOOK_CODE, self._hook_code)
            uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._hook_unmapped)

    def _hook_code(self, uc, address, size, user_data):
        if len(self.log) > 200000:
            uc.emu_stop()
            return
        try:
            code = uc.mem_read(address, size)
            self.log.append((address, bytes(code)))
        except Exception:
            self.log.append((address, b''))

    def _hook_unmapped(self, uc, access, address, size, value, user_data):
        # Optionally grow the heap-like region on demand.
        print('UNMAPPED ACCESS: type=%d addr=%08X size=%d pc=%08X'
              % (access, address, size, uc.reg_read(UC_X86_REG_EIP)))
        return False

    # ------------------------------------------------------------------ heap
    def alloc(self, size, align=16, fill=None, data=None):
        p = (self.heap_ptr + align - 1) & ~(align - 1)
        end = p + size
        assert end < HEAP_BASE + HEAP_SIZE, 'heap exhausted'
        if data is not None:
            self.uc.mem_write(p, data)
        elif fill is not None:
            self.uc.mem_write(p, bytes([fill]) * size)
        self.heap_ptr = end
        return p

    def read(self, addr, size):
        return bytes(self.uc.mem_read(addr, size))

    # ------------------------------------------------------------------ call
    def call(self, va, args, this=0, timeout=2000000, push_ret=True,
             ret_target=0x0BADF00D):
        """__thiscall: this in ECX, args pushed right-to-left, callee cleans."""
        uc = self.uc
        for a in reversed(args):
            uc.mem_write(self.esp_top - 4, struct.pack('<I', a & 0xFFFFFFFF))
            self.esp_top -= 4
        # return address slot (so that 'ret' pops something sane)
        uc.mem_write(self.esp_top - 4, struct.pack('<I', ret_target))
        self.esp_top -= 4
        uc.reg_write(UC_X86_REG_ECX, this & 0xFFFFFFFF)
        uc.reg_write(UC_X86_REG_ESP, self.esp_top)
        uc.reg_write(UC_X86_REG_EBP, self.esp_top)
        self.esp_base = self.esp_top
        self.calls_done = 0
        try:
            uc.emu_start(va, ret_target, count=timeout)
        except UcError as e:
            print('UcError: %s at EIP=%08X' % (e, uc.reg_read(UC_X86_REG_EIP)))
            raise
        # restore stack (callee may have cleaned; we just reset)
        self.esp_top = self.esp_base


if __name__ == '__main__':
    d = Dll()
    print('image base=%08X size=%X' % (d.base, d.size))
    # quick smoke: call seed expansion 0x430790 directly (not virtualized)
    out = d.alloc(16, 16, fill=0)
    ctxA = d.alloc(4, 16, fill=0)
    ctxC = d.alloc(4, 16, fill=0)
    # FUN_00430790(seed, &A, &C, &B, &D)  __cdecl?  it does 'ret' with no cleanup
    B = d.alloc(4, 16, fill=0)
    D = d.alloc(4, 16, fill=0)
    # stack order: push param5(D), param4(B), param3(&C), param2(&A), param1(seed)
    args = [0x12345678, ctxA, ctxC, B, D]
    uc = d.uc
    sp = d.esp_top
    for a in reversed(args):
        sp -= 4
        uc.mem_write(sp, struct.pack('<I', a))
    uc.mem_write(sp - 4, struct.pack('<I', 0x0BADF00D))
    sp -= 4
    uc.reg_write(UC_X86_REG_ESP, sp)
    uc.emu_start(0x430790, 0x0BADF00D)
    print('A=%08X B=%08X C=%08X D=%08X' % (
        struct.unpack('<I', d.read(ctxA, 4))[0],
        struct.unpack('<I', d.read(B, 4))[0],
        struct.unpack('<I', d.read(ctxC, 4))[0],
        struct.unpack('<I', d.read(D, 4))[0]))
