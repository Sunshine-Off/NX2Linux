"""nn::fs on the Linux host (NX2Linux S7bis): the Python side of engine/dm/host/hle_fs.c. On a Switch every call goes through the fsp-srv service; here the SDK's functions are replaced (horizon_overrides.py)
and read the host's files. Mount name -> backend:
  rom   the RomFS image romfs_image.py packs (read-only, one descriptor, pread)
  save  a folder under $XDG_DATA_HOME/nx2linux/saves/<title id> (writable)
  temp  a folder under $XDG_CACHE_HOME/nx2linux/temp/<title id> (writable)
Paths are "name:/dir/file". A FileHandle / DirectoryHandle is 8 bytes the guest passes by value: here a number shifted left by 4, so the low bits are clear as a heap pointer's are (a game may mask them).
Results follow Horizon: 0 is success, 0x202 path not found, 0x402 path already exists."""
import os, shutil, struct
from pathlib import Path

import romfs_image
from horizon_overrides import hle, noop, returns

RES_OK, RES_NOT_FOUND, RES_EXISTS, RES_INVALID_PATH, RES_READ_ONLY = 0, 0x202, 0x402, 0x2EE02, 0x2F202
ENT_DIR, ENT_FILE = 0, 1
DIRMODE_DIR, DIRMODE_FILE = 1, 2
MODE_READ, MODE_WRITE, MODE_APPEND = 1, 2, 4
DIRENT = 0x310


def xdg(var, fallback):
    return Path(os.environ.get(var) or Path.home() / fallback)


class RomImage:
    def __init__(self, path):
        self.path = path
        self.index = romfs_image.read_index(path)
        self.fd = os.open(path, os.O_RDONLY)
        self.kids = {}
        for p in sorted(self.index):
            self.kids.setdefault(p.rpartition('/')[0], []).append(p)

    def stat(self, p):
        p = p.strip('/')
        if p == '': return ENT_DIR, 0, 0
        e = self.index.get(p)
        return None if e is None else (ENT_DIR if e[0] else ENT_FILE, e[1], e[2])

    def read(self, p, off, n):
        t = self.stat(p)
        base, size = t[1], t[2]
        n = max(0, min(n, size - off))
        return os.pread(self.fd, n, base + off) if n else b''

    def listing(self, p):
        p = p.strip('/')
        return [(c.rpartition('/')[2], self.index[c]) for c in self.kids.get(p, [])]


class HostDir:
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, p): return self.root / p.strip('/')

    def stat(self, p):
        h = self._p(p)
        if h.is_dir(): return ENT_DIR, 0, 0
        if h.is_file(): return ENT_FILE, 0, h.stat().st_size
        return None

    def read(self, p, off, n):
        with open(self._p(p), 'rb') as f: f.seek(off); return f.read(n)

    def listing(self, p):
        h = self._p(p)
        return [(c.name, (c.is_dir(), 0, 0 if c.is_dir() else c.stat().st_size)) for c in sorted(h.iterdir())] if h.is_dir() else []


class FileSystem:
    def __init__(self, hz, romfs_img, title_id):
        self.hz, self.mounts, self.files, self.dirs, self._next = hz, {}, {}, {}, 1
        self.romfs_img = romfs_img
        self.save_root = xdg('XDG_DATA_HOME', '.local/share') / 'nx2linux/saves' / title_id
        self.temp_root = xdg('XDG_CACHE_HOME', '.cache') / 'nx2linux/temp' / title_id

    def split(self, path):
        name, sep, rest = path.partition(':')
        return (name, rest) if sep else (None, path)

    def resolve(self, path):
        name, rest = self.split(path)
        m = self.mounts.get(name)
        return (m, rest, name) if m else (None, rest, name)

    def mount_rom(self, name):
        if self.romfs_img is None: return RES_NOT_FOUND
        self.mounts[name] = RomImage(self.romfs_img) if not hasattr(self, '_rom') else self._rom
        self._rom = self.mounts[name]
        return RES_OK

    def handle(self, table, obj):
        h = self._next; self._next += 1; table[h] = obj
        return h << 4


