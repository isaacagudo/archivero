"""Archives and disk images: listing, inspection and matching with folders on disk."""

import os
import shutil
import tarfile
import zipfile

import pytest

from archivero import config
from archivero.analysis import Analysis
from archivero.archives import SevenZipBackend, container_format, inspect_containers, list_containers
from archivero.db import open_db
from archivero.scanner import scan


def w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "nas"
    photos = {"a.jpg": b"A" * 4000, "b.jpg": b"B" * 4100, "sub/c.jpg": b"C" * 4200}
    for name, data in photos.items():
        w(r / "Photos" / "Trip" / name, data)
    os.makedirs(r / "Backups")
    # 1) A zip of the folder, which is also extracted on disk
    with zipfile.ZipFile(r / "Backups/trip.zip", "w") as z:
        for name, data in photos.items():
            z.writestr("Trip/" + name, data)
    # 2) The same content under another top-folder name
    with zipfile.ZipFile(r / "Backups/renamed.zip", "w") as z:
        for name, data in photos.items():
            z.writestr("Viaje 2019/" + name, data)
    # 3) A tarball with photos that exist nowhere else
    src = tmp_path / "only_in_tar"
    for i in range(3):
        w(src / ("old_%d.jpg" % i), bytes([i]) * 5000)
    with tarfile.open(r / "Backups/old_photos.tar.gz", "w:gz") as t:
        t.add(src, arcname="old")
    # 4) A zip with the same names and sizes but different content (must not be "confirmed")
    with zipfile.ZipFile(r / "Backups/lookalike.zip", "w") as z:
        for name, data in photos.items():
            z.writestr("Trip/" + name, bytes(reversed(data[:-1])) + b"Z")
    return r


def analyse(tree, tmp_path, inspect=None):
    cfg = config.load()
    con = open_db(str(tmp_path / "inv.db"))
    scan(con, str(tree), cfg)
    list_containers(con, cfg)
    if inspect:
        inspect_containers(con, cfg, everything=True)
    return con, cfg, Analysis(con, cfg)


def rel(tree, p):
    return os.path.relpath(p, str(tree))


def test_container_format():
    assert container_format("x.zip") == "zip"
    assert container_format("x.TAR.GZ") == "tar"
    assert container_format("disk.vmdk") == "image"
    assert container_format("notes.txt") is None


def test_list_and_match(tree, tmp_path):
    con, cfg, an = analyse(tree, tmp_path)
    c = {rel(tree, k): v for k, v in an.containers.items()}
    assert c["Backups/trip.zip"]["suggestion"] == "already_extracted"
    assert rel(tree, c["Backups/trip.zip"]["extracted_at"]) == "Photos/Trip"
    assert c["Backups/trip.zip"]["confidence"] == "likely (name+size)"
    # Renamed top folder still points at the folder on disk
    assert rel(tree, c["Backups/renamed.zip"]["extracted_at"]) == "Photos/Trip"
    # The tarball holds photos found nowhere else
    assert c["Backups/old_photos.tar.gz"]["suggestion"] == "inspect"
    assert c["Backups/old_photos.tar.gz"]["personal_missing"]["image"] == 3
    # Zip CRC32 values are read from the index for free
    crc = con.execute("SELECT crc32 FROM members WHERE inner_path='Trip/a.jpg' AND container LIKE '%trip.zip'").fetchone()
    assert crc and crc[0]
    # Name+size is not enough to propose deleting the archive
    props = {(p["type"], rel(tree, p["path"])) for p in an.cleanup}
    assert ("extracted_archive", "Backups/trip.zip") not in props


def test_inspect_confirms_by_hash(tree, tmp_path):
    con, cfg, an = analyse(tree, tmp_path, inspect=True)
    c = {rel(tree, k): v for k, v in an.containers.items()}
    assert c["Backups/trip.zip"]["status"] == "inspected"
    assert c["Backups/trip.zip"]["confidence"] == "confirmed (hash)"
    # Same names and sizes, different bytes: only "likely", never "confirmed"
    assert c["Backups/lookalike.zip"]["confidence"] == "likely (name+size)"
    props = {(p["type"], rel(tree, p["path"])) for p in an.cleanup}
    assert ("extracted_archive", "Backups/trip.zip") in props
    assert ("extracted_archive", "Backups/lookalike.zip") not in props
    n = con.execute("SELECT COUNT(*) FROM members WHERE full_hash IS NOT NULL").fetchone()[0]
    assert n >= 12


def test_libarchive_7z(tmp_path):
    libarchive = pytest.importorskip("libarchive")
    r = tmp_path / "nas"
    w(r / "Docs/report.pdf", b"P" * 3000)
    os.makedirs(r / "Backups")
    with libarchive.file_writer(str(r / "Backups/docs.7z"), "7zip") as a:
        a.add_file_from_memory("Docs/report.pdf", 3000, b"P" * 3000)
    con, cfg, an = analyse(r, tmp_path)
    c = an.containers[str(r / "Backups/docs.7z")]
    assert c["status"] == "listed" and c["backend"] == "libarchive"
    assert c["suggestion"] == "already_extracted"


@pytest.mark.skipif(not (shutil.which("7zz") or shutil.which("7z") or shutil.which("7za")),
                    reason="7-Zip not installed")
def test_sevenzip_cli_listing(tmp_path):
    src = tmp_path / "src"
    w(src / "a.txt", b"hello")
    w(src / "sub/b.jpg", b"J" * 3000)
    zpath = tmp_path / "x.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(src / "a.txt", "src/a.txt")
        z.write(src / "sub/b.jpg", "src/sub/b.jpg")
    b = SevenZipBackend()
    assert b.available()
    members = {m.inner_path: m for m in b.list(str(zpath))}
    assert members["src/sub/b.jpg"].size == 3000 and members["src/sub/b.jpg"].crc32
    data = {m.inner_path: f.read() for m, f in b.streams(str(zpath))}
    assert data["src/a.txt"] == b"hello"
