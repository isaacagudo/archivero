"""Operating-system layout knowledge (pure data).

Everything here is matched case-insensitively against folder and file names, so it
works on partial copies: a backup that only kept "Documents and Settings" and
"Program Files", or a single user profile, is still recognized from what is there.

Contributors: add markers, zone rules or clues here; no other code needs to change.
"""

# -----------------------------------------------------------------------------------
# 1. Scopes: what kind of folder is this, judged by the names it contains.
#    Each marker adds a weight; a folder becomes a scope when the sum reaches the
#    threshold. Era markers vote for a period; arch markers for an architecture.
# -----------------------------------------------------------------------------------

SCOPES = {
    "win_root": {
        "os": "Windows", "label": "Windows system drive", "threshold": 3,
        "markers": {
            "windows": 2, "winnt": 2, "program files": 2, "program files (x86)": 2,
            "documents and settings": 3, "programdata": 2, "users": 1, "$recycle.bin": 2,
            "recycler": 2, "system volume information": 2, "pagefile.sys": 3, "hiberfil.sys": 3,
            "swapfile.sys": 3, "boot.ini": 3, "bootmgr": 3, "ntldr": 3, "msocache": 2,
            "perflogs": 2, "i386": 1, "windows.old": 3, "$windows.~bt": 2, "recovery": 1,
            "autoexec.bat": 1, "config.sys": 1, "io.sys": 1, "msdos.sys": 1,
            "swsetup": 1, "dell": 0.5, "drivers": 0.5, "intel": 0.5,
        },
        "era": {
            "documents and settings": "2000/XP", "boot.ini": "2000/XP", "ntldr": "2000/XP",
            "recycler": "2000/XP", "i386": "2000/XP", "winnt": "NT/2000",
            "users": "Vista or later", "programdata": "Vista or later", "$recycle.bin": "Vista or later",
            "bootmgr": "Vista or later", "perflogs": "Vista or later", "swapfile.sys": "8 or later",
            "$windows.~bt": "10 or later",
            "msdos.sys": "9x/ME", "io.sys": "9x/ME",
        },
        "arch": {"program files (x86)": "64-bit"},
    },
    "win_profile": {
        "os": "Windows", "label": "Windows user profile", "threshold": 3,
        "markers": {
            "ntuser.dat": 3, "ntuser.ini": 1, "appdata": 3, "application data": 3, "local settings": 3,
            "my documents": 2, "mis documentos": 2, "eigene dateien": 2, "mes documents": 2,
            "cookies": 1, "nethood": 2, "printhood": 2, "sendto": 1, "start menu": 1, "menú inicio": 1,
            "favorites": 0.5, "favoritos": 0.5, "contacts": 1, "saved games": 2, "searches": 1,
            "links": 0.5, "recent": 0.5, "templates": 0.5, "plantillas": 0.5,
        },
        "era": {
            "appdata": "Vista or later", "saved games": "Vista or later", "contacts": "Vista or later",
            "searches": "Vista or later", "links": "Vista or later",
            "application data": "2000/XP", "local settings": "2000/XP", "my documents": "2000/XP",
            "mis documentos": "2000/XP", "eigene dateien": "2000/XP", "mes documents": "2000/XP",
            "nethood": "2000/XP", "printhood": "2000/XP",
        },
    },
    "mac_root": {
        "os": "macOS", "label": "Mac system drive", "threshold": 4, "requires": {"applications", "private", "system"},
        "markers": {
            "system": 1, "applications": 2, "library": 1, "users": 1, "private": 2, "cores": 2,
            "volumes": 1, "sbin": 0.5, ".spotlight-v100": 2, ".fseventsd": 2, ".documentrevisions-v100": 2,
            ".vol": 2, "network": 0.5,
        },
        "era": {".documentrevisions-v100": "10.7 Lion or later"},
    },
    "mac_home": {
        "os": "macOS", "label": "Mac home folder", "threshold": 4, "requires": {"library", "movies"},
        "markers": {
            "library": 2, "movies": 2, "desktop": 0.5, "documents": 0.5, "downloads": 0.5, "music": 0.5,
            "pictures": 0.5, "public": 0.5, "sites": 1, ".trash": 2, ".cfusertextencoding": 2,
            "applications": 0.5,
        },
        "era": {"sites": "before 10.8"},
    },
    "linux_root": {
        "os": "Linux/Unix", "label": "Linux/Unix system drive", "threshold": 5,
        "markers": {
            "bin": 1, "sbin": 1, "etc": 1.5, "usr": 1.5, "var": 1.5, "home": 1, "boot": 1, "lib": 1,
            "lib64": 1, "opt": 0.5, "proc": 1, "sys": 1, "dev": 1, "srv": 0.5, "tmp": 0.5, "root": 1,
            "mnt": 0.5, "media": 0.5, "lost+found": 1, "vmlinuz": 2, "initrd.img": 2,
        },
        "era": {},
    },
    "linux_home": {
        "os": "Linux/Unix", "label": "Linux/Unix home folder", "threshold": 3,
        "markers": {
            ".bashrc": 2, ".profile": 1.5, ".bash_history": 2, ".bash_logout": 1, ".config": 1.5,
            ".local": 1.5, ".cache": 1, ".mozilla": 1, ".ssh": 1, ".gnupg": 1, ".thunderbird": 1,
            ".xsession-errors": 2, ".xinitrc": 1, ".gconf": 1, ".kde": 1, ".kde4": 1, ".gnome2": 1,
            ".zshrc": 2, ".viminfo": 1,
        },
        "era": {".gnome2": "GNOME 2 era (before ~2011)", ".kde": "KDE 3/4 era", ".gconf": "before ~2012"},
    },
}

