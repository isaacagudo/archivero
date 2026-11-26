"""End-to-end test on a synthetic tree."""

import csv
import os
import time

import pytest

from archivero import config
from archivero.analysis import Analysis
from archivero.db import open_db
from archivero.dupes import find_duplicates
from archivero.plan import apply_plan, make_plan
from archivero.report import report
from archivero.scanner import scan

OLD = time.time() - 10 * 365 * 86400  # ten years ago


def w(path, data=b"x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "nas"
    # Photos with one duplicate and a date in the file name
    w(r / "Photos/Trip/IMG_20190812_101010.jpg", b"A" * 5000)
    w(r / "Photos/Trip/IMG_20190812_101011.jpg", b"B" * 5000)
    w(r / "Photos backup/IMG_20190812_101010.jpg", b"A" * 5000)
    w(r / "Photos/Trip/.DS_Store", b"junk")
    # Documents
    w(r / "Work/report.docx", b"D" * 3000)
    w(r / "Work/paper.pdf", b"P" * 4000)
    # Code project with node_modules and .git
    w(r / "Code/myapp/package.json", b"{}")
    w(r / "Code/myapp/index.js", b"console.log(1)")
    w(r / "Code/myapp/node_modules/left-pad/index.js", b"y" * 2000)
    w(r / "Code/myapp/.git/HEAD", b"ref")
    # Thunderbird profile
    p = r / "Backup/Thunderbird/Profiles/abcd.default"
    w(p / "prefs.js", b"user_pref")
    w(p / "Mail/Local Folders/Inbox", b"From a@b\n" * 200)
    w(p / "Mail/Local Folders/Inbox.msf", b"msf")
    w(r / "Backup/Thunderbird/profiles.ini", b"[Profile0]")
    # Old software
    w(r / "Programs/Office2007/setup.exe", b"E" * (2 * 1024 * 1024), OLD)
    w(r / "Programs/Office2007/data.cab", b"C" * (1024 * 1024), OLD)
    w(r / "Programs/Office2007/readme.txt", b"read", OLD)
    # Old stand-alone installer in a mixed folder
    w(r / "Downloads/driver_x64.zip", b"Z" * (1500 * 1024), OLD)
    w(r / "Downloads/notes.txt", b"notes")
    # Music
    for i in range(4):
        w(r / ("Music/Album1/%02d track.mp3" % i), bytes([i]) * 3000)
    # Movie (by size; threshold lowered in the config below)
    w(r / "Movies/movie.mkv", b"M" * 200000)
    return r


def test_full_workflow(tree, tmp_path):
    cfg = config.load()
    cfg["long_video_gb"] = 100000 / 1024 ** 3
    con = open_db(str(tmp_path / "inv.db"))
    scan(con, str(tree), cfg)
    find_duplicates(con, cfg)
    an = Analysis(con, cfg)

    kinds = {(os.path.relpath(r[0], str(tree)), r[1]) for r in an.detections}
    assert ("Code/myapp", "code_project") in kinds
    assert ("Code/myapp/node_modules", "dependencies") in kinds
    assert ("Backup/Thunderbird/Profiles/abcd.default", "thunderbird_profile") in kinds
    assert ("Music/Album1", "music_folder") in kinds

    cats = {os.path.relpath(f[1], str(tree)): (f[4], f[5]) for f in an.files}
    assert cats["Backup/Thunderbird/Profiles/abcd.default/Mail/Local Folders/Inbox"] == ("email", "mbox")
    assert cats["Movies/movie.mkv"][0] == "video_long"
    assert cats["Photos/Trip/.DS_Store"][0] == "system_junk"

    # Duplicate: the copy under "Photos" is kept, the backup one is proposed
    types = {(p["type"], os.path.relpath(p["path"], str(tree))) for p in an.cleanup}
    assert ("duplicate", "Photos backup/IMG_20190812_101010.jpg") in types
    # The whole "Programs" folder is old software: the top-most folder is proposed
    assert ("obsolete_software", "Programs") in types
    assert ("obsolete_software", "Programs/Office2007") not in types
    assert ("old_installer", "Downloads/driver_x64.zip") in types
    assert ("regenerable", "Code/myapp/node_modules") in types
    assert ("system_junk", "Photos/Trip/.DS_Store") in types

    out = tmp_path / "report"
    report(an, str(out))
    for name in ("report.html", "cleanup_proposals.csv", "folders.csv", "files.csv", "duplicates.csv",
                 "units.csv"):
        assert (out / name).exists()

    plan = tmp_path / "plan.csv"
    dest = tmp_path / "new"
    make_plan(an, str(dest), str(plan))
    with open(plan, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_src = {os.path.relpath(r["source"], str(tree)): r for r in rows}
    assert by_src["Photos/Trip/IMG_20190812_101010.jpg"]["destination"].endswith(
        os.path.join("Photos_and_videos", "2019", "2019-08", "IMG_20190812_101010.jpg"))
    assert by_src["Code/myapp"]["action"] == "copy_dir"
    assert by_src["Photos backup/IMG_20190812_101010.jpg"]["action"] == "skip_cleanup"
    assert by_src["Programs/Office2007/setup.exe"]["action"] == "skip_cleanup"
    assert "Code/myapp/index.js" not in by_src  # travels with its project

    apply_plan(str(plan), execute=True)
    assert (dest / "Projects/myapp/.git/HEAD").exists()
    assert not (dest / "Projects/myapp/node_modules").exists()
    assert (dest / "Email/Thunderbird/Profiles__abcd.default/Mail/Local Folders/Inbox").exists()
    assert (dest / "Music/Album1/00 track.mp3").exists()

    # Resuming: a second scan inserts nothing new
    n_before = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    scan(con, str(tree), cfg)
    assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == n_before


def test_classify_mbox_and_junk():
    from archivero.categories import classify
    assert classify("Inbox", {"inbox", "inbox.msf"}) == ("", "email", "mbox")
    assert classify("Inbox.msf") == ("msf", "email", "thunderbird_index")
    assert classify("Enviados", set(), in_mail_profile=True) == ("", "email", "mbox")
    assert classify("._photo.jpg")[1] == "system_junk"
    assert classify("Makefile")[1] == "code"
    assert classify("song.FLAC")[1] == "audio"