def fs(hz): return hz.fs


def cstr(hz, a): return hz.cstring(a, 0x300)


@hle('_ZN2nn2fs8MountRomEPKcPvm', 'fsp-srv; rom: the RomFS image on the host')
def mount_rom(hz, r): r[0] = hz.fs.mount_rom(cstr(hz, r[0]))


@hle('_ZN2nn2fs8MountRomEPKc', 'fsp-srv; rom: the RomFS image on the host (overload without the cache arguments)')
def mount_rom1(hz, r): r[0] = hz.fs.mount_rom(cstr(hz, r[0]))


@hle('_ZN2nn2fs22QueryMountRomCacheSizeEPm', 'fsp-srv; rom reads the host image directly, the cache only has to exist')
def rom_cache_size(hz, r): hz.wr64(r[0], 0x10000); r[0] = RES_OK


@hle('_ZN2nn2fs7UnmountEPKc', 'fsp-srv; the mount table is permanent, unmounting is a no-op')
def unmount(hz, r): pass


@hle('_ZN2nn2fs9IsMountedEPKc', 'fsp-srv; looks up the host mount table')
def is_mounted(hz, r): r[0] = int(cstr(hz, r[0]) in hz.fs.mounts)


@hle('_ZN2nn2fs13IsPathMountedEPKc', 'fsp-srv; looks up the host mount table')
def is_path_mounted(hz, r): r[0] = int(hz.fs.split(cstr(hz, r[0]))[0] in hz.fs.mounts)


@hle('_ZN2nn2fs21MountTemporaryStorageEPKc', 'fsp-srv; temporary storage is a host directory')
def mount_temp(hz, r):
    hz.fs.mounts[cstr(hz, r[0])] = HostDir(hz.fs.temp_root); r[0] = RES_OK


def _mount_save(hz, name):
    hz.fs.mounts[name] = HostDir(hz.fs.save_root)
    return RES_OK


@hle('_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE', 'fsp-srv; save data is a host directory (single user)')
def mount_save(hz, r): r[0] = _mount_save(hz, cstr(hz, r[0]))


@hle('_ZN2nn2fs19MountDeviceSaveDataEPKc', 'fsp-srv; device save data is the same host directory (one player)')
def mount_device_save(hz, r): r[0] = _mount_save(hz, cstr(hz, r[0]))


returns('_ZN2nn2fs14EnsureSaveDataERKNS_7account3UidE', 'fsp-srv; the save directory is created at mount time')
returns('_ZN2nn2fs18IsSaveDataExistingERKNS_7account3UidE', 'fsp-srv; save data always exists', 1)
returns('_ZN2nn2fs18IsSaveDataExistingENS_13ApplicationIdERKNS_7account3UidE', 'fsp-srv; save data always exists', 1)
returns('_ZN2nn2fs6detail18IsEnabledAccessLogEj', 'fsp-srv; access log always off')
returns('_ZN2nn2fs6detail18IsEnabledAccessLogEv', 'fsp-srv; access log always off')
noop('_ZN2nn2fs26SetPriorityOnCurrentThreadENS0_8PriorityE', 'fsp-srv; file read priority is ignored by the host')
noop('_ZN2nn2fs25EnableGlobalFileDataCacheEPvm', 'fsp-srv global read cache; unused by the host')
noop('_ZN2nn2fs26DisableGlobalFileDataCacheEv', 'fsp-srv global read cache; unused by the host')
returns('_ZN2nn2fs6CommitEPKc', 'fsp-srv; the host directory is written in place')
returns('_ZN2nn2fs14CommitSaveDataEPKc', 'fsp-srv; same as Commit')