# Folders that hold user profiles: a profile found under one of them implies that
# its grandparent was the system drive, even if nothing else of the drive was copied.
PROFILE_CONTAINERS = {"users": "Windows", "documents and settings": "Windows", "home": "Linux/Unix"}

ROOT_SCOPES = {"win_root", "mac_root", "linux_root"}
HOME_SCOPES = {"win_profile", "mac_home", "linux_home"}

# -----------------------------------------------------------------------------------
# 2. Zones. A rule is "<scope>:<relative/path>" or "any:<path>".
#    Scoped rules only match right below a folder detected as that scope; "any" rules
#    match anywhere and must therefore be specific. Segments may use * wildcards.
#    The deepest match wins.
# -----------------------------------------------------------------------------------

USER_DATA_DIRS = [
    "desktop", "escritorio", "documents", "documentos", "downloads", "descargas", "pictures", "imágenes",
    "imagenes", "music", "música", "musica", "videos", "vídeos", "movies", "favorites", "favoritos",
    "contacts", "contactos", "saved games", "onedrive", "dropbox", "google drive", "icloud drive",
    "public", "público", "my documents", "mis documentos", "eigene dateien", "mes documents",
]

ZONE_RULES = [
    # ---- Windows: system drive (anchored) ----
    ("win_root:windows", "system"), ("win_root:winnt", "system"),
    ("win_root:program files", "applications"), ("win_root:program files (x86)", "applications"),
    ("win_root:programdata", "system"), ("win_root:msocache", "system"), ("win_root:i386", "system"),
    ("win_root:$recycle.bin", "trash"), ("win_root:recycler", "trash"),
    ("win_root:system volume information", "system"), ("win_root:recovery", "system"),
    ("win_root:perflogs", "system"), ("win_root:$windows.~bt", "system"), ("win_root:$windows.~ws", "system"),
    ("win_root:boot", "system"), ("win_root:intel", "system"), ("win_root:drivers", "system"),
    ("win_root:dell", "system"), ("win_root:swsetup", "system"), ("win_root:temp", "cache"),
    ("win_root:pagefile.sys", "system"), ("win_root:hiberfil.sys", "system"), ("win_root:swapfile.sys", "system"),
    ("win_root:documents and settings/all users", "system"),
    ("win_root:documents and settings/default user", "system"),
    ("win_root:documents and settings/localservice", "system"),
    ("win_root:documents and settings/networkservice", "system"),
    ("win_root:users/default", "system"), ("win_root:users/default user", "system"),
    ("win_root:users/all users", "system"), ("win_root:users/public", "user_data"),
    ("win_root:windows.old/windows", "system"), ("win_root:windows.old/program files", "applications"),
    ("win_root:windows.old/program files (x86)", "applications"), ("win_root:windows.old/programdata", "system"),
    # ---- Windows: anywhere (specific enough) ----
    ("any:windows/system32", "system"), ("any:windows/syswow64", "system"), ("any:windows/winsxs", "system"),
    ("any:windows/installer", "system"), ("any:windows/assembly", "system"), ("any:windows/microsoft.net", "system"),
    ("any:windows/fonts", "system"), ("any:windows/softwaredistribution", "cache"), ("any:windows/temp", "cache"),
    ("any:program files", "applications"), ("any:program files (x86)", "applications"),
    ("any:program files/common files", "applications"),
    ("any:appdata/local", "user_appdata"), ("any:appdata/locallow", "user_appdata"),
    ("any:appdata/roaming", "user_appdata"),
    ("any:appdata/local/temp", "cache"), ("any:appdata/local/microsoft/windows/temporary internet files", "cache"),
    ("any:appdata/local/microsoft/windows/inetcache", "cache"),
    ("any:appdata/local/microsoft/windows/explorer", "cache"),
    ("any:appdata/local/microsoft/windows/history", "cache"),
    ("any:appdata/local/microsoft/windows/webcache", "cache"),
    ("any:appdata/local/mozilla/firefox/profiles", "cache"),
    ("any:appdata/local/google/chrome/user data/*/cache", "cache"),
    ("any:appdata/local/google/chrome/user data/*/code cache", "cache"),
    ("any:appdata/local/packages", "user_appdata"),
    ("any:appdata/roaming/microsoft/windows/recent", "system"),
    ("any:appdata/roaming/microsoft/windows/start menu", "system"),
    ("any:appdata/roaming/microsoft/windows/sendto", "system"),
    ("any:appdata/roaming/microsoft/windows/cookies", "cache"),
    ("any:application data", "user_appdata"), ("any:local settings/application data", "user_appdata"),
    ("any:local settings/temp", "cache"), ("any:local settings/temporary internet files", "cache"),
    ("any:local settings/history", "cache"),
    # ---- Windows: user profile (anchored) ----
    ("win_profile:cookies", "cache"), ("win_profile:nethood", "system"), ("win_profile:printhood", "system"),
    ("win_profile:recent", "system"), ("win_profile:sendto", "system"), ("win_profile:start menu", "system"),
    ("win_profile:menú inicio", "system"), ("win_profile:templates", "system"), ("win_profile:plantillas", "system"),
    ("win_profile:searches", "system"), ("win_profile:links", "system"),
    ("win_profile:ntuser.dat", "user_appdata"),
    # ---- macOS: system drive (anchored) ----
    ("mac_root:system", "system"), ("mac_root:library", "system"), ("mac_root:applications", "applications"),
    ("mac_root:private", "system"), ("mac_root:usr", "system"), ("mac_root:bin", "system"),
    ("mac_root:sbin", "system"), ("mac_root:cores", "system"), ("mac_root:.spotlight-v100", "system"),
    ("mac_root:.fseventsd", "system"), ("mac_root:.documentrevisions-v100", "system"),
    ("mac_root:.trashes", "trash"), ("mac_root:users/shared", "user_data"),
    # ---- macOS: home folder (anchored) ----
    ("mac_home:library", "user_appdata"), ("mac_home:applications", "applications"), ("mac_home:.trash", "trash"),
    ("mac_home:library/mobile documents", "user_data"),
    # ---- macOS: anywhere ----
    ("any:library/caches", "cache"), ("any:library/logs", "system"),
    ("any:library/application support/mobilesync/backup", "user_appdata"),
    ("any:*.photoslibrary", "user_data"), ("any:*.photoslibrary/resources/derivatives", "cache"),
    ("any:*.photoslibrary/resources/proxy", "cache"), ("any:*.photoslibrary/thumbnails", "cache"),
    ("any:iphoto library/thumbnails", "cache"), ("any:iphoto library/data", "cache"),
    # ---- Linux/Unix: system drive (anchored) ----
    ("linux_root:bin", "system"), ("linux_root:sbin", "system"), ("linux_root:lib", "system"),
    ("linux_root:lib32", "system"), ("linux_root:lib64", "system"), ("linux_root:usr", "system"),
    ("linux_root:boot", "system"), ("linux_root:dev", "system"), ("linux_root:proc", "system"),
    ("linux_root:sys", "system"), ("linux_root:run", "system"), ("linux_root:srv", "system"),
    ("linux_root:opt", "applications"), ("linux_root:etc", "system"), ("linux_root:var", "system"),
    ("linux_root:var/cache", "cache"), ("linux_root:var/tmp", "cache"), ("linux_root:tmp", "cache"),
    ("linux_root:var/mail", "user_appdata"), ("linux_root:var/spool/mail", "user_appdata"),
    ("linux_root:lost+found", "system"), ("linux_root:snap", "applications"),
    # ---- Linux/Unix: home (anchored) and anywhere ----
    ("linux_home:.config", "user_appdata"), ("linux_home:.local", "user_appdata"),
    ("linux_home:.mozilla", "user_appdata"), ("linux_home:.thunderbird", "user_appdata"),
    ("linux_home:.ssh", "user_appdata"), ("linux_home:.gnupg", "user_appdata"),
    ("linux_home:.steam", "applications"), ("linux_home:snap", "applications"),
    ("linux_home:templates", "system"), ("linux_home:plantillas", "system"),
    ("any:.cache", "cache"), ("any:.local/share/trash", "trash"), ("any:.thumbnails", "cache"),
    ("any:.wine/drive_c/windows", "system"),
    # ---- iPhone/iPad backups made by iTunes on Windows ----
    ("any:apple computer/mobilesync/backup", "user_appdata"),
]

