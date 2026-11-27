#!/usr/bin/env python3
"""Create a realistic demo tree (sparse files simulate large sizes without using
disk space). Handy for trying archivero and for screenshots.

    python3 scripts/demo_tree.py /tmp/demo_nas
"""

import io
import os
import random
import sys
import tarfile
import time
import zipfile
from datetime import datetime

random.seed(7)


def put(path, size, mtime=None, content=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        if content is not None:
            f.write(content)
        else:
            f.write(os.urandom(min(size, 4096)))  # unique header: avoids false duplicates
            if size > 4096:
                f.truncate(size)  # the rest is sparse
    if mtime:
        os.utime(path, (mtime, mtime))


def ts(y, m=6, d=15):
    return time.mktime(datetime(y, m, d, 12).timetuple())


def main(root):
    # Personal photos and videos, 2008-2024
    for y in range(2008, 2025):
        for m in random.sample(range(1, 13), 4):
            ev = os.path.join(root, "Photos", str(y), "%d-%02d event" % (y, m))
            for i in range(random.randint(8, 25)):
                d = random.randint(1, 28)
                put(os.path.join(ev, "IMG_%d%02d%02d_%04d.jpg" % (y, m, d, i)),
                    random.randint(2_000_000, 6_000_000), ts(y, m, d))
            if random.random() < 0.5:
                put(os.path.join(ev, "VID_%d%02d01_0001.mp4" % (y, m)), random.randint(40_000_000, 300_000_000),
                    ts(y, m))
    # A duplicated copy of some photos
    src = os.path.join(root, "Photos", "2015")
    for dirpath, _, files in os.walk(src):
        for fn in files[:10]:
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as f:
                data = f.read()
            put(os.path.join(root, "Phone backup 2016", fn), len(data), os.path.getmtime(p), content=data)
    # Movies
    for name, gb in [("The Movie (1999).mkv", 1.4), ("Documentary.avi", 0.9), ("Series S01E01.mkv", 1.1)]:
        put(os.path.join(root, "Movies", name), int(gb * 1024 ** 3) + random.randint(1, 9999), ts(2012))
    # Music
    for artist in ["Artist One", "Band Two", "Group Three"]:
        for album in range(2):
            for t in range(10):
                put(os.path.join(root, "Music", artist, "Album %d" % (album + 1), "%02d - Track.mp3" % (t + 1)),
                    random.randint(3_000_000, 9_000_000), ts(2010))
    # Documents
    for y in range(2014, 2025):
        for i in range(random.randint(5, 15)):
            ext = random.choice(["pdf", "docx", "xlsx", "pptx", "odt"])
            put(os.path.join(root, "Documents", "Work %d" % y, "document_%02d.%s" % (i, ext)),
                random.randint(50_000, 5_000_000), ts(y, random.randint(1, 12)))
    # Code projects with node_modules and .git
    for proj in ["thesis-web", "mobile-app", "class-scripts"]:
        base = os.path.join(root, "Code", proj)
        put(os.path.join(base, "package.json"), 800, ts(2019))
        for i in range(12):
            put(os.path.join(base, "src", "mod%d.js" % i), random.randint(1000, 20000), ts(2019))
        put(os.path.join(base, ".git", "HEAD"), 30, ts(2019))
        for i in range(300):
            put(os.path.join(base, "node_modules", "pkg%d" % i, "index.js"), random.randint(2000, 400000), ts(2019))
    # Old Thunderbird profile
    prof = os.path.join(root, "Backups", "Laptop 2014", "Thunderbird", "Profiles", "x1y2z3.default")
    put(os.path.join(prof, "prefs.js"), 9000, ts(2014))
    put(os.path.join(prof, "abook.mab"), 30000, ts(2014))
    for box, size in [("Inbox", 1_800_000_000), ("Sent", 420_000_000), ("Trash", 90_000_000),
                      ("Projects", 260_000_000)]:
        put(os.path.join(prof, "Mail", "Local Folders", box), size, ts(2014))
        put(os.path.join(prof, "Mail", "Local Folders", box + ".msf"), 200_000, ts(2014))
    put(os.path.join(root, "Backups", "Laptop 2014", "Thunderbird", "profiles.ini"), 200, ts(2014))
    # Old software
    for prog, items in [("Office 2007", [("setup.exe", 600_000_000), ("PRO.cab", 400_000_000)]),
                        ("Printer drivers", [("driver_x64.exe", 80_000_000)]),
                        ("Ubuntu 10.04", [("ubuntu-10.04-desktop-i386.iso", 700_000_000)]),
                        ("Matlab R2010", [("install.exe", 2_100_000_000), ("readme.txt", 3000)])]:
        for fn, size in items:
            put(os.path.join(root, "Programs", prog, fn), size, ts(2010))
    put(os.path.join(root, "Programs", "VirtualBox.app", "Contents", "MacOS", "VirtualBox"), 150_000_000, ts(2013))
    # Mixed downloads
    put(os.path.join(root, "Downloads", "Firefox Setup 3.6.exe"), 8_000_000, ts(2011))
    put(os.path.join(root, "Downloads", "electricity_bill.pdf"), 120_000, ts(2023))
    put(os.path.join(root, "Downloads", "skype_installer_x86.zip"), 35_000_000, ts(2012))
    # System junk
    for d in ["Photos", "Documents", "Downloads", "Music"]:
        put(os.path.join(root, d, ".DS_Store"), 6148, ts(2018))
        put(os.path.join(root, d, "Thumbs.db"), 40000, ts(2012))
    put(os.path.join(root, "Network Trash Folder", "old.tmp"), 5_000_000, ts(2011))
    # A partial copy of an old XP-era Dell laptop: only the main top folders were kept
    xp = os.path.join(root, "Backups", "Old laptop")
    prof = os.path.join(xp, "Documents and Settings", "Juan")
    for i in range(30):
        y = random.choice([2006, 2007, 2007, 2008])
        put(os.path.join(prof, "Mis documentos", "doc_%02d.doc" % i), random.randint(20_000, 300_000), ts(y, random.randint(1, 12)))
        put(os.path.join(prof, "Mis documentos", "Mis imágenes", "DSC%04d.JPG" % i), random.randint(800_000, 2_000_000), ts(y))
    put(os.path.join(prof, "NTUSER.DAT"), 1_500_000, ts(2008))
    put(os.path.join(prof, "Application Data", "Mozilla", "Firefox", "Profiles", "k3j2.default", "prefs.js"), 9000, ts(2008))
    put(os.path.join(prof, "Application Data", "Mozilla", "Firefox", "Profiles", "k3j2.default", "places.sqlite"), 2_000_000, ts(2008))
    for i in range(50):
        put(os.path.join(prof, "Local Settings", "Temporary Internet Files", "Content.IE5", "f%03d.tmp" % i), 30_000, ts(2008))
    for rel, size in [("Program Files/Dell/QuickSet/QuickSet.exe", 2_000_000),
                      ("Program Files/Synaptics/SynTP/SynTPEnh.exe", 1_200_000),
                      ("Program Files/Microsoft Office/OFFICE12/WINWORD.EXE", 12_000_000),
                      ("Program Files/Dell Support Center/Latitude D630 support.exe", 3_000_000),
                      ("WINDOWS/system32/kernel32.dll", 1_000_000), ("WINDOWS/system32/drivers/atapi.sys", 100_000),
                      ("WINDOWS/Web/Wallpaper/Bliss.jpg", 200_000)]:
        put(os.path.join(xp, *rel.split("/")), size, ts(2007))

    # Archives: a zip of a folder that is also on disk, and a tarball with photos found nowhere else
    os.makedirs(os.path.join(root, "Backups"), exist_ok=True)
    src = os.path.join(root, "Photos", "2016")
    with zipfile.ZipFile(os.path.join(root, "Backups", "Photos 2016.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, files in os.walk(src):
            for fn in files:
                p = os.path.join(dirpath, fn)
                z.write(p, os.path.join("2016", os.path.relpath(p, src)))
    with tarfile.open(os.path.join(root, "Backups", "old laptop photos.tar.gz"), "w:gz") as t:
        for i in range(40):
            data = os.urandom(2048) + bytes(random.randint(100_000, 400_000))
            ti = tarfile.TarInfo("Pictures/2007/DSC%05d.JPG" % i)
            ti.size, ti.mtime = len(data), ts(2007, random.randint(1, 12))
            t.addfile(ti, io.BytesIO(data))
    print("Demo tree created at", root)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/demo_nas")
