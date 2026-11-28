"""Phase 3: HTML report and CSV exports."""

import csv
import html
import os
from datetime import datetime

from .analysis import SUGGESTIONS
from .layouts import ZONE_LABELS
from .util import human

E = html.escape

CSS = """
body{font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:24px auto;max-width:1200px;color:#222;padding:0 16px}
h1{font-size:22px}h2{font-size:17px;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{padding:4px 8px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}
th{background:#f6f6f6;position:sticky;top:0}td.n{text-align:right;white-space:nowrap}
.cards{display:flex;gap:12px;flex-wrap:wrap}.card{background:#f6f7f9;border-radius:8px;padding:10px 14px;flex:1 1 140px}
.card b{display:block;font-size:20px}.muted{color:#777}.bar{background:#e8eef7;height:8px;border-radius:4px}
.bar span{display:block;height:8px;background:#4a78c2;border-radius:4px}
code{font-size:12px;background:#f3f3f3;padding:1px 4px;border-radius:3px}
details{margin:6px 0}summary{cursor:pointer}input{padding:6px;width:320px;margin:6px 0}
.warn{background:#fff6e5;border-left:4px solid #e8a33d;padding:8px 12px}
"""

CLEANUP_LABELS = {
    "obsolete_software": "Obsolete software",
    "known_software": "Known OS and software files (public hash sets)",
    "old_installer": "Old stand-alone installers",
    "regenerable": "Regenerable dependencies and caches",
    "os_files": "Operating-system files (from copied system drives)",
    "installed_programs": "Installed programs (from copied system drives)",
    "cache_files": "Caches and temporary files",
    "duplicate": "Duplicates (another copy is kept)",
    "extracted_archive": "Archives whose content is already on disk",
    "trash": "Trash folders",
    "system": "Internal system folders",
    "system_junk": "System helper files (.DS_Store, ._*)",
}


def known_cell(st):
    """files.csv 'known' column: the set that knows the file, 'unknown', or empty if not looked up."""
    if not st:
        return ""
    return (st[1] or "known") if st[0] == "known" else st[0]


