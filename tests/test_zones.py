"""Zones and provenance on partial copies of system drives and profiles."""

import os
import time
import zipfile
from datetime import datetime

import pytest

from archivero import config
from archivero.analysis import Analysis
from archivero.archives import list_containers
from archivero.db import open_db
from archivero.scanner import scan


def ts(y, m=6):
    return time.mktime(datetime(y, m, 15, 12).timetuple())


def w(root, rel, data=b"x", year=None):
    p = os.path.join(str(root), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    if year:
        os.utime(p, (ts(year), ts(year)))


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "nas"
    # 1) Partial copy of an XP-era Dell laptop: only some top folders were kept
    xp = "Backups/Old Dell"
    for i in range(6):
        w(r, xp + "/Documents and Settings/Juan/Mis documentos/carta%d.doc" % i, b"D%d" % i, 2006 + i % 3)
        w(r, xp + "/Documents and Settings/Juan/Escritorio/foto%d.jpg" % i, b"J%d" % i * 500, 2007)
    w(r, xp + "/Documents and Settings/Juan/NTUSER.DAT", b"regf")
    ff = xp + "/Documents and Settings/Juan/Application Data/Mozilla/Firefox/Profiles/ab12.default/"
    w(r, ff + "prefs.js", b"user_pref")
    w(r, ff + "places.sqlite", b"sqlite")
    w(r, xp + "/Documents and Settings/Juan/Local Settings/Temp/~tmp1.tmp", b"t")
    w(r, xp + "/Program Files/Dell/QuickSet/QuickSet.exe", b"E" * 100, 2007)
    w(r, xp + "/Program Files/Synaptics/SynTP/SynTPEnh.exe", b"E" * 100, 2007)
    w(r, xp + "/Program Files/Microsoft Office/OFFICE12/WINWORD.EXE", b"E" * 100, 2007)
    w(r, xp + "/Program Files/Dell/Latitude D630/readme.txt", b"r", 2007)
    w(r, xp + "/WINDOWS/system32/kernel32.dll", b"K" * 100, 2007)
    w(r, xp + "/WINDOWS/Web/Wallpaper/Bliss.jpg", b"W" * 100, 2004)
    # 2) A lone Vista-era profile (no drive around it)
    v = "Backups/Juan Vista"
    w(r, v + "/NTUSER.DAT", b"regf")
    tb = v + "/AppData/Roaming/Thunderbird/Profiles/x9.default/"
    w(r, tb + "prefs.js", b"user_pref")
    w(r, tb + "Mail/Local Folders/Inbox", b"From a\n" * 50)
    w(r, tb + "Mail/Local Folders/Inbox.msf", b"m")
    w(r, v + "/Documents/informe.pdf", b"P" * 300, 2011)
    w(r, v + "/AppData/Local/Temp/junk.tmp", b"j")
    w(r, v + "/AppData/Roaming/Apple Computer/MobileSync/Backup/abc/Manifest.db", b"m")
    # 3) A Linux home copied out of /home
    lx = "Backups/ubuntu/home/juan"
    w(r, lx + "/.bashrc", b"export")
    w(r, lx + "/.cache/thumbnails/a.png", b"c" * 100)
    w(r, lx + "/.config/app/settings.ini", b"s")
    w(r, lx + "/Documentos/tesis.odt", b"T" * 300, 2014)
    # 4) A Mac home
    mac = "Backups/MacBook Pro"
    w(r, mac + "/Library/Caches/com.apple.Safari/Cache.db", b"c")
    w(r, mac + "/Movies/holiday.mov", b"M" * 300, 2015)
    w(r, mac + "/Pictures/Photos Library.photoslibrary/originals/IMG_1.jpg", b"O" * 300, 2015)
    w(r, mac + "/Pictures/Photos Library.photoslibrary/resources/derivatives/IMG_1.jpg", b"d" * 50, 2015)
    w(r, mac + "/Desktop/notes.txt", b"n", 2015)
    # 5) A zipped XP drive
    os.makedirs(r / "Images", exist_ok=True)
    with zipfile.ZipFile(r / "Images/oldpc.zip", "w") as z:
        z.writestr("Documents and Settings/Ana/Mis documentos/diario.doc", b"diary" * 50)
        z.writestr("Documents and Settings/Ana/NTUSER.DAT", b"regf")
        z.writestr("WINDOWS/system32/hal.dll", b"H" * 500)
        z.writestr("Program Files/Toshiba/Utilities/tosutil.exe", b"T" * 500)
        z.writestr("boot.ini", b"[boot loader]")
    return r


@pytest.fixture
def an(tree, tmp_path):
    cfg = config.load()
    con = open_db(str(tmp_path / "inv.db"))
    scan(con, str(tree), cfg)
    list_containers(con, cfg)
    return Analysis(con, cfg)


def by_rel(an, tree):
    return {os.path.relpath(f[1], str(tree)): f[0] for f in an.files}


def test_zones(an, tree):
    ids = by_rel(an, tree)
    z = lambda rel: an.file_zone[ids[rel]]  # noqa: E731
    assert z("Backups/Old Dell/Documents and Settings/Juan/Mis documentos/carta0.doc") == "user_data"
    assert z("Backups/Old Dell/Documents and Settings/Juan/Local Settings/Temp/~tmp1.tmp") == "cache"
    assert z("Backups/Old Dell/Program Files/Dell/QuickSet/QuickSet.exe") == "applications"
    assert z("Backups/Old Dell/WINDOWS/system32/kernel32.dll") == "system"
    assert z("Backups/Juan Vista/AppData/Local/Temp/junk.tmp") == "cache"
    assert z("Backups/Juan Vista/Documents/informe.pdf") == "user_data"
    assert z("Backups/ubuntu/home/juan/.cache/thumbnails/a.png") == "cache"
    assert z("Backups/ubuntu/home/juan/.bashrc") == "user_appdata"
    assert z("Backups/ubuntu/home/juan/Documentos/tesis.odt") == "user_data"
    assert z("Backups/MacBook Pro/Pictures/Photos Library.photoslibrary/originals/IMG_1.jpg") == "user_data"
    assert z("Backups/MacBook Pro/Pictures/Photos Library.photoslibrary/resources/derivatives/IMG_1.jpg") == "cache"
    assert z("Backups/MacBook Pro/Library/Caches/com.apple.Safari/Cache.db") == "cache"
    # A wallpaper shipped with Windows is not a personal photo
    assert ids["Backups/Old Dell/WINDOWS/Web/Wallpaper/Bliss.jpg"] not in an.personal_ids
    assert ids["Backups/Old Dell/Documents and Settings/Juan/Escritorio/foto0.jpg"] in an.personal_ids


def test_sources_and_hints(an, tree):
    srcs = {os.path.relpath(an.source_name(s), str(tree)): s for s in an.layout.sources.values()}
    dell = srcs["Backups/Old Dell"]
    text = dell.describe()
    assert "Windows" in text and "XP" in text
    assert "Dell laptop" in text and "Latitude" in text
    assert "2006" in text and "2008" in text
    assert any("Office 2007" in e for e in dell.evidence())
    vista = srcs["Backups/Juan Vista"]
    assert "Vista" in vista.describe() or "7" in vista.describe()
    assert "Linux" in srcs["Backups/ubuntu"].describe()  # implied from /home
    assert "Mac home" in srcs["Backups/MacBook Pro"].describe()
    hints = {os.path.relpath(p, str(tree)): t for p, k, t in an.app_profiles}
    ff = hints["Backups/Old Dell/Documents and Settings/Juan/Application Data/Mozilla/Firefox/Profiles/ab12.default"]
    assert "Firefox profile from Windows 2000/XP" in ff and "Dell laptop" in ff
    labels = {label for _, label in an.valuable}
    assert "iPhone/iPad backup" in labels and "Thunderbird mail" in labels


def test_cleanup_by_zone(an, tree):
    props = {(p["type"], os.path.relpath(p["path"], str(tree))) for p in an.cleanup}
    assert ("os_files", "Backups/Old Dell/WINDOWS") in props
    assert ("installed_programs", "Backups/Old Dell/Program Files") in props
    assert ("cache_files", "Backups/Juan Vista/AppData/Local/Temp") in props
    assert not any(t in ("os_files", "installed_programs", "cache_files") and "Mis documentos" in p
                   for t, p in props)
    reason = next(p["reason"] for p in an.cleanup if p["path"].endswith("Old Dell/WINDOWS"))
    assert "look personal" in reason  # the wallpaper is flagged for review, not silently deleted


def test_disk_image_members(an, tree):
    c = an.containers[str(tree / "Images/oldpc.zip")]
    assert "Windows" in c["hint"] and "Toshiba" in c["hint"]
    assert c["zones"]["system"] > 0 and c["personal_missing"]["document"] == 1