# User folders are user_data right below any detected home/profile.
for _d in USER_DATA_DIRS:
    for _scope in ("win_profile", "mac_home", "linux_home"):
        ZONE_RULES.append(("%s:%s" % (_scope, _d), "user_data"))

ZONE_LABELS = {
    "user_data": "Personal files", "user_appdata": "Application data of a user",
    "cache": "Caches and temporary files", "system": "Operating system", "applications": "Installed programs",
    "trash": "Trash", "unknown": "Loose folders (no OS layout around them)",
}

# Mail and browser profiles: OS and era from where they sit. (path fragment, OS, era)
APP_PROFILE_PATHS = [
    ("appdata/roaming/mozilla/firefox/profiles", "Windows", "Vista or later"),
    ("application data/mozilla/firefox/profiles", "Windows", "2000/XP"),
    ("library/application support/firefox/profiles", "macOS", ""),
    (".mozilla/firefox", "Linux/Unix", ""),
    ("appdata/roaming/thunderbird/profiles", "Windows", "Vista or later"),
    ("application data/thunderbird/profiles", "Windows", "2000/XP"),
    ("library/thunderbird/profiles", "macOS", ""),
    (".thunderbird", "Linux/Unix", ""),
]

# High-value application data: where to look inside user_appdata.
VALUABLE_APPDATA = [
    ("mobilesync/backup", "iPhone/iPad backup"),
    ("thunderbird/profiles", "Thunderbird mail"), (".thunderbird", "Thunderbird mail"),
    ("mozilla/firefox/profiles", "Firefox profile"), (".mozilla/firefox", "Firefox profile"),
    ("application support/firefox/profiles", "Firefox profile"),
    ("google/chrome/user data", "Chrome profile"), ("microsoft/edge/user data", "Edge profile"),
    ("library/mail", "Apple Mail"), ("library/messages", "iMessage history"),
    ("microsoft/outlook", "Outlook data"), ("identities", "Outlook Express mail"),
    ("windows live mail", "Windows Live Mail"), ("microsoft/windows mail", "Windows Mail"),
    ("skype", "Skype history"), ("whatsapp", "WhatsApp data"), ("telegram desktop", "Telegram data"),
    (".ssh", "SSH keys (sensitive)"), (".gnupg", "GnuPG keys (sensitive)"),
    ("microsoft/crypto", "Windows certificates (sensitive)"), ("microsoft/protect", "Windows DPAPI keys (sensitive)"),
]