def _sizes(hz, r):   # (int64 *size, int64 *journal, ...): 256 MB of data and 256 MB of journal, the host directory has no limit
    hz.wr64(r[0], 256 << 20); hz.wr64(r[1], 256 << 20); r[0] = RES_OK


for _sym in ('_ZN2nn2fs15GetSaveDataSizeEPlS1_RKNS_7account3UidE', '_ZN2nn2fs18GetSaveDataSizeMaxEPlS1_', '_ZN2nn2fs21GetDeviceSaveDataSizeEPlS1_'):
    hle(_sym, 'fsp-srv; save data size: 256 MB data + 256 MB journal')(_sizes)


@hle('_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci', 'fsp-srv -> open')
def open_file(hz, r):
    out, path, mode = r[0], cstr(hz, r[1]), r[2] & 0xFFFFFFFF
    m, rest, _ = hz.fs.resolve(path)
    if m is None: r[0] = RES_NOT_FOUND; return
    st = m.stat(rest)
    if mode & (MODE_WRITE | MODE_APPEND):
        if isinstance(m, RomImage): r[0] = RES_READ_ONLY; return
        if st is None: r[0] = RES_NOT_FOUND; return
    elif st is None or st[0] != ENT_FILE: r[0] = RES_NOT_FOUND; return
    hz.wr64(out, hz.fs.handle(hz.fs.files, (m, rest, mode))); r[0] = RES_OK


def _read(hz, out, h, off, buf, size):
    ent = hz.fs.files.get(h >> 4)
    if ent is None: return RES_INVALID_PATH
    m, rest, _ = ent
    data = m.read(rest, off, size)
    if data: hz.wr(buf, data)
    if out: hz.wr64(out, len(data))
    return RES_OK if out or len(data) == size else 0x2EE02 + 0   # without an out count a short read is an error


@hle('_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm', 'fsp-srv -> pread')
def read_file(hz, r): r[0] = _read(hz, r[0], r[1], r[2], r[3], r[4])


@hle('_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvmRKNS0_10ReadOptionE', 'fsp-srv -> pread (overload with read options)')
def read_file_opt(hz, r): r[0] = _read(hz, r[0], r[1], r[2], r[3], r[4])


@hle('_ZN2nn2fs8ReadFileENS0_10FileHandleElPvm', 'fsp-srv -> pread, must read in full')
def read_file_full(hz, r): r[0] = _read(hz, 0, r[0], r[1], r[2], r[3])


@hle('_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE', 'fsp-srv -> fstat')
def get_file_size(hz, r):
    ent = hz.fs.files.get(r[1] >> 4)
    if ent is None: r[0] = RES_INVALID_PATH; return
    m, rest, _ = ent
    hz.wr64(r[0], m.stat(rest)[2]); r[0] = RES_OK


@hle('_ZN2nn2fs9CloseFileENS0_10FileHandleE', 'fsp-srv -> close')
def close_file(hz, r): hz.fs.files.pop(r[0] >> 4, None)


@hle('_ZN2nn2fs15GetFileOpenModeENS0_10FileHandleE', 'fsp-srv; the open mode is recorded by the host')
def file_open_mode(hz, r):
    ent = hz.fs.files.get(r[0] >> 4); r[0] = ent[2] if ent else 0


@hle('_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE', 'fsp-srv -> pwrite')
def write_file(hz, r):
    ent = hz.fs.files.get(r[0] >> 4)
    if ent is None: r[0] = RES_INVALID_PATH; return
    m, rest, _ = ent
    if isinstance(m, RomImage): r[0] = RES_READ_ONLY; return
    data = hz.rd(r[2], r[3])
    with open(m._p(rest), 'r+b') as f: f.seek(r[1]); f.write(data)
    r[0] = RES_OK


noop('_ZN2nn2fs9FlushFileENS0_10FileHandleE', 'fsp-srv; host writes are already in the page cache')


