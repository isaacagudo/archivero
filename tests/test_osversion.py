"""Exact OS versions: parsers, the registry hive reader, and the scan → source description path."""

import os
import plistlib
import struct

from archivero import config, osversion
from archivero.analysis import Analysis
from archivero.db import open_db
from archivero.scanner import scan


def w(root, rel, data=b"x"):
    p = os.path.join(str(root), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def make_hive(values, extra_keys=("Classes", "Clients")):
    """A minimal regf hive with HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion = values."""
    cells = bytearray(b"hbin" + b"\0" * 28)

    def cell(payload):
        off = len(cells)
        size = 4 + len(payload)
        size += (-size) % 8
        cells.extend(struct.pack("<i", -size) + payload + b"\0" * (size - 4 - len(payload)))
        return off

    def nk(name, subs=(), vals=(), list_sig=b"lf"):
        if not subs:
            lst = 0xFFFFFFFF
        elif list_sig == b"li":
            lst = cell(b"li" + struct.pack("<H", len(subs)) + b"".join(struct.pack("<I", s) for s in subs))
        else:
            lst = cell(list_sig + struct.pack("<H", len(subs)) + b"".join(struct.pack("<I4x", s) for s in subs))
        vl = cell(b"".join(struct.pack("<I", v) for v in vals)) if vals else 0xFFFFFFFF
        body = bytearray(0x4C)
        body[0:2] = b"nk"
        struct.pack_into("<H", body, 0x02, 0x20)  # ASCII name
        struct.pack_into("<I", body, 0x14, len(subs))
        struct.pack_into("<I", body, 0x1C, lst)
        struct.pack_into("<I", body, 0x24, len(vals))
        struct.pack_into("<I", body, 0x28, vl)
        struct.pack_into("<H", body, 0x48, len(name))
        return cell(bytes(body) + name.encode("latin-1"))

    def vk(name, value):
        if isinstance(value, int):  # REG_DWORD, stored inline
            return cell(b"vk" + struct.pack("<HIIIHH", len(name), 0x80000004, value, 4, 1, 0) + name.encode())
        raw = (value + "\0").encode("utf-16-le")
        d = cell(raw)
        return cell(b"vk" + struct.pack("<HIIIHH", len(name), len(raw), d, 1, 1, 0) + name.encode())

    cv = nk("CurrentVersion", vals=[vk(k, v) for k, v in values.items()])
    fonts = nk("Fonts")
    wnt = nk("Windows NT", [fonts, cv], list_sig=b"lh")
    win = nk("Windows")
    ms = nk("Microsoft", [win, wnt], list_sig=b"li")
    others = [nk(n) for n in extra_keys]
    root = nk("CMI-CreateHive{199DAFC2}", others + [ms])
    base = bytearray(4096)
    base[:4] = b"regf"
    struct.pack_into("<I", base, 0x24, root)
    return bytes(base + cells)


def test_hive_reader(tmp_path):
    p = tmp_path / "SOFTWARE"
    p.write_bytes(make_hive({"ProductName": "Windows 7 Home Premium", "CSDVersion": "Service Pack 1",
                             "CurrentVersion": "6.1", "CurrentBuildNumber": "7601", "InstallDate": 1262304000}))
    rec = osversion.read_registry_version(str(p))
    assert rec == {"name": "Windows 7 Home Premium Service Pack 1", "os": "Windows", "version": "6.1",
                   "build": "7601"}
    p.write_bytes(make_hive({"ProductName": "Windows 10 Pro", "DisplayVersion": "23H2", "CurrentBuild": "22631"}))
    assert osversion.read_registry_version(str(p))["name"] == "Windows 11 Pro 23H2"
    p.write_bytes(make_hive({"ProductName": "Microsoft Windows XP", "CSDVersion": "Service Pack 3"}))
    assert osversion.read_registry_version(str(p))["name"] == "Windows XP Service Pack 3"
    p.write_bytes(b"not a hive" * 100)
    assert osversion.read_version(str(p), "registry", 1000) is None


def test_text_parsers():
    rec = osversion.parse_os_release(b'NAME="Ubuntu"\nVERSION="10.04.4 LTS, Lucid Lynx"\nID=ubuntu\n'
                                     b'PRETTY_NAME="Ubuntu 10.04.4 LTS"\nVERSION_ID="10.04"\n')
    assert rec == {"name": "Ubuntu 10.04.4 LTS", "os": "Linux/Unix", "id": "ubuntu", "version": "10.04"}
    assert osversion.parse_lsb_release(b"DISTRIB_ID=Ubuntu\nDISTRIB_RELEASE=8.04\n"
                                       b'DISTRIB_DESCRIPTION="Ubuntu 8.04.4 LTS"\n')["name"] == "Ubuntu 8.04.4 LTS"
    assert osversion.parse_release_line(b"CentOS release 5.11 (Final)\n")["version"] == "5.11"
    assert osversion.parse_debian_version(b"6.0.10\n")["name"] == "Debian 6.0.10"
    ini = (b";\r\n[SMS Inventory Identification]\r\nVersion=1.0\r\n\r\n[Product Specification]\r\n"
           b"Product=Windows XP Professional\r\n\r\nVersion=5.0\r\n")
    assert osversion.parse_prodspec(ini) == {"name": "Windows XP Professional", "os": "Windows"}
    pl = {"ProductName": "Mac OS X", "ProductVersion": "10.6.8", "ProductBuildVersion": "10K549"}
    for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
        rec = osversion.parse_system_version_plist(plistlib.dumps(pl, fmt=fmt))
        assert rec == {"name": "Mac OS X 10.6.8 Snow Leopard", "os": "macOS", "version": "10.6.8", "build": "10K549"}
    assert osversion.parse_system_version_plist(plistlib.dumps(
        {"ProductName": "macOS", "ProductVersion": "14.2.1"}))["name"] == "macOS 14.2.1 Sonoma"
    assert osversion.parse_system_version_plist(b"garbage") is None


def test_match_only_at_known_places():
    assert osversion.match(("backups", "pc", "windows", "system32", "config"), "software")[1] == "registry"
    assert osversion.match(("etc",), "os-release") == ("etc/os-release", "os_release")
    assert osversion.match(("docs",), "os-release") is None
    assert osversion.match(("software",), "software") is None


def test_versions_in_source_descriptions(tmp_path):
    r = tmp_path / "nas"
    # Windows 7 drive with its SOFTWARE hive
    win = "Disks/Desktop"
    w(r, win + "/Windows/System32/config/SOFTWARE", make_hive({
        "ProductName": "Windows 7 Professional", "CSDVersion": "Service Pack 1", "CurrentBuildNumber": "7601"}))
    w(r, win + "/Windows/System32/kernel32.dll", b"K" * 100)
    w(r, win + "/Program Files/App/app.exe", b"E" * 100)
    w(r, win + "/Users/Ana/NTUSER.DAT", b"regf")
    w(r, win + "/Users/Ana/Documents/cv.doc", b"D")
    w(r, win + "/pagefile.sys", b"p")
    # Ubuntu root
    lx = "Disks/server"
    for d in ("bin/ls", "usr/bin/env", "var/log/syslog", "lib/libc.so", "boot/vmlinuz"):
        w(r, lx + "/" + d)
    w(r, lx + "/etc/os-release", b'PRETTY_NAME="Ubuntu 12.04.5 LTS"\nVERSION_ID="12.04"\n')
    w(r, lx + "/etc/lsb-release", b'DISTRIB_DESCRIPTION="should lose to os-release"\n')
    # Mac root
    mac = "Disks/iMac"
    w(r, mac + "/System/Library/CoreServices/SystemVersion.plist", plistlib.dumps(
        {"ProductName": "Mac OS X", "ProductVersion": "10.9.5", "ProductBuildVersion": "13F34"}))
    w(r, mac + "/Applications/Safari.app/Contents/Info.plist", b"p")
    w(r, mac + "/private/var/log/system.log", b"l")
    w(r, mac + "/Users/ana/Documents/a.txt", b"a")
    w(r, mac + "/Library/Fonts/x.ttf", b"f")
    # An os-release that is not where an OS keeps it: ignored
    w(r, "Notes/os-release", b'PRETTY_NAME="Nope 1.0"\n')

    cfg = config.load()
    db = str(tmp_path / "inv.db")
    con = open_db(db)
    scan(con, str(r), cfg)
    an = Analysis(con, cfg)
    srcs = {os.path.relpath(an.source_name(s), str(r)): s for s in an.layout.sources.values()}
    assert "Windows 7 Professional Service Pack 1, build 7601" in srcs["Disks/Desktop"].describe()
    assert "Ubuntu 12.04.5 LTS" in srcs["Disks/server"].describe()
    assert "Mac OS X 10.9.5 Mavericks, build 13F34" in srcs["Disks/iMac"].describe()
    assert any(e.startswith("version from etc/os-release") for e in srcs["Disks/server"].evidence())
    assert not any("Nope" in s.describe() for s in srcs.values())

    # A database scanned before this feature gets the versions on the next (resumed) scan
    con.execute("UPDATE files SET extra=NULL")
    con.commit()
    assert not any(s.os_version for s in Analysis(con, cfg).layout.sources.values())
    scan(con, str(r), cfg)
    assert "Ubuntu 12.04.5 LTS" in {s.os_version["name"] for s in Analysis(con, cfg).layout.sources.values()
                                    if s.os_version}
