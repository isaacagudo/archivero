# Reading an old NAS over NFS as another UID

NFS v3 asks for no user name or password. The server checks the client's IP and then
the **numeric UID** of every process that touches a file. If your folder on the NAS
belongs to UID 1000 with `drwx------` permissions and your local user has a different
UID, you cannot read it.

## Option A: Linux (simplest)

If your Linux user has the same UID as the one on the NAS (check with `id -u`):

```bash
sudo apt install nfs-common
sudo mkdir -p /mnt/nas_home
sudo mount -t nfs -o vers=3 NAS_IP:/c/home /mnt/nas_home
archivero scan /mnt/nas_home/alice --db alice.db
```

In a virtual machine, use **bridged** networking. Old NFS servers often refuse
connections that come through NAT.

## Option B: macOS

1. Mount **outside your home folder**. `~` has `750` permissions, so a foreign UID
   could not even reach the mount point:

   ```bash
   mkdir -p /tmp/nas_home
   sudo mount -t nfs -o resvport,vers=3,tcp,rsize=32768,wsize=32768,nolocks NAS_IP:/c/home /tmp/nas_home
   ```

2. Run the commands as the NAS UID. macOS `sudo -u '#1000'` refuses UIDs that do not
   exist locally, so use a tiny `perl` wrapper:

   ```bash
   cd /Users/Shared   # a folder UID 1000 can enter
   sudo perl -e '$( = 100; $) = "100 100"; $< = $> = 1000; exec @ARGV or die "exec: $!"' \
     python3 -m archivero scan /tmp/nas_home/alice --db /Users/Shared/alice.db
   ```

   The database and reports must go to a folder that UID can write to, such as
   `/Users/Shared`.

3. When done, take ownership of the output:
   `sudo chown -R $(whoami) /Users/Shared/alice.db* /Users/Shared/report`.