@hle('_ZN2nn2fs10CreateFileEPKcl', 'fsp-srv -> create + truncate')
def create_file(hz, r):
    m, rest, _ = hz.fs.resolve(cstr(hz, r[0]))
    if m is None: r[0] = RES_NOT_FOUND; return
    if isinstance(m, RomImage): r[0] = RES_READ_ONLY; return
    p = m._p(rest)
    if p.exists(): r[0] = RES_EXISTS; return
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'wb') as f: f.truncate(r[1])
    r[0] = RES_OK


@hle('_ZN2nn2fs15CreateDirectoryEPKc', 'fsp-srv -> mkdir')
def create_dir(hz, r):
    m, rest, _ = hz.fs.resolve(cstr(hz, r[0]))
    if m is None: r[0] = RES_NOT_FOUND; return
    if isinstance(m, RomImage): r[0] = RES_READ_ONLY; return
    p = m._p(rest)
    if p.exists(): r[0] = RES_EXISTS; return
    p.mkdir(parents=True); r[0] = RES_OK


@hle('_ZN2nn2fs10DeleteFileEPKc', 'fsp-srv -> unlink')
def delete_file(hz, r):
    m, rest, _ = hz.fs.resolve(cstr(hz, r[0]))
    if m is None or isinstance(m, RomImage) or not m._p(rest).is_file(): r[0] = RES_NOT_FOUND; return
    m._p(rest).unlink(); r[0] = RES_OK


@hle('_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc', 'fsp-srv -> stat')
def get_entry_type(hz, r):
    m, rest, _ = hz.fs.resolve(cstr(hz, r[1]))
    st = m.stat(rest) if m else None
    if st is None: r[0] = RES_NOT_FOUND; return
    hz.wr32(r[0], st[0]); r[0] = RES_OK


@hle('_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci', 'fsp-srv -> opendir')
def open_dir(hz, r):
    out, path, mode = r[0], cstr(hz, r[1]), r[2] & 0xFFFFFFFF
    m, rest, _ = hz.fs.resolve(path)
    st = m.stat(rest) if m else None
    if st is None or st[0] != ENT_DIR: r[0] = RES_NOT_FOUND; return
    entries = [(n, e) for n, e in m.listing(rest) if (mode & DIRMODE_DIR if e[0] else mode & DIRMODE_FILE)]
    hz.wr64(out, hz.fs.handle(hz.fs.dirs, [entries, 0])); r[0] = RES_OK


@hle('_ZN2nn2fs13ReadDirectoryEPlPNS0_14DirectoryEntryENS0_15DirectoryHandleEl', 'fsp-srv -> readdir')
def read_dir(hz, r):
    out_count, buf, h, cap = r[0], r[1], r[2], r[3]
    d = hz.fs.dirs.get(h >> 4)
    if d is None: r[0] = RES_INVALID_PATH; return
    entries, pos = d
    take = entries[pos:pos + max(0, cap)]
    for i, (name, (is_dir, _, size)) in enumerate(take):
        raw = name.encode()[:0x300]
        hz.wr(buf + i * DIRENT, raw.ljust(0x301, b'\0') + b'\0\0\0' + struct.pack('<b3xq', ENT_DIR if is_dir else ENT_FILE, 0 if is_dir else size))
    d[1] = pos + len(take)
    hz.wr64(out_count, len(take)); r[0] = RES_OK


@hle('_ZN2nn2fs14CloseDirectoryENS0_15DirectoryHandleE', 'fsp-srv -> closedir')
def close_dir(hz, r): hz.fs.dirs.pop(r[0] >> 4, None)


@hle('_ZN2nn2fs22GetDirectoryEntryCountEPlNS0_15DirectoryHandleE', 'fsp-srv; entry count of an open directory')
def dir_count(hz, r):
    d = hz.fs.dirs.get(r[1] >> 4)
    if d is None: r[0] = RES_INVALID_PATH; return
    hz.wr64(r[0], len(d[0])); r[0] = RES_OK