# -----------------------------------------------------------------------------------
# 3. Provenance clues: regular expressions matched against folder names and the names
#    of executables, drivers and installers. Each clue says what it suggests.
# -----------------------------------------------------------------------------------

VENDOR_CLUES = [
    # (vendor, regex, weight). Weight 2 = specific to that maker's computers.
    ("Dell", r"\bdell\b|dellsupport|quickset|dell ?datasafe|dell ?webcam|dell ?support ?center", 2),
    ("HP/Compaq", r"\bswsetup\b|hp ?support ?assistant|hp ?quick ?launch|hp ?recovery|\bcompaq\b|hpqwmi", 2),
    ("HP (printer)", r"hp ?(deskjet|officejet|laserjet|photosmart|envy)", 0.5),
    ("Lenovo", r"lenovo|thinkvantage|onekey ?recovery|lenovo ?vantage", 2),
    ("IBM/Lenovo ThinkPad", r"thinkpad|ibmtools|access ?ibm", 2),
    ("Toshiba", r"toshiba", 2),
    ("Sony", r"\bvaio\b|sony ?vaio|vaio ?care", 2),
    ("Acer", r"\bacer\b|empowering ?technology|erecovery|\bgateway\b|packard ?bell", 2),
    ("ASUS", r"\basus\b|live ?update.*asus|\batk ?package\b|eee ?pc", 2),
    ("Samsung", r"samsung ?(settings|recovery|update|easy)", 2),
    ("Fujitsu", r"fujitsu|siemens ?computers", 2),
    ("MSI", r"\bmsi\b ?(dragon|afterburner|center)", 1.5),
    ("Apple (Boot Camp)", r"boot ?camp|applewirelessmouse|apple ?keyboard ?support", 2),
]

