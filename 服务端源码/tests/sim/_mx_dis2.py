# -*- coding: utf-8 -*-
"""精确反汇编 mx.exe 指定 VA 区间，带 IAT/字符串注解。
用法: python _mx_dis2.py <exe> <start_va_hex> <end_va_hex>
"""
import sys
import pefile
from capstone import *
from capstone import CS_OP_MEM, CS_OP_IMM

def main():
    exe = sys.argv[1]
    start_va = int(sys.argv[2], 16)
    end_va = int(sys.argv[3], 16)

    raw = open(exe, 'rb').read()
    pe = pefile.PE(exe, fast_load=True)
    ib = pe.OPTIONAL_HEADER.ImageBase

    secs = []
    for s in pe.sections:
        secs.append((ib + s.VirtualAddress, s.VirtualAddress, s.PointerToRawData,
                     s.Misc_VirtualSize, s.SizeOfRawData, s.Name.rstrip(b'\0').decode('latin1')))

    def va2off(va):
        for start, rva, praw, vsz, rsz, name in secs:
            if start <= va < start + max(vsz, rsz):
                return praw + (va - start)
        return None

    def rd_cstr(va, maxlen=120):
        off = va2off(va)
        if off is None:
            return None
        chunk = raw[off:off + maxlen]
        z = chunk.find(b'\0')
        if z >= 0:
            chunk = chunk[:z]
        if not chunk:
            return None
        try:
            return chunk.decode('gbk')
        except UnicodeDecodeError:
            try:
                return chunk.decode('latin1')
            except Exception:
                return repr(chunk)

    pe.parse_data_directories()
    imports = {}
    try:
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            dll = entry.dll.decode() if entry.dll else '?'
            for imp in entry.imports:
                if imp.address:
                    nm = imp.name.decode() if imp.name else 'ord%d' % imp.ordinal
                    imports[imp.address] = '%s!%s' % (dll, nm)
    except Exception as e:
        print('!! import parse: %r' % e)

    off = va2off(start_va)
    if off is None:
        print('!! start_va not mappable')
        return
    data = raw[off:off + (end_va - start_va)]

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True

    for insn in md.disasm(data, start_va):
        cmt = ''
        # IAT / mem refs
        for op in insn.operands:
            if op.type == CS_OP_MEM:
                disp = op.mem.disp
                if disp in imports:
                    cmt += '  ; %s' % imports[disp]
                else:
                    st = rd_cstr(disp, 100)
                    if st and all(32 <= ord(c) < 127 or ord(c) > 127 for c in st[:1] or 'x'):
                        if len(st) > 3 and any(ch.isalpha() for ch in st):
                            cmt += '  ; "%s"' % st[:60]
            elif op.type == CS_OP_IMM:
                imm = op.imm
                if 0x430000 <= imm < 0x440000:
                    st = rd_cstr(imm, 100)
                    if st:
                        cmt += '  ; "%s"' % st[:70]
                elif imm in imports:
                    cmt += '  ; %s' % imports[imm]
        print('0x%06X: %-7s %s%s' % (insn.address, insn.mnemonic, insn.op_str, cmt))

if __name__ == '__main__':
    main()
