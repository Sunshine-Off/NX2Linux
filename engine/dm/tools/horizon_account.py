"""nn::account on the Linux host (NX2Linux S7bis): the Python side of engine/dm/host/hle_account.c. One fixed local user; online accounts are out of scope and answer RES_FAIL.
Uid is 16 bytes, UserHandle 24 bytes (the Uid and a magic word); the nickname comes from NX2LINUX_NICK (default Player)."""
import os, struct

from horizon_overrides import hle, noop, returns

RES_OK, RES_FAIL = 0, 0x2A2
UID = struct.pack('<QQ', 0x78626c0000000001, 0x53574e4154495645)
HANDLE_MAGIC = 0x55534552484e444c


def handle_bytes(uid=UID): return uid + struct.pack('<Q', HANDLE_MAGIC)


noop('_ZN2nn7account10InitializeEv', 'connects to the acc:u0 account service; the host has one fixed local user')
noop('_ZN2nn7account13PushOpenUsersEv', 'hand the open users to the next program: the host runs only this one')
noop('_ZN2nn7account9CloseUserERKNS0_10UserHandleE', 'close user; the handle holds no session')


def _list(hz, r):   # (int *n, Uid *out, int max)
    n, out, cap = r[0], r[1], r[2] & 0xFFFFFFFF
    count = 1 if cap > 0 else 0
    if count: hz.wr(out, UID)
    hz.wr32(n, count); r[0] = RES_OK


for _s in ('_ZN2nn7account13ListOpenUsersEPiPNS0_3UidEi', '_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi', '_ZN2nn7account18ListQualifiedUsersEPiPNS0_3UidEi'):
    hle(_s, 'list of users; always the one local user')(_list)


@hle('_ZN2nn7account12PopOpenUsersEPiPNS0_10UserHandleEi', 'users opened by the system: none (the game falls back to the preselected user)')
def pop_open(hz, r): hz.wr32(r[0], 0)


@hle('_ZN2nn7account22TryOpenPreselectedUserEPNS0_10UserHandleE', 'user preselected at launch; always the local user (returns bool)')
def try_open_pre(hz, r): hz.wr(r[0], handle_bytes()); r[0] = 1


@hle('_ZN2nn7account19OpenPreselectedUserEPNS0_10UserHandleE', 'user preselected at launch (a Result in SDK 6); always the local user')
def open_pre(hz, r): hz.wr(r[0], handle_bytes()); r[0] = RES_OK


@hle('_ZN2nn7account17GetLastOpenedUserEPNS0_3UidE', 'last opened user; always the local user')
def last_opened(hz, r): hz.wr(r[0], UID); r[0] = RES_OK


@hle('_ZN2nn7account8OpenUserEPNS0_10UserHandleERKNS0_3UidE', 'open user; the handle holds the Uid itself')
def open_user(hz, r): hz.wr(r[0], handle_bytes(hz.rd(r[1], 16))); r[0] = RES_OK


@hle('_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE', 'gets the Uid from the handle')
def get_user_id(hz, r):
    h = hz.rd(r[1], 24)
    if struct.unpack_from('<Q', h, 16)[0] != HANDLE_MAGIC: raise RuntimeError('account::GetUserId: the handle was not issued by the host')
    hz.wr(r[0], h[:16]); r[0] = RES_OK


@hle('_ZN2nn7account11GetNicknameEPNS0_8NicknameERKNS0_3UidE', 'user nickname; taken from NX2LINUX_NICK')
def get_nickname(hz, r): hz.wr(r[0], os.environ.get('NX2LINUX_NICK', 'Player').encode()[:32].ljust(33, b'\0')); r[0] = RES_OK


@hle('_ZN2nn7account12GetUserCountEPi', 'user count; always the one local user')
def user_count(hz, r): hz.wr32(r[0], 1); r[0] = RES_OK


@hle('_ZN2nn7account16GetUserExistenceEPbRKNS0_3UidE', 'whether a user exists; true for the local user')
def user_exists(hz, r): hz.wr(r[0], bytes([hz.rd(r[1], 16) == UID])); r[0] = RES_OK


@hle('_ZN2nn7account32IsNetworkServiceAccountAvailableEPbRKNS0_10UserHandleE', 'network service account, out of scope; always unavailable')
def nsa_available(hz, r): hz.wr(r[0], b'\0'); r[0] = RES_OK


for _s in ('_ZN2nn7account36EnsureNetworkServiceAccountAvailableERKNS0_10UserHandleE', '_ZN2nn7account40ShowLicenseRequirementsForNetworkServiceERKNS0_10UserHandleE'):
    returns(_s, 'network service account, out of scope; cut', RES_FAIL)


@hle('_ZN2nn7account27StoreSaveDataThumbnailImageERKNS0_3UidEPKvm', "save-data thumbnail for the system's data management screen; the host keeps none")
def store_thumb(hz, r): r[0] = RES_OK


returns('_ZN2nn7account28DeleteSaveDataThumbnailImageERKNS0_3UidE', 'save-data thumbnail; the host keeps none')
