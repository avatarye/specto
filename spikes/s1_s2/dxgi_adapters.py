"""List DXGI adapters with their LUIDs (Windows only, ctypes, no dependencies).

Used to pin Chromium's GPU process to one adapter on multi-GPU machines:
    QTWEBENGINE_CHROMIUM_FLAGS=--use-adapter-luid=<high>,<low>
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


IID_IDXGIFactory1 = GUID(
    0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87)
)


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", wintypes.WCHAR * 128),
        ("VendorId", wintypes.UINT),
        ("DeviceId", wintypes.UINT),
        ("SubSysId", wintypes.UINT),
        ("Revision", wintypes.UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", LUID),
        ("Flags", wintypes.UINT),
    ]


def _vtbl_call(obj: ctypes.c_void_p, index: int, restype, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def adapters() -> list[dict]:
    dxgi = ctypes.WinDLL("dxgi")
    factory = ctypes.c_void_p()
    hr = dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
    if hr != 0:
        raise OSError(f"CreateDXGIFactory1 failed: 0x{hr & 0xFFFFFFFF:08x}")
    enum_adapters1 = _vtbl_call(
        factory, 12, ctypes.c_long, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)
    )
    out = []
    i = 0
    while True:
        adapter = ctypes.c_void_p()
        if enum_adapters1(factory, i, ctypes.byref(adapter)) != 0:
            break
        desc = DXGI_ADAPTER_DESC1()
        get_desc1 = _vtbl_call(adapter, 10, ctypes.c_long, ctypes.POINTER(DXGI_ADAPTER_DESC1))
        get_desc1(adapter, ctypes.byref(desc))
        out.append(
            {
                "index": i,
                "description": desc.Description,
                "vendor_id": f"0x{desc.VendorId:04x}",
                "luid": (desc.AdapterLuid.HighPart, desc.AdapterLuid.LowPart),
                "dedicated_mb": desc.DedicatedVideoMemory // (1024 * 1024),
                "software": bool(desc.Flags & 2),
            }
        )
        _vtbl_call(adapter, 2, ctypes.c_ulong)(adapter)  # Release
        i += 1
    _vtbl_call(factory, 2, ctypes.c_ulong)(factory)
    return out


def main() -> int:
    for a in adapters():
        hi, lo = a["luid"]
        print(
            f"[{a['index']}] {a['description']}  vendor {a['vendor_id']}  "
            f"luid {hi},{lo}  {a['dedicated_mb']} MB{'  (software)' if a['software'] else ''}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
