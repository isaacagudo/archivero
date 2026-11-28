"""Media index (ADR 0005) and non-personal flags (ADR 0006)."""

import io
import os
import shutil
import sqlite3
import subprocess
import zipfile

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from archivero import config  # noqa: E402
from archivero.archives import list_containers  # noqa: E402
from archivero.cli import main  # noqa: E402
from archivero.db import SCHEMA_VERSION, open_db  # noqa: E402
from archivero.media_index import build_media_index, hash_bytes  # noqa: E402
from archivero.scanner import scan  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def photo(w=800, h=600, color=(200, 80, 40), exif=None, fmt="JPEG"):
    im = Image.new("RGB", (w, h), color)
    # Some structure, so the dHash is not all zeros
    for x in range(0, w, 7):
        for y in range(0, h, 50):
            im.putpixel((x, y), (x % 256, y % 256, 0))
    im.paste((10, 10, 200), (w // 4, h // 4, w // 2, h // 2))
    buf = io.BytesIO()
    kw = {"exif": exif} if exif is not None else {}
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def camera_exif():
    ex = Image.Exif()
    ex[0x010F] = "Canon"
    ex[0x0110] = "Canon EOS 5D"
    ex[0x0112] = 6  # rotated 90° clockwise
    sub = ex.get_ifd(0x8769)
    sub[0x9003] = "2019:08:12 10:10:10"
    sub[0x9011] = "+02:00"
    sub[0xA431] = "123456"
    sub[0xA434] = "EF 24-105mm f/4L"
    gps = ex.get_ifd(0x8825)
    gps.update({1: "N", 2: (40.0, 25.0, 1.5), 3: "W", 4: (3.0, 42.0, 0.0)})
    return ex


def w(root, rel, data):
    p = os.path.join(str(root), *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "nas"
    cam = photo(exif=camera_exif())
    w(r, "Photos/2019/IMG_0001.jpg", cam)
    w(r, "Photos backup/IMG_0001.jpg", cam)                    # identical copy
    # A "web" export: rotated in the pixels, resized, metadata stripped
    small = Image.open(io.BytesIO(cam)).transpose(Image.ROTATE_270).resize((300, 400))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=70)
    w(r, "Photos/2019/IMG_0001_web.jpg", buf.getvalue())       # near duplicate (resized)
    w(r, "Photos/2019/drawing.png", photo(640, 480, (30, 160, 90), fmt="PNG"))
    w(r, "Photos/2019/IMG_0003.HEIC", b"\x00\x00\x00\x18ftypheic" + os.urandom(2000))
    w(r, "Photos/2019/broken.jpg", b"\xff\xd8\xff\xe0 not really a jpeg" * 10)
    w(r, "Photos/2019/avatar.png", photo(48, 48, (1, 2, 3), fmt="PNG"))     # tiny, no camera
    w(r, "Program/skin/icons/app.png", photo(300, 300, (9, 9, 9), fmt="PNG"))  # icon folder
    w(r, "Sample Pictures/Koala.jpg", photo(1024, 768, (120, 120, 0)))      # stock picture
    w(r, "Photos/2019/Koala.jpg", photo(1024, 768, (120, 120, 1), exif=camera_exif()))  # a real koala
    # A RAW file that Pillow cannot decode, with a JPEG preview inside
    w(r, "Photos/2019/IMG_0002.CR3", b"\x00\x00\x00\x18ftypcrx " + os.urandom(500) +
      photo(1200, 800, (50, 50, 50)) + os.urandom(300))
    # An archive with a picture found nowhere else and a copy of IMG_0001
    zp = os.path.join(str(r), "Backups", "old.zip")
    os.makedirs(os.path.dirname(zp))
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Pictures/2007/DSC0001.JPG", photo(640, 480, (0, 90, 200)))
        z.writestr("Pictures/2007/IMG_0001.jpg", cam)
    if HAS_FFMPEG:
        out = os.path.join(str(r), "Photos", "2019", "VID_0001.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                        "-t", "2", "-metadata", "creation_time=2019-08-12T08:10:10Z",
                        "-metadata", "location=+40.4168-003.7038/", "-pix_fmt", "yuv420p", out], check=True)
    return r


def snapshot(root):
    return sorted((os.path.relpath(os.path.join(d, f), root), os.path.getsize(os.path.join(d, f)),
                   os.path.getmtime(os.path.join(d, f))) for d, _, fs in os.walk(root) for f in fs)


def rows(con):
    cur = con.execute("SELECT * FROM media")
    cols = [c[0] for c in cur.description]
    return {r[0]: dict(zip(cols, r)) for r in cur}


def by_name(con, tree, rel):
    h = con.execute("SELECT full_hash FROM files WHERE path=?", (os.path.join(str(tree), *rel.split("/")),)).fetchone()[0]
    return rows(con)[h]


def test_media_index(tree, tmp_path):
    cfg = config.load()
    before = snapshot(str(tree))
    db = str(tmp_path / "inv.db")
    con = open_db(db)
    scan(con, str(tree), cfg)
    list_containers(con, cfg)
    build_media_index(con, cfg, archives=True)

    # Read-only: the source is untouched
    assert snapshot(str(tree)) == before

    media = rows(con)
    img = by_name(con, tree, "Photos/2019/IMG_0001.jpg")
    assert img["kind"] == "image" and img["format"] == "JPEG" and img["status"] == "ok"
    assert (img["width"], img["height"]) == (600, 800)  # orientation 6 applied
    assert img["orientation"] == 6
    assert img["date_taken"] == "2019-08-12 10:10:10" and img["date_source"] == "exif_original"
    assert img["date_offset"] == "+02:00"
    assert img["gps_lat"] == pytest.approx(40.4170833, abs=1e-6) and img["gps_lon"] == pytest.approx(-3.7)
    assert (img["make"], img["model"], img["serial"]) == ("Canon", "Canon EOS 5D", "123456")
    assert img["lens"] == "EF 24-105mm f/4L"
    assert len(img["dhash"]) == 16 and img["nonpersonal"] == 0

    # Thumbnail: content-addressed WebP beside the database, 320 px on the longest side
    thumb = os.path.join(db + ".media", *img["thumb"].split("/"))
    assert img["thumb"] == "%s/%s/%s.webp" % (img["hash"][:2], img["hash"][2:4], img["hash"])
    with Image.open(thumb) as t:
        assert t.format == "WEBP" and max(t.size) == 320 and t.size[1] > t.size[0]

    # One row per unique content; every copy is reachable through the view
    inst = con.execute("SELECT origin, path FROM media_instances WHERE hash=?", (img["hash"],)).fetchall()
    assert sorted(o for o, _ in inst) == ["file", "file", "member"]
    assert con.execute("SELECT COUNT(*) FROM media WHERE hash=?", (img["hash"],)).fetchone()[0] == 1

    # Near duplicate: same picture resized, close dHash
    web = by_name(con, tree, "Photos/2019/IMG_0001_web.jpg")
    dist = bin(int(web["dhash"], 16) ^ int(img["dhash"], 16)).count("1")
    assert web["hash"] != img["hash"] and dist <= 10

    # RAW through its embedded preview
    raw = by_name(con, tree, "Photos/2019/IMG_0002.CR3")
    assert raw["kind"] == "raw" and raw["status"] == "ok" and (raw["width"], raw["height"]) == (1200, 800)
    assert raw["thumb"]

    # Broken file: an error row, so it is not read again
    broken = by_name(con, tree, "Photos/2019/broken.jpg")
    assert broken["status"] == "error" and broken["error"]

    # HEIC without pillow-heif: hashed, kept as partial until the decoder is installed
    heic = by_name(con, tree, "Photos/2019/IMG_0003.HEIC")
    try:
        import pillow_heif  # noqa: F401  (only checks it is installed)
    except ImportError:
        assert (heic["status"], heic["missing"]) == ("partial", "heif")
    else:
        assert heic["status"] == "error"  # random bytes

    # Picture found only inside the archive
    member_hash = hash_bytes(zipfile.ZipFile(os.path.join(str(tree), "Backups", "old.zip"))
                             .read("Pictures/2007/DSC0001.JPG"))
    assert media[member_hash]["status"] == "ok" and media[member_hash]["thumb"]

    # ADR 0006 flags
    assert by_name(con, tree, "Photos/2019/drawing.png")["nonpersonal"] == 0
    assert by_name(con, tree, "Photos/2019/avatar.png")["nonpersonal_why"] == "tiny"
    assert "icon_folder" in by_name(con, tree, "Program/skin/icons/app.png")["nonpersonal_why"]
    koala = by_name(con, tree, "Sample Pictures/Koala.jpg")
    assert koala["nonpersonal"] == 1 and "stock_name" in koala["nonpersonal_why"]
    assert "wallpaper_or_sample_folder" in koala["nonpersonal_why"]
    assert by_name(con, tree, "Photos/2019/Koala.jpg")["nonpersonal"] == 0  # has a camera: personal

    if HAS_FFMPEG:
        vid = by_name(con, tree, "Photos/2019/VID_0001.mp4")
        assert vid["kind"] == "video" and vid["status"] == "ok"
        assert (vid["width"], vid["height"]) == (320, 240) and vid["video_codec"]
        assert vid["duration"] == pytest.approx(2.0, abs=0.2)
        assert vid["date_taken"] == "2019-08-12 08:10:10" and vid["date_offset"] == "+00:00"
        assert vid["gps_lat"] == pytest.approx(40.4168) and vid["gps_lon"] == pytest.approx(-3.7038)
        assert vid["thumb"]

    # Resumable: a second run reads nothing and changes nothing
    stamps = {h: r["indexed_at"] for h, r in rows(con).items()}
    stats = build_media_index(con, cfg, archives=True)
    assert not stats.get("ok") and not stats.get("error")
    assert {h: r["indexed_at"] for h, r in rows(con).items()} == stamps
    con.close()


def test_cli(tree, tmp_path):
    db = str(tmp_path / "inv.db")
    main(["scan", str(tree), "--db", db, "--media"])
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    assert n >= 9
    # Members are only indexed when asked (--archives)
    assert con.execute("SELECT COUNT(*) FROM media_instances WHERE origin='member'").fetchone()[0] == 0
    con.close()
    main(["archives", "list", "--db", db])
    main(["media", "--db", db, "--archives"])
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM media_instances WHERE origin='member'").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == n + 1  # DSC0001 was only in the zip
    con.close()


def test_video_without_ffmpeg_is_partial_then_completed(tree, tmp_path, monkeypatch):
    if not HAS_FFMPEG:
        pytest.skip("needs ffmpeg")
    import archivero.media_index as mi
    real_which = shutil.which
    monkeypatch.setattr(mi.shutil, "which", lambda name: None if name.startswith("ff") else real_which(name))
    cfg = config.load()
    con = open_db(str(tmp_path / "inv.db"))
    scan(con, str(tree), cfg)
    build_media_index(con, cfg)
    vid = by_name(con, tree, "Photos/2019/VID_0001.mp4")
    assert (vid["status"], vid["missing"], vid["thumb"]) == ("partial", "ffmpeg", None)
    monkeypatch.setattr(mi.shutil, "which", real_which)
    stats = build_media_index(con, cfg)
    assert stats["ok"] == 1  # only the video is processed again
    assert by_name(con, tree, "Photos/2019/VID_0001.mp4")["status"] == "ok"


def test_migration_from_schema_1(tmp_path):
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    con.commit()
    con.close()
    con = open_db(db)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
    assert con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
