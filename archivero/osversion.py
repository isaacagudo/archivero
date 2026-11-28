"""Exact operating-system versions from the small files that state them.

A copied system drive usually keeps the file where its OS writes its own version:
``etc/os-release`` on Linux, ``System/Library/CoreServices/SystemVersion.plist`` on macOS,
and the ``SOFTWARE`` registry hive on Windows. 'scan' reads these files (only at the
paths listed in ``layouts.VERSION_FILES``, and only a few kilobytes of each) and keeps
what they say in ``files.extra``; the analysis then attaches it to the source.

Every parser returns a dict with at least ``name`` (``"Ubuntu 10.04.4 LTS"``), or None.
"""

import plistlib
import re
import struct

from .layouts import MAC_RELEASE_NAMES, VERSION_FILES

MAX_TEXT = 64 * 1024  # version text files are tiny; never read more than this

# --------------------------------------------------------------------- text formats


def _text(data):
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _keyvals(text):
    """KEY=value lines (os-release, lsb-release), quotes removed."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip().upper()] = v.replace('\\"', '"').strip()
    return out


def parse_os_release(data):
    kv = _keyvals(_text(data))
    name = kv.get("PRETTY_NAME") or " ".join(x for x in (kv.get("NAME"), kv.get("VERSION")) if x)
    if not name:
        return None
    rec = {"name": name, "os": "Linux/Unix"}
    if kv.get("ID"):
        rec["id"] = kv["ID"]
    if kv.get("VERSION_ID"):
        rec["version"] = kv["VERSION_ID"]
    return rec


def parse_lsb_release(data):
    kv = _keyvals(_text(data))
    name = kv.get("DISTRIB_DESCRIPTION") or " ".join(
        x for x in (kv.get("DISTRIB_ID"), kv.get("DISTRIB_RELEASE")) if x)
    if not name:
        return None
    rec = {"name": name, "os": "Linux/Unix"}
    if kv.get("DISTRIB_RELEASE"):
        rec["version"] = kv["DISTRIB_RELEASE"]
    return rec


def parse_release_line(data):
    """One-line release files: redhat-release, centos-release, SuSE-release…"""
    line = next((ln.strip() for ln in _text(data).splitlines() if ln.strip()), "")
    if not line or len(line) > 200:
        return None
    m = re.search(r"\d+(?:\.\d+)*", line)
    rec = {"name": line, "os": "Linux/Unix"}
    if m:
        rec["version"] = m.group(0)
    return rec


def parse_debian_version(data):
    v = _text(data).strip().splitlines()[0].strip() if _text(data).strip() else ""
    if not v or len(v) > 40:
        return None
    return {"name": "Debian " + v, "os": "Linux/Unix", "id": "debian", "version": v}


def mac_release_name(version):
    """'10.6.8' -> 'Snow Leopard', '14.2' -> 'Sonoma'."""
    parts = version.split(".")
    key = ".".join(parts[:2]) if parts[0] == "10" else parts[0]
    return MAC_RELEASE_NAMES.get(key)


def parse_system_version_plist(data):
    try:
        pl = plistlib.loads(data)
    except Exception:
        return None
    if not isinstance(pl, dict):
        return None
    product = pl.get("ProductName") or "macOS"
    version = pl.get("ProductUserVisibleVersion") or pl.get("ProductVersion")
    if not version:
        return None
    version = str(version)
    name = "%s %s" % (product, version)
    rel = mac_release_name(version)
    if rel:
        name += " " + rel
    rec = {"name": name, "os": "macOS", "version": version}
    if pl.get("ProductBuildVersion"):
        rec["build"] = str(pl["ProductBuildVersion"])
    return rec


def parse_prodspec(data):
    """Windows 2000/XP/2003 system32/prodspec.ini: Product=Windows XP Professional."""
    sec = None
    for line in _text(data).splitlines():
        line = line.strip()
        if line.startswith("["):
            sec = line.strip("[]").strip().lower()
        elif sec == "product specification" and line.lower().startswith("product="):
            name = line.partition("=")[2].strip()
            if name:
                return {"name": name, "os": "Windows"}
    return None


# ------------------------------------------------------------------ registry hive

class HiveError(Exception):
    pass


class Hive:
    """Minimal read-only reader for Windows registry hives (regf).

    It reads only the cells on the way to the requested key, by seeking, so finding a
    value in a 100 MB SOFTWARE hive touches a few kilobytes.
    """

    BASE = 4096

    def __init__(self, f):
        self.f = f
        head = self._read(0, 512)
        if head[:4] != b"regf":
            raise HiveError("not a registry hive")
        self.root = struct.unpack_from("<I", head, 0x24)[0]

    def _read(self, off, n):
        self.f.seek(off)
        data = self.f.read(n)
        if len(data) < n:
            raise HiveError("truncated hive")
        return data

    def cell(self, off):
        size = struct.unpack("<i", self._read(self.BASE + off, 4))[0]
        size = -size if size < 0 else size
        if size < 8 or size > 16 * 1024 * 1024:
            raise HiveError("bad cell at %#x" % off)
        return self._read(self.BASE + off + 4, size - 4)

    @staticmethod
    def _name(raw, comp):
        return raw.decode("latin-1") if comp else raw.decode("utf-16-le", "replace")

    def _nk(self, off):
        c = self.cell(off)
        if c[:2] != b"nk":
            raise HiveError("expected a key at %#x" % off)
        flags = struct.unpack_from("<H", c, 0x02)[0]
        n_sub, sub_off = struct.unpack_from("<I4xI", c, 0x14)
        n_val, val_off = struct.unpack_from("<II", c, 0x24)
        nlen = struct.unpack_from("<H", c, 0x48)[0]
        name = self._name(c[0x4C:0x4C + nlen], flags & 0x20)
        return {"name": name, "n_sub": n_sub, "sub": sub_off, "n_val": n_val, "val": val_off}

    def _subkeys(self, list_off):
        c = self.cell(list_off)
        sig, n = c[:2], struct.unpack_from("<H", c, 2)[0]
        if sig in (b"lf", b"lh"):
            return [struct.unpack_from("<I", c, 4 + 8 * i)[0] for i in range(n)]
        if sig == b"li":
            return [struct.unpack_from("<I", c, 4 + 4 * i)[0] for i in range(n)]
        if sig == b"ri":
            out = []
            for i in range(n):
                out += self._subkeys(struct.unpack_from("<I", c, 4 + 4 * i)[0])
            return out
        raise HiveError("unknown subkey list %r" % sig)

    def key(self, path):
        """The key at a backslash-separated path, or None."""
        nk = self._nk(self.root)
        for part in [p for p in path.split("\\") if p]:
            if not nk["n_sub"]:
                return None
            want = part.lower()
            for off in self._subkeys(nk["sub"]):
                sub = self._nk(off)
                if sub["name"].lower() == want:
                    nk = sub
                    break
            else:
                return None
        return nk

    def values(self, nk):
        """{name: value} for REG_SZ / REG_EXPAND_SZ / REG_DWORD values of a key."""
        out = {}
        if not nk["n_val"]:
            return out
        lst = self.cell(nk["val"])
        for i in range(nk["n_val"]):
            vk = self.cell(struct.unpack_from("<I", lst, 4 * i)[0])
            if vk[:2] != b"vk":
                continue
            nlen, dsize, doff, vtype, flags = struct.unpack_from("<HIIIH", vk, 2)
            name = self._name(vk[0x14:0x14 + nlen], flags & 1)
            inline = dsize & 0x80000000
            dsize &= 0x7FFFFFFF
            if vtype == 4 and dsize == 4:  # REG_DWORD
                raw = struct.pack("<I", doff) if inline else self.cell(doff)[:4]
                out[name] = struct.unpack("<I", raw)[0]
            elif vtype in (1, 2) and 0 < dsize <= 16344:  # REG_SZ, REG_EXPAND_SZ (no big-data cells)
                raw = struct.pack("<I", doff)[:dsize] if inline else self.cell(doff)[:dsize]
                out[name] = raw.decode("utf-16-le", "replace").split("\x00")[0]
        return out


def windows_version(values):
    """Describe Windows from the values of HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion."""
    product = (values.get("ProductName") or "").strip()
    if not product:
        return None
    build = str(values.get("CurrentBuildNumber") or values.get("CurrentBuild") or "").strip()
    if build.isdigit() and int(build) >= 22000 and "Windows 10" in product:
        product = product.replace("Windows 10", "Windows 11")  # Windows 11 still says 10 here
    if product.lower().startswith("microsoft "):
        product = product[len("microsoft "):]  # 'Microsoft Windows XP' (2000/XP/2003)
    if not product.lower().startswith("windows"):
        product = "Windows " + product
    name = product
    extra = values.get("DisplayVersion") or values.get("ReleaseId") or values.get("CSDVersion")
    if extra:
        name += " " + str(extra).strip()
    rec = {"name": name, "os": "Windows"}
    if values.get("CurrentVersion"):
        rec["version"] = str(values["CurrentVersion"])
    if build:
        rec["build"] = build
    return rec


def read_registry_version(path):
    with open(path, "rb") as f:
        hive = Hive(f)
        nk = hive.key("Microsoft\\Windows NT\\CurrentVersion")
        return windows_version(hive.values(nk)) if nk else None


# ---------------------------------------------------------------------- dispatch

PARSERS = {
    "os_release": parse_os_release,
    "lsb_release": parse_lsb_release,
    "release_line": parse_release_line,
    "debian_version": parse_debian_version,
    "plist": parse_system_version_plist,
    "prodspec": parse_prodspec,
}

_BY_NAME = {}
for _rel, _kind in VERSION_FILES:
    _BY_NAME.setdefault(_rel.rsplit("/", 1)[-1], []).append((tuple(_rel.split("/")[:-1]), _rel, _kind))
VERSION_FILE_NAMES = frozenset(_BY_NAME)


def match(dir_segs_low, name_low):
    """(relative path, parser kind) when a file sits where an OS keeps its version, else None."""
    for parent, rel, kind in _BY_NAME.get(name_low, ()):
        if tuple(dir_segs_low[len(dir_segs_low) - len(parent):]) == parent:
            return rel, kind
    return None


def read_version(path, kind, size):
    """Parse a version file found on disk. Returns a dict, or None if it says nothing useful."""
    try:
        if kind == "registry":
            return read_registry_version(path)
        if size is not None and size > MAX_TEXT:
            return None
        with open(path, "rb") as f:
            data = f.read(MAX_TEXT)
        return PARSERS[kind](data)
    except (OSError, HiveError, struct.error, ValueError, KeyError, IndexError):
        return None