_NUM = r"(?: ?[a-z]{0,2}-?\d{2,4}[a-z]{0,3})?"
MODEL_CLUES = [
    ("Dell", r"\b(?:latitude|inspiron|vostro|xps|precision|studio ?xps|alienware)%s\b" % _NUM),
    ("HP/Compaq", r"\b(?:pavilion(?: ?dv\d+)?|elitebook|probook|presario|envy|spectre|omen)%s\b" % _NUM),
    ("Lenovo", r"\b(?:thinkpad ?[a-z]?\d{0,4}|ideapad|yoga|legion|thinkcentre)%s\b" % _NUM),
    ("Toshiba", r"\b(?:satellite|tecra|portege|qosmio)%s\b" % _NUM),
    ("Sony", r"\bvaio(?: ?[a-z]{2,3}\d*)?\b"),
    ("Acer", r"\b(?:aspire|travelmate|extensa|swift)%s\b" % _NUM),
    ("ASUS", r"\b(?:zenbook|vivobook|eee ?pc|rog)%s\b" % _NUM),
    ("Apple", r"\b(?:macbook(?: ?(?:pro|air))?|imac|mac ?mini|mac ?pro)\b"),
]

LAPTOP_CLUES = r"synaptics|\balps\b|elantech|touch ?pad|quickset|power ?manager|battery|hotkey|\bfn\b|" \
               r"latitude|inspiron|vostro|thinkpad|ideapad|elitebook|probook|pavilion ?dv|satellite|tecra|" \
               r"\bvaio\b|aspire|travelmate|zenbook|vivobook|eee ?pc|macbook|bluetooth|wireless ?lan|wlan|" \
               r"intel.*(wifi|wireless|centrino)|atheros|broadcom.*(wireless|802)"

HARDWARE_CLUES = [
    ("NVIDIA graphics", r"nvidia|geforce"), ("AMD/ATI graphics", r"\bati\b|catalyst|radeon"),
    ("Intel graphics", r"intel.*(graphics|gma|hd ?graphics)"), ("Realtek audio/network", r"realtek"),
    ("Synaptics touchpad", r"synaptics"), ("Intel wireless", r"intel.*(wifi|wireless|centrino|proset)"),
    ("Atheros/Qualcomm wireless", r"atheros|qualcomm"), ("Broadcom wireless/Bluetooth", r"broadcom|widcomm"),
    ("Webcam software", r"webcam|cyberlink ?youcam|arcsoft"),
]