def write_csvs(an, out):
    rows = []
    for p, a in an.agg.items():
        rel = an.rel(p)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        top = sorted(a["sz"].items(), key=lambda kv: -kv[1])[:5]
        rows.append([rel, depth, a["n"], a["size"], human(a["size"]), an.folder_label(p),
                     "; ".join("%s: %d (%s)" % (c, a["cnt"][c], human(s)) for c, s in top),
                     (a["dmin"] or "")[:10], (a["dmax"] or "")[:10],
                     "yes" if p in an.units else "", a["dupn"], a["dupb"]])
    rows.sort(key=lambda r: r[0])
    an.folder_rows = rows
    with open(os.path.join(out, "folders.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["folder", "depth", "files", "bytes", "size", "folder_type", "main_content",
                    "date_min", "date_max", "unit_root", "duplicate_files", "duplicate_bytes"])
        w.writerows(rows)

    with open(os.path.join(out, "files.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "folder", "name", "category", "subtype", "zone", "source", "personal", "bytes", "date",
                    "date_source", "hash", "duplicate_of", "unit", "cleanup_proposed", "known"])
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in sorted(an.files, key=lambda r: r[1]):
            unit = an.unit_of_dir(d)
            source = an.file_source.get(fid)
            w.writerow([an.rel(path), an.rel(d), name, cat, sub or "", an.file_zone.get(fid, ""),
                        an.rel(an.source_name(source)) if source else "", "yes" if fid in an.personal_ids else "",
                        size, date or "", src or "", h or "",
                        an.rel(an.dup_of[fid]) if fid in an.dup_of else "",
                        an.rel(unit[0]) if unit else "", "yes" if fid in an.cleanup_files else "",
                        known_cell(an.known_status.get(fid))])

    with open(os.path.join(out, "duplicates.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "hash", "bytes", "copies", "reclaimable_bytes", "path", "keep"])
        for i, (h, size, members, keeper) in enumerate(an.dup_groups, 1):
            for m in sorted(members, key=lambda m: m[0] != keeper):
                w.writerow([i, h, size, len(members), (len(members) - 1) * size, an.rel(m[1]),
                            "yes" if m[0] == keeper else ""])

    with open(os.path.join(out, "cleanup_proposals.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["approve", "type", "path", "is_folder", "files", "bytes", "size",
                    "last_modified", "reason", "kept_copy"])
        for p in sorted(an.cleanup, key=lambda p: (p["type"], -p["bytes"])):
            w.writerow(["", p["type"], p["path"], "yes" if p["is_dir"] else "", p["files"], p["bytes"],
                        human(p["bytes"]), p["last_modified"], p["reason"], p["kept_copy"]])

    with open(os.path.join(out, "sources.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "description", "os", "os_version", "profiles", "files", "bytes", "personal_years",
                    "evidence"])
        for src in sorted(an.layout.sources.values(), key=lambda s: s.segs):
            y = src.years()
            ver = src.os_version["name"] if src.os_version else ""
            w.writerow([an.rel(an.source_name(src)), src.describe(), src.os, ver, "; ".join(src.profiles()), src.files,
                        src.bytes, "%d-%d" % (y[0], y[1]) if y else "", " | ".join(src.evidence())])

    with open(os.path.join(out, "containers.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "format", "backend", "status", "files", "uncompressed_bytes", "main_content",
                    "found_on_disk_share", "confidence", "extracted_at", "personal_files",
                    "personal_files_not_on_disk", "suggestion", "what_it_looks_like", "note"])
        for c in sorted(an.containers.values(), key=lambda c: c["path"]):
            top = sorted(c["sz"].items(), key=lambda kv: -kv[1])[:4]
            w.writerow([an.rel(c["path"]), c["format"], c["backend"] or "", c["status"], c["files"], c["size"],
                        "; ".join("%s: %d" % (k, c["cnt"][k]) for k, _ in top),
                        "%.2f" % c["found_share"], c["confidence"],
                        an.rel(c["extracted_at"]) if c["extracted_at"] else "", c["personal"],
                        sum(c["personal_missing"].values()), c["suggestion"], c.get("hint", ""), c["note"]])

    with open(os.path.join(out, "archive_members.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["container", "inner_path", "category", "zone", "bytes", "date", "crc32", "hash", "encrypted"])
        for row in an.con.execute("SELECT container, inner_path, category, size, date_best, crc32, full_hash, "
                                  "encrypted FROM members WHERE is_dir=0 ORDER BY container, inner_path"):
            zone = an.member_zone.get((row[0], row[1].replace("\\", "/")), "")
            w.writerow([an.rel(row[0]), row[1], row[2] or "", zone] + [x if x is not None else "" for x in row[3:7]]
                       + ["yes" if row[7] else ""])

    with open(os.path.join(out, "units.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "kind", "markers", "files", "bytes", "mbox_files", "mbox_bytes",
                    "date_min", "date_max"])
        notes = {(r[0], r[1]): r[3] for r in an.detections}
        for p in sorted(an.units):
            us = an.unit_stats.get(p, {})
            w.writerow([an.rel(p), an.units[p], notes.get((p, an.units[p]), ""), us.get("files", 0),
                        us.get("bytes", 0), us.get("mbox", 0), us.get("mbox_bytes", 0),
                        (us.get("dmin") or "")[:10], (us.get("dmax") or "")[:10]])


def write_html(an, out, depth):
    rel = lambda p: E(an.rel(p))  # noqa: E731
    cats = sorted(an.cat_tot, key=lambda c: -an.cat_tot[c][1])
    csum = an.cleanup_summary()
    clean_bytes = sum(v[2] for v in csum.values())
    con = an.con
    ms = an.cfg["dupes_min_size"]
    pending_hash = con.execute("""SELECT COUNT(*) FROM files WHERE quick_hash IS NULL AND size >= ? AND
        category!='system_junk' AND size IN (SELECT size FROM files WHERE category!='system_junk'
        AND size >= ? GROUP BY size HAVING COUNT(*)>1)""", (ms, ms)).fetchone()[0]
    n_err = con.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    date_src = con.execute("SELECT COALESCE(date_source,'no date'), COUNT(*) FROM files "
                           "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    long_videos = con.execute("SELECT path, size, duration, method FROM files WHERE category='video_long' "
                              "ORDER BY size DESC LIMIT 300").fetchall()
    biggest = con.execute("SELECT path, size, category FROM files ORDER BY size DESC LIMIT 50").fetchall()

    o = []
    a = o.append
    a("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>Inventory · %s</title>"
      "<style>%s</style></head><body>" % (E(an.root), CSS))
    a("<h1>Inventory of <code>%s</code></h1><p class='muted'>Generated %s · archivero</p>" % (
        E(an.root), datetime.now().strftime("%Y-%m-%d %H:%M")))
    a("<div class='cards'>")
    for label, val in [("Files", "{:,}".format(an.total_n)), ("Inventoried size", human(an.total_b)),
                       ("Proposed cleanup", human(clean_bytes)), ("In duplicates", human(an.dup_wasted)),
                       ("Projects / profiles", len(an.units)), ("Read errors", n_err)]:
        a("<div class='card'><span class='muted'>%s</span><b>%s</b></div>" % (E(label), E(str(val))))
    a("</div>")
    if pending_hash:
        a("<p class='warn'><b>%d</b> files share their size with others and have no hash yet. "
          "Run <code>archivero dupes</code> to confirm duplicates.</p>" % pending_hash)

    # Sources
    srcs = sorted(an.layout.sources.values(), key=lambda s: -s.bytes)
    a("<h2>Where this came from</h2>")
    if not srcs:
        a("<p class='muted'>No copied system drives or user profiles recognized.</p>")
    else:
        a("<p class='muted'>Copied system drives and user profiles, recognized from their folder layout even when "
          "only part was copied, and described from clues: layout era, vendor tools, drivers and file dates.</p>")
        a("<table><tr><th>Folder</th><th>What it looks like</th><th>Profiles</th><th class='n'>Files</th>"
          "<th class='n'>Size</th></tr>")
        for src in srcs[:100]:
            ev = "".join("<li>%s</li>" % E(e) for e in src.evidence())
            a("<tr><td>%s</td><td>%s<details><summary class='muted'>evidence</summary><ul>%s</ul></details></td>"
              "<td>%s</td><td class='n'>%d</td><td class='n'>%s</td></tr>" % (
                  rel(an.source_name(src)) or ".", E(src.describe()), ev, E(", ".join(src.profiles())),
                  src.files, human(src.bytes)))
        a("</table>")

    # Personal data
    a("<h2>Personal data</h2>")
    a("<table><tr><th>Zone</th><th class='n'>Files</th><th class='n'>Size</th></tr>")
    for z in ("user_data", "user_appdata", "unknown", "applications", "system", "cache", "trash"):
        if z in an.zone_tot:
            n, b = an.zone_tot[z]
            a("<tr><td>%s</td><td class='n'>%d</td><td class='n'>%s</td></tr>" % (E(ZONE_LABELS[z]), n, human(b)))
    a("</table><p>%d files look personal (photos, videos, music, documents, mail) and sit in personal or "
      "unrecognized folders.</p>" % len(an.personal_ids))
    if an.valuable:
        a("<details open><summary>Valuable application data (%d)</summary><ul>" % len(an.valuable))
        for path, label in an.valuable[:200]:
            a("<li>%s <span class='muted'>%s · %s</span></li>" % (
                rel(path), E(label), human(an.agg.get(path, {}).get("size", 0))))
        a("</ul></details>")
    if an.app_profiles:
        a("<details open><summary>Mail and browser profiles (%d)</summary><ul>" % len(an.app_profiles))
        for path, kind, text in an.app_profiles[:200]:
            a("<li>%s<br><span class='muted'>%s</span></li>" % (rel(path), E(text)))
        a("</ul></details>")
    if an.unusual_personal:
        a("<details><summary>Personal-looking files inside system or program folders (%d folders)</summary>"
          "<ul>" % len(an.unusual_personal))
        for d, n in an.unusual_personal.most_common(50):
            a("<li>%s <span class='muted'>%d files</span></li>" % (rel(d), n))
        a("</ul></details>")

    # Cleanup
    a("<h2>Proposed cleanup</h2><p class='muted'>Nothing is deleted automatically. Review "
      "<code>cleanup_proposals.csv</code>. Software counts as obsolete when untouched for %d years.</p>"
      % an.cfg["obsolete_years"])
    a("<table><tr><th>Type</th><th class='n'>Items</th><th class='n'>Files</th><th class='n'>Size</th></tr>")
    for ptype in CLEANUP_LABELS:
        if ptype in csum:
            n, nf, b = csum[ptype]
            a("<tr><td>%s</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%s</td></tr>" % (
                E(CLEANUP_LABELS[ptype]), n, nf, human(b)))
    a("</table>")
    sw = sorted((p for p in an.cleanup if p["type"] in ("obsolete_software", "old_installer")),
                key=lambda p: -p["bytes"])
    if sw:
        a("<details open><summary>Obsolete software and installers (%d)</summary><table><tr><th>Path</th>"
          "<th class='n'>Size</th><th>Last modified</th><th>Reason</th></tr>" % len(sw))
        for p in sw[:300]:
            a("<tr><td>%s%s</td><td class='n'>%s</td><td>%s</td><td class='muted'>%s</td></tr>" % (
                rel(p["path"]), "/" if p["is_dir"] else "", human(p["bytes"]), E(p["last_modified"]),
                E(p["reason"])))
        a("</table></details>")
    kn = sorted((p for p in an.cleanup if p["type"] == "known_software"), key=lambda p: -p["bytes"])
    if kn:
        a("<details open><summary>Known OS and software files (%d, %s)</summary><table><tr><th>Path</th>"
          "<th class='n'>Size</th><th>Reason</th></tr>" % (len(kn), human(sum(p["bytes"] for p in kn))))
        for p in kn[:300]:
            a("<tr><td>%s%s</td><td class='n'>%s</td><td class='muted'>%s</td></tr>" % (
                rel(p["path"]), "/" if p["is_dir"] else "", human(p["bytes"]), E(p["reason"])))
        a("</table></details>")
    reg = sorted((p for p in an.cleanup if p["type"] == "regenerable"), key=lambda p: -p["bytes"])
    if reg:
        a("<details><summary>Dependencies and caches (%d, %s)</summary><ul>" % (
            len(reg), human(sum(p["bytes"] for p in reg))))
        for p in reg[:300]:
            a("<li>%s <span class='muted'>%s · %d files</span></li>" % (rel(p["path"]), human(p["bytes"]),
                                                                       p["files"]))
        a("</ul></details>")

    # Known files
    a("<h2>Known files</h2>")
    if not an.n_known_hashed:
        a("<p class='muted'>Not checked yet. <code>archivero known --bloom hashlookup-full.bloom</code> recognizes "
          "files shipped by operating systems and software vendors (CIRCL hashlookup, NSRL), offline.</p>")
    else:
        st = [v[0] for v in an.known_status.values()]
        sets = ", ".join("%s%s" % (E(x.get("label", "")), " (%s entries, false-positive rate %g)" % (
            "{:,}".format(x["entries"]), x.get("false_positive_rate") or 0) if x.get("entries") else "")
            for x in an.known_sets) or "none yet"
        a("<p>%d contents hashed (SHA-1). Files on disk: <b>%d known</b>, %d not in the sets checked. "
          "Sets checked: %s.</p>" % (an.n_known_hashed, st.count("known"), st.count("unknown"), sets))
        if an.unknown_sw:
            a("<details><summary>Unknown executables in system or program folders (%d)</summary>"
              "<p class='muted'>No set lists them: custom or newer software, a localized build, or a tampered "
              "file. Worth a look before deleting a system drive.</p><ul>" % len(an.unknown_sw))
            for path, size in an.unknown_sw[:200]:
                a("<li>%s <span class='muted'>%s</span></li>" % (rel(path), human(size)))
            a("</ul></details>")

    # By type
    a("<h2>By content type</h2><table><tr><th>Category</th><th class='n'>Files</th>"
      "<th class='n'>Size</th><th style='width:40%'></th></tr>")
    tot = sum(v[1] for v in an.cat_tot.values()) or 1
    for c in cats:
        n, s = an.cat_tot[c]
        a("<tr><td>%s</td><td class='n'>%d</td><td class='n'>%s</td><td><div class='bar'>"
          "<span style='width:%.1f%%'></span></div></td></tr>" % (E(c), n, human(s), 100.0 * s / tot))
    a("</table><p class='muted'>Bracketed categories are folders that were not walked (measured in bulk).</p>")

    # By year
    top_cats = [c for c in cats if not c.startswith("[")][:8]
    a("<h2>By year (size)</h2><table><tr><th>Year</th>%s<th class='n'>Total</th></tr>"
      % "".join("<th class='n'>%s</th>" % E(c) for c in top_cats))
    for y in sorted(an.year_cat):
        row = an.year_cat[y]
        a("<tr><td>%s</td>%s<td class='n'>%s</td></tr>" % (
            E(y), "".join("<td class='n'>%s</td>" % (human(row[c]) if row.get(c) else "") for c in top_cats),
            human(sum(row.values()))))
    a("</table><p class='muted'>Date sources: %s. «mtime» is the modification date and may not match "
      "when the content was created.</p>" % ", ".join("%s %d" % (E(s), n) for s, n in date_src))

    # Email
    tb = [p for p in an.units if an.units[p] == "thunderbird_profile"]
    a("<h2>Email</h2>")
    if tb:
        a("<table><tr><th>Thunderbird profile</th><th class='n'>mbox files</th><th class='n'>mbox size</th>"
          "<th class='n'>Total</th><th>Dates</th></tr>")
        for p in sorted(tb):
            us = an.unit_stats.get(p, {})
            a("<tr><td>%s</td><td class='n'>%d</td><td class='n'>%s</td><td class='n'>%s</td>"
              "<td class='muted'>%s → %s</td></tr>" % (rel(p), us.get("mbox", 0), human(us.get("mbox_bytes", 0)),
                                                       human(us.get("bytes", 0)), E((us.get("dmin") or "")[:10]),
                                                       E((us.get("dmax") or "")[:10])))
        a("</table>")
    else:
        a("<p class='muted'>No Thunderbird profiles found.</p>")
    if an.loose_mail:
        a("<p>Email outside profiles: %s.</p>" % ", ".join(
            "%s: %d (%s)" % (E(k), v[0], human(v[1])) for k, v in sorted(an.loose_mail.items())))
    a("<p class='muted'>Looking inside mailboxes (messages, senders, dates) is phase 2 of the roadmap.</p>")

    # Archives and disk images
    a("<h2>Archives and disk images</h2>")
    n_cand = sum(1 for f in an.files if f[4] in ("compressed", "disk_image"))
    if not an.containers:
        a("<p class='muted'>%d archives or disk images found, not listed yet. Run <code>archivero archives list</code> "
          "to read their contents without extracting them.</p>" % n_cand)
    else:
        bysug = {}
        for c in an.containers.values():
            v = bysug.setdefault(c["suggestion"], [0, 0, 0])
            v[0] += 1
            v[1] += c["files"]
            v[2] += c["size"]
        a("<table><tr><th>Suggestion</th><th class='n'>Archives</th><th class='n'>Files inside</th>"
          "<th class='n'>Uncompressed</th></tr>")
        for sug in SUGGESTIONS:
            if sug in bysug:
                v = bysug[sug]
                a("<tr><td>%s</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%s</td></tr>" % (
                    E(SUGGESTIONS[sug]), v[0], v[1], human(v[2])))
        a("</table>")
        order = {k: i for i, k in enumerate(SUGGESTIONS)}
        cs = sorted(an.containers.values(), key=lambda c: (order.get(c["suggestion"], 99), -c["size"]))
        a("<details open><summary>Details (%d)</summary><table><tr><th>Archive</th><th class='n'>Files</th>"
          "<th class='n'>Uncompressed</th><th>Main content</th><th>On disk</th><th>Suggestion</th></tr>" % len(cs))
        for c in cs[:300]:
            top = sorted(c["cnt"].items(), key=lambda kv: -c["sz"][kv[0]])[:3]
            ondisk = ""
            if c["found_share"]:
                ondisk = "%.0f%% · %s" % (100 * c["found_share"], c["confidence"])
                if c["extracted_at"]:
                    ondisk += "<br><span class='muted'>%s</span>" % rel(c["extracted_at"])
            miss = sum(c["personal_missing"].values())
            sug = E(SUGGESTIONS.get(c["suggestion"], c["suggestion"]))
            if c["suggestion"] == "already_extracted" and not c["confidence"].startswith("confirmed"):
                sug += "<br><span class='muted'>matched by name and size only: run "
                sug += "<code>archives inspect</code> to confirm before deleting</span>"
            if miss:
                sug += "<br><span class='muted'>%d personal files not on disk: %s</span>" % (miss, E(", ".join(
                    "%s %d" % kv for kv in c["personal_missing"].most_common(4))))
            if c.get("hint"):
                sug += "<br><span class='muted'>%s</span>" % E(c["hint"])
            if c["note"]:
                sug += "<br><span class='muted'>%s</span>" % E(c["note"])
            a("<tr><td>%s <span class='muted'>%s</span></td><td class='n'>%d</td><td class='n'>%s</td>"
              "<td class='muted'>%s</td><td>%s</td><td>%s</td></tr>" % (
                  rel(c["path"]), E(c["format"] or ""), c["files"], human(c["size"]),
                  E(", ".join("%s %d" % kv for kv in top)), ondisk, sug))
        a("</table></details>")
        if n_cand > len(an.containers):
            a("<p class='muted'>%d more archives are not listed yet.</p>" % (n_cand - len(an.containers)))

    # Music
    a("<h2>Music</h2>")
    n_music_dirs = sum(1 for r in an.tags if r[1] == "music_folder")
    a("<p>%d audio files (%s) in %d music folders.</p>" % (
        an.music["files"], human(an.music["bytes"]), n_music_dirs))
    if an.music["artists"]:
        top = sorted(an.music["artists"].items(), key=lambda kv: -kv[1][0])[:50]
        a("<details><summary>Artists (from tags)</summary><ul>%s</ul></details>" % "".join(
            "<li>%s <span class='muted'>%d tracks · %s</span></li>" % (E(k), v[0], human(v[1])) for k, v in top))
    elif an.music["files"]:
        a("<p class='muted'>Run <code>scan --tags</code> (with mutagen installed) to read artist and album.</p>")

    # Folders
    a("<h2>Folders (up to depth %d)</h2>" % depth)
    a("<input placeholder='Filter folders…' oninput=\"for(const r of document.querySelectorAll('#ft tr.r'))"
      "r.style.display=r.textContent.toLowerCase().includes(this.value.toLowerCase())?'':'none'\">")
    a("<table id='ft'><tr><th>Folder</th><th>Type</th><th class='n'>Files</th><th class='n'>Size</th>"
      "<th>Dates</th><th>Main content</th><th class='n'>Duplicated</th></tr>")
    for r in an.folder_rows:
        if r[1] > depth:
            continue
        a("<tr class='r'><td>%s%s</td><td>%s</td><td class='n'>%d</td><td class='n'>%s</td>"
          "<td class='muted'>%s → %s</td><td class='muted'>%s</td><td class='n'>%s</td></tr>" % (
              "&nbsp;" * 4 * max(r[1] - 1, 0), E(r[0] if r[1] <= 1 else os.path.basename(r[0])), E(r[5]),
              r[2], r[4], E(r[7]), E(r[8]), E(r[6]), human(r[11]) if r[11] else ""))
    a("</table><p class='muted'>Full list in <code>folders.csv</code>.</p>")

    # Duplicates
    a("<h2>Confirmed duplicates (top 200 by space)</h2>")
    if not an.dup_groups:
        a("<p class='muted'>None (or <code>dupes</code> has not been run yet).</p>")
    for h, size, members, keeper in an.dup_groups[:200]:
        a("<details><summary>%d copies × %s → %s · <code>%s</code></summary><ul>" % (
            len(members), human(size), human((len(members) - 1) * size), E(os.path.basename(members[0][1]))))
        for m in sorted(members, key=lambda m: m[0] != keeper):
            a("<li>%s%s</li>" % (rel(m[1]), " <b>(keep)</b>" if m[0] == keeper else ""))
        a("</ul></details>")

    # Units
    proj = sorted(p for p in an.units if an.units[p] == "code_project")
    a("<h2>Code projects (%d)</h2><details><summary>Show list</summary><ul>" % len(proj))
    for p in proj[:1000]:
        a("<li>%s <span class='muted'>%s</span></li>" % (rel(p), human(an.agg.get(p, {}).get("size", 0))))
    a("</ul></details>")

    a("<h2>Long videos</h2><table><tr><th>File</th><th class='n'>Size</th><th class='n'>Duration</th>"
      "<th>Decided by</th></tr>")
    for path, size, dur, meth in long_videos:
        a("<tr><td>%s</td><td class='n'>%s</td><td class='n'>%s</td><td>%s</td></tr>" % (
            rel(path), human(size), ("%d min" % (dur / 60)) if dur else "", E(meth or "")))
    a("</table>")

    a("<h2>Largest files</h2><table><tr><th>File</th><th>Category</th><th class='n'>Size</th></tr>")
    for path, size, cat in biggest:
        a("<tr><td>%s</td><td>%s</td><td class='n'>%s</td></tr>" % (rel(path), E(cat), human(size)))
    a("</table></body></html>")
    with open(os.path.join(out, "report.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(o))


def report(an, out, depth=3):
    os.makedirs(out, exist_ok=True)
    write_csvs(an, out)
    write_html(an, out, depth)
    print("Report written to %s/: report.html, cleanup_proposals.csv, folders.csv, files.csv, "
          "duplicates.csv, units.csv, sources.csv, containers.csv, archive_members.csv" % out)
