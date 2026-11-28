"""Known-file filter (ADR 0007): Bloom filter format, NSRL, online API, proposals and flags."""

import hashlib
import io
import json
import os
import sqlite3

import pytest

from archivero import config
from archivero.analysis import Analysis
from archivero.db import open_db
from archivero.known import BloomFilter, is_candidate, run_known
from archivero.scanner import scan


def w(root, rel, data):
    p = os.path.join(str(root), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def sha1(data):
    return hashlib.sha1(data).hexdigest().upper()


APP_EXE = b"MZ app" * 400
APP_DLL = b"MZ dll" * 400
APP_DAT = b"resource" * 300
SETUP = b"MZ setup" * 300
CUSTOM = b"MZ custom tool" * 50
KERNEL = b"MZ kernel" * 100
WALL = b"\xff\xd8 stock wallpaper" * 30
LETTER = b"Dear Ana" * 100


@pytest.fixture
def inv(tmp_path):
    r = tmp_path / "nas"
    # A program copied out of its drive, to a folder no layout rule knows: only its content says what it is
    w(r, "Backup/old/Juegos/Tetrix/app.exe", APP_EXE)
    w(r, "Backup/old/Juegos/Tetrix/app.dll", APP_DLL)
    w(r, "Backup/old/Juegos/Tetrix/data.bin", APP_DAT)
    w(r, "Backup/old/Juegos/Tetrix/app.ini", b"[app]")
    w(r, "Backup/old/Juegos/Tetrix/readme.txt", b"read me")
    w(r, "Backup/old/Juegos/Tetrix/wall.jpg", WALL)
    w(r, "Backup/old/notes.doc", LETTER * 4)
    w(r, "Ana/Downloads/setup.exe", SETUP)
    w(r, "Ana/Downloads/ticket.pdf", LETTER * 4)
    w(r, "Ana/Documents/letter.doc", LETTER)
    # A Windows drive: one known system file, one executable nobody knows
    w(r, "Disk/WINDOWS/system32/kernel32.dll", KERNEL)
    w(r, "Disk/WINDOWS/system32/tool.exe", CUSTOM)
    w(r, "Disk/Program Files/Other/x.exe", b"MZ x" * 10)
    w(r, "Disk/Documents and Settings/Ana/NTUSER.DAT", b"regf")
    w(r, "Disk/pagefile.sys", b"p")
    cfg = config.load()
    cfg["software_min_bytes"] = 1000
    con = open_db(str(tmp_path / "inv.db"))
    scan(con, str(r), cfg)
    bloom = str(tmp_path / "hashlookup-full.bloom")
    BloomFilter.write(bloom, [sha1(x).encode() for x in (APP_EXE, APP_DLL, APP_DAT, KERNEL, WALL)], n=1000)
    return r, con, cfg, bloom, tmp_path


def test_fnv1_and_bloom_roundtrip(tmp_path):
    # Reference vectors for 64-bit FNV-1 (not 1a), the hash DCSO's bloom library uses
    assert BloomFilter.fnv1_64(b"") == 0xCBF29CE484222325
    assert BloomFilter.fnv1_64(b"a") == 0xAF63BD4C8601B7BE
    keys = [("%040X" % i).encode() for i in range(500)]
    p = str(tmp_path / "t.bloom")
    BloomFilter.write(p, keys, p=0.001)
    bf = BloomFilter(p)
    assert all(k in bf for k in keys)
    assert sum(("%040X" % (10 ** 6 + i)).encode() in bf for i in range(2000)) < 20
    assert (bf.k, bf.count) == (10, 500)
    bf.close()
    with open(p, "r+b") as f:
        f.write(b"\x07")
    with pytest.raises(ValueError):
        BloomFilter(p)


def test_candidates_rule():
    assert is_candidate("executable", "a.exe", "user_data")
    assert is_candidate("compressed", "setup_v2.zip", "user_data")
    assert not is_candidate("compressed", "photos2005.zip", "unknown")
    assert is_candidate("no_extension", "SOFTWARE", "system")
    assert not is_candidate("config", "settings.ini", "user_data")
    for cat in ("document", "pdf", "email", "certificate", "database", "audio", "raw"):
        assert not is_candidate(cat, "x", "system")
    assert not is_candidate("image", "a.jpg", "system")
    assert is_candidate("image", "a.jpg", "system", images=True)
    assert not is_candidate("image", "a.jpg", "user_data", images=True)


def test_known_offline(inv):
    r, con, cfg, bloom, tmp = inv
    res = run_known(con, cfg, bloom=[bloom])
    rows = {row[0]: row[1:] for row in con.execute(
        "SELECT f.name, k.status, k.source FROM files f JOIN known k ON k.hash = f.full_hash")}
    assert rows["app.exe"] == ("known", "bloom:hashlookup-full")
    assert rows["kernel32.dll"][0] == "known"
    assert rows["setup.exe"][0] == "unknown" and rows["tool.exe"][0] == "unknown"
    assert rows["app.ini"][0] == "unknown"
    assert "data.bin" in rows and "app.ini" in rows  # software-like files outside user folders
    for name in ("letter.doc", "readme.txt", "wall.jpg"):  # documents and pictures are not looked up
        assert name not in rows
    assert "pagefile.sys" not in rows
    assert res["known"] == 4
    # Resumable: nothing is read again, and a new set only sees what is still unknown
    hashed = con.execute("SELECT COUNT(*) FROM known").fetchone()[0]
    db = str(tmp / "rds_modern_minimal.db")
    nsrl = sqlite3.connect(db)
    nsrl.execute("CREATE TABLE FILE (sha256 TEXT, sha1 TEXT, md5 TEXT, file_name TEXT, file_size INTEGER, "
                 "package_id INTEGER)")
    nsrl.execute("INSERT INTO FILE VALUES ('', ?, '', 'SETUP.EXE', 1, 1)", (sha1(SETUP),))
    nsrl.execute("CREATE TABLE VERSION (version TEXT)")
    nsrl.execute("INSERT INTO VERSION VALUES ('2025.03.1')")
    nsrl.commit()
    nsrl.close()
    mtime = os.path.getmtime(db)
    run_known(con, cfg, bloom=[bloom], nsrl=[db])
    assert con.execute("SELECT COUNT(*) FROM known").fetchone()[0] == hashed
    assert con.execute("SELECT k.status, k.source, k.name FROM files f JOIN known k ON k.hash = f.full_hash "
                       "WHERE f.name='setup.exe'").fetchone() == ("known", "nsrl:rds_modern_minimal", "SETUP.EXE")
    assert os.path.getmtime(db) == mtime  # the set is opened read-only
    sets = json.loads(con.execute("SELECT value FROM meta WHERE key='known_sets'").fetchone()[0])
    assert {s["label"] for s in sets} == {"bloom:hashlookup-full", "nsrl:rds_modern_minimal"}

    an = Analysis(con, cfg)
    props = {os.path.relpath(p["path"], str(r)): p for p in an.cleanup}
    folder = props["Backup/old/Juegos"]  # the top-most folder made of known files
    assert folder["type"] == "known_software" and "CIRCL hashlookup and NSRL" in folder["reason"]
    assert "2 files inside look personal" in folder["reason"]  # wall.jpg and readme.txt, not looked up
    assert props["Ana/Downloads/setup.exe"]["type"] == "known_software"
    assert "Ana/Documents/letter.doc" not in props
    assert [os.path.relpath(p, str(r)) for p, _ in an.unknown_sw] == [
        "Disk/Program Files/Other/x.exe", "Disk/WINDOWS/system32/tool.exe"]


def test_report_shows_known(inv):
    r, con, cfg, bloom, tmp = inv
    from archivero.report import report
    out = str(tmp / "rep")
    report(Analysis(con, cfg), out)
    assert "Not checked yet" in open(os.path.join(out, "report.html"), encoding="utf-8").read()
    run_known(con, cfg, bloom=[bloom])
    report(Analysis(con, cfg), out)
    html = open(os.path.join(out, "report.html"), encoding="utf-8").read()
    assert "Unknown executables in system or program folders (2)" in html
    assert "Known OS and software files" in html
    import csv
    with open(os.path.join(out, "files.csv"), encoding="utf-8") as f:
        known = {row["name"]: row["known"] for row in csv.DictReader(f)}
    assert known["app.exe"] == "bloom:hashlookup-full" and known["tool.exe"] == "unknown"
    assert known["letter.doc"] == ""


def test_online_sends_only_executables_once(inv):
    r, con, cfg, bloom, tmp = inv
    sent = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        hashes = json.loads(req.data.decode())["hashes"]
        sent.extend(hashes)
        assert req.full_url == "https://hashlookup.circl.lu/bulk/sha1"
        known = [{"SHA-1": h, "FileName": "tool.exe"} for h in hashes if h == sha1(CUSTOM)]
        return Resp(json.dumps(known + [{"message": "Non existing SHA-1", "query": "x"}]).encode())

    run_known(con, cfg, bloom=[bloom], online=True, opener=opener)
    names = {h: n for n, h in con.execute("SELECT f.name, k.sha1 FROM files f JOIN known k ON k.hash=f.full_hash")}
    assert {names[h] for h in sent} == {"setup.exe", "tool.exe", "x.exe"}  # executables the Bloom filter missed
    assert con.execute("SELECT k.source FROM files f JOIN known k ON k.hash=f.full_hash WHERE f.name='tool.exe'"
                       ).fetchone()[0] == "circl-api"
    sent.clear()
    run_known(con, cfg, bloom=[bloom], online=True, opener=opener)
    assert sent == []  # never sent twice


def test_known_images_flag_media(inv):
    r, con, cfg, bloom, tmp = inv
    run_known(con, cfg, bloom=[bloom], images=True)
    h = con.execute("SELECT full_hash FROM files WHERE name='wall.jpg'").fetchone()[0]
    assert con.execute("SELECT status FROM known WHERE hash=?", (h,)).fetchone()[0] == "known"
    con.execute("INSERT INTO media(hash, kind, width, height, status) VALUES(?, 'image', 1920, 1080, 'ok')", (h,))
    con.commit()
    from archivero.media_index import flag_nonpersonal
    flag_nonpersonal(con, cfg)
    assert "known_file" in con.execute("SELECT nonpersonal_why FROM media WHERE hash=?", (h,)).fetchone()[0]


def test_no_set_only_hashes(inv, capsys):
    r, con, cfg, bloom, tmp = inv
    run_known(con, cfg)
    assert con.execute("SELECT COUNT(*) FROM known WHERE status IS NULL").fetchone()[0] > 0
    assert "No hash set given" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run_known(con, cfg, bloom=[str(tmp / "missing.bloom")])