# Software that dates a machine. (regex, hint)
SOFTWARE_ERA_CLUES = [
    (r"outlook ?express", "Outlook Express (Windows 98–XP era)"),
    (r"\bwindows ?mail\b", "Windows Mail (Windows Vista)"),
    (r"windows ?live", "Windows Live Essentials (2007–2012)"),
    (r"msn ?messenger", "MSN Messenger (1999–2006)"),
    (r"\boffice ?11\b", "Microsoft Office 2003"), (r"\boffice ?12\b", "Microsoft Office 2007"),
    (r"\boffice ?14\b", "Microsoft Office 2010"), (r"\boffice ?15\b", "Microsoft Office 2013"),
    (r"\boffice ?16\b", "Microsoft Office 2016 or later"),
    (r"internet ?explorer ?6|\bie6\b", "Internet Explorer 6"),
    (r"nokia ?pc ?suite|nokia ?suite", "Nokia phone sync (2005–2012)"),
    (r"samsung ?kies", "Samsung Kies (2009–2015)"), (r"blackberry ?desktop", "BlackBerry Desktop (2006–2013)"),
    (r"\bpicasa\b", "Picasa (2004–2016)"), (r"windows ?xp ?mode", "Windows XP Mode (Windows 7)"),
    (r"adobe ?flash", "Adobe Flash (before 2021)"), (r"java ?6|jre6", "Java 6 (2006–2013)"),
    (r"windows ?media ?center", "Windows Media Center (XP MCE–7)"),
]

# File names worth keeping in the folder tree because they are markers (case-insensitive).
MARKER_FILES = {"ntuser.dat", "ntuser.ini", "pagefile.sys", "hiberfil.sys", "swapfile.sys", "boot.ini",
                "bootmgr", "ntldr", "autoexec.bat", "config.sys", "io.sys", "msdos.sys", "vmlinuz",
                "initrd.img", ".bashrc", ".profile", ".bash_history", ".bash_logout", ".xsession-errors",
                ".xinitrc", ".zshrc", ".viminfo", ".cfusertextencoding", "prefs.js"}

# -----------------------------------------------------------------------------------
# Files that state the exact OS version, relative to a system drive (lower case).
# 'scan' reads them when it meets them at these paths (osversion.py); the first one
# found, in this order, describes the drive.
# -----------------------------------------------------------------------------------
VERSION_FILES = [
    ("windows/system32/config/software", "registry"),   # HKLM\SOFTWARE hive: ProductName, build
    ("winnt/system32/config/software", "registry"),
    ("windows/system32/prodspec.ini", "prodspec"),       # Windows 2000/XP/2003
    ("winnt/system32/prodspec.ini", "prodspec"),
    ("system/library/coreservices/systemversion.plist", "plist"),
    ("system/library/coreservices/serverversion.plist", "plist"),
    ("etc/os-release", "os_release"),
    ("usr/lib/os-release", "os_release"),
    ("etc/lsb-release", "lsb_release"),
    ("etc/redhat-release", "release_line"),
    ("etc/centos-release", "release_line"),
    ("etc/fedora-release", "release_line"),
    ("etc/suse-release", "release_line"),
    ("etc/slackware-version", "release_line"),
    ("etc/gentoo-release", "release_line"),
    ("etc/debian_version", "debian_version"),
]

MAC_RELEASE_NAMES = {
    "10.0": "Cheetah", "10.1": "Puma", "10.2": "Jaguar", "10.3": "Panther", "10.4": "Tiger", "10.5": "Leopard",
    "10.6": "Snow Leopard", "10.7": "Lion", "10.8": "Mountain Lion", "10.9": "Mavericks", "10.10": "Yosemite",
    "10.11": "El Capitan", "10.12": "Sierra", "10.13": "High Sierra", "10.14": "Mojave", "10.15": "Catalina",
    "11": "Big Sur", "12": "Monterey", "13": "Ventura", "14": "Sonoma", "15": "Sequoia", "26": "Tahoe",
}
