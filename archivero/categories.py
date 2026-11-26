"""File classification by name and extension."""

import os
import re

CATEGORY_EXT = {
    "image": "jpg jpeg png gif bmp tif tiff heic heif webp jfif",
    "raw": "cr2 cr3 crw nef nrw arw srf sr2 dng orf rw2 raf pef srw x3f",
    "video": "mp4 m4v mov avi mkv wmv mpg mpeg mpe 3gp 3g2 mts m2ts ts vob flv webm divx ogv asf rm rmvb",
    "audio": "mp3 wav flac aac m4a ogg oga opus wma aif aiff alac mid midi amr ape mpc wv dsf",
    "pdf": "pdf",
    "document": "doc docx docm odt rtf txt md pages tex bib epub mobi",
    "spreadsheet": "xls xlsx xlsm ods csv tsv numbers",
    "presentation": "ppt pptx pps ppsx odp key",
    "code": ("py ipynb js mjs cjs ts tsx jsx java kt kts scala c h cc cpp cxx hpp hh cs go rs rb php "
             "pl pm sh bash zsh ps1 bat cmd html htm css scss sass less vue svelte sql r m mat swift "
             "dart lua sol vy hs erl ex exs clj groovy gradle json yml yaml toml xml "
             "xsd wsdl proto asm s v vhd vhdl cmake"),
    "compressed": "zip rar 7z tar gz tgz bz2 tbz xz txz lz lzma z",
    "disk_image": "iso dmg img vmdk vdi qcow2 ova ovf vhdx toast nrg bin cue",
    "executable": "exe msi msp cab app pkg mpkg deb rpm apk jar war dll so dylib sys drv inf",
    "email": "eml emlx mbox pst ost msg",
    "database": "db sqlite sqlite3 mdb accdb dbf frm ibd",
    "design": "psd psb ai eps svg sketch fig xcf indd idml cdr dwg dxf skp blend fbx obj stl",
    "certificate": "pem crt cer der p12 pfx key csr jks keystore",
    "config": "ini cfg conf plist properties reg",
}

SYSTEM_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".localized", "icon\r",
                     "ehthumbs.db", ".directory", ".bridgecache", ".bridgecachet"}

SPECIAL_NAMES = {"makefile": "code", "dockerfile": "code", "cmakelists.txt": "code",
                 "gemfile": "code", "rakefile": "code", "vagrantfile": "code"}

# Typical Thunderbird mbox names (no extension), including localized ones.
MBOX_NAMES = {"inbox", "sent", "drafts", "trash", "templates", "archives", "junk", "outbox",
              "unsent messages", "sent messages", "deleted messages",
              # es
              "borradores", "enviados", "papelera", "plantillas", "archivos", "correo no deseado",
              "bandeja de entrada",
              # de / fr / it / pt
              "gesendet", "entwürfe", "papierkorb", "envoyés", "brouillons", "corbeille",
              "posta inviata", "bozze", "cestino", "enviadas", "rascunhos", "lixo"}

EMAIL_SUBTYPES = {"mbox": "mbox", "eml": "eml", "emlx": "eml", "pst": "pst", "ost": "pst", "msg": "msg"}

MEDIA_CATS = {"image", "raw", "video_short", "video_long"}
DOC_CATS = {"pdf", "document", "spreadsheet", "presentation"}
SOFTWARE_CATS = {"executable", "disk_image"}
# A compressed file only counts as software when its name clearly suggests it.
SOFTWARE_NAME_RE = re.compile(
    r"(setup|install|instal|driver|update|upgrade|patch|firmware|portable|x86|x64|win32|win64|"
    r"amd64|i386|macos|osx|linux|\.app\b)", re.I)

_EXT2CAT = {}


def build_ext_map(extra=None):
    """Build the extension -> category map (plus extensions added via config)."""
    _EXT2CAT.clear()
    for cat, exts in CATEGORY_EXT.items():
        for e in exts.split():
            _EXT2CAT.setdefault(e, cat)
    for cat, exts in (extra or {}).items():
        for e in exts:
            _EXT2CAT[e] = cat


build_ext_map()


def classify(name, sibling_names_low=None, in_mail_profile=False):
    """Return (extension, category, subtype).

    sibling_names_low: lower-cased names in the same folder, used to recognize
    Thunderbird mbox files (a file with no extension next to a same-named .msf).
    """
    low = name.lower()
    if low in SYSTEM_JUNK_NAMES or low.startswith("._"):
        return ("", "system_junk", None)
    if low in SPECIAL_NAMES:
        return (low, SPECIAL_NAMES[low], None)
    if low.endswith(".sql.gz"):
        return ("sql.gz", "database", None)
    ext = os.path.splitext(low)[1].lstrip(".")
    if ext == "msf":
        return ("msf", "email", "thunderbird_index")
    if sibling_names_low and (low + ".msf") in sibling_names_low:
        return (ext, "email", "mbox")
    if not ext:
        if in_mail_profile and low in MBOX_NAMES:
            return ("", "email", "mbox")
        return ("", "no_extension", None)
    cat = _EXT2CAT.get(ext, "other")
    return (ext, cat, EMAIL_SUBTYPES.get(ext) if cat == "email" else None)


def is_software(category, name):
    return category in SOFTWARE_CATS or (category == "compressed" and bool(SOFTWARE_NAME_RE.search(name)))
