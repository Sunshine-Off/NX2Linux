"""SDK functions the host replaces (NX2Linux S7bis): the Python side of the Mach host's HLE_FORCE table (engine/dm/host/hle_*.c). The recompiled game calls the SDK's own code except where a name is listed here:
that function is not lifted and its calls run `fn(hz, regs)`, which reads x0.. and writes x0 (the result). Each entry says why, as HLE_FORCE does. The list grows with what a game's boot needs, not in bulk."""

OK = 0
TABLE = {}   # symbol -> (fn(hz, r), why)


def hle(symbol, why):
    def deco(fn):
        if symbol in TABLE: raise ValueError(f'{symbol} replaced twice')
        TABLE[symbol] = (fn, why)
        return fn
    return deco


def noop(symbol, why):
    """A void function the host has nothing to do for (the real one connects to a Horizon service process)"""
    return hle(symbol, why)(lambda hz, r: hz.note(f'takeover: {symbol} (no-op)'))


def returns(symbol, why, value=0):
    return hle(symbol, why)(lambda hz, r: r.__setitem__(0, value))


# ---- hle_sdkinit.c: the service connections nninitInitializeSdkModule and nnMain make ----
noop('nndiagStartup', 'connect to the lm log service; the host prints diagnostic logs itself')
noop('_ZN2nn2oe10InitializeEv', 'connect to the am/oe environment services; environment queries are wired one by one to host defaults')
noop('_ZN2nn3aoc10InitializeEv', 'add-on content service: no session opened')
noop('_ZN2nn3err26InitializeContextWriterApiEv', 'connect to the err error context service; the host does not report')
noop('_ZN2nn2fs24InitializeForApplicationEv', 'connect to the fsp-srv file service; the file interface goes to the host file system and the RomFS')
noop('_ZN2nn4pctl10InitializeEv', 'connect to the pctl parental controls service; single-player has no parental controls')
noop('_ZN2nn4diag34InitializeApplicationAbortObserverEv', "register with the system's abort observer; the host prints crashes itself")


def _stub(symbol):
    def f(hz, r):
        n = hz.stub_calls[symbol] = hz.stub_calls.get(symbol, 0) + 1
        if n == 1: hz.log(f'[stub] {symbol} is not ported, answers 0 (x0={r[0]:#x} x1={r[1]:#x})')
        r[0] = 0
    return f


def install_profile(horizon, profile):
    """Per-title workarounds ({symbol: (fn, why)}), logged as such, added to the table for this run"""
    exported = set().union(*(m.exports for m in horizon.ms.modules))
    out = {}
    for sym, (fn, why) in profile.items():
        if sym not in exported: horizon.log(f'[profile] {sym} is not exported by this build: skipped'); continue
        horizon.log(f'[profile] workaround in place: {sym}: {why}')
        out[sym] = (lambda r, f=fn: f(horizon, r))
    return out


def install(horizon):
    """The table as {symbol: fn(regs)} for ModuleSet.build(overrides=...); symbols the loaded modules do not export are left out (the game may not use them)"""
    import horizon_fs, horizon_account, horizon_oe, horizon_vi, horizon_nvn, horizon_hid, horizon_fiber, horizon_audio, horizon_time   # register the nn::fs, nn::account, nn::oe, nn::vi/nv and NVN entries
    import horizon_trivial
    for sym, value, why, src in horizon_trivial.TRIVIAL:   # generated from the Mach host: entries that do nothing or return a constant; a hand-written port of the same symbol wins
        if sym in TABLE: continue
        TABLE[sym] = ((lambda hz, r: None) if value is None else (lambda hz, r, v=value: r.__setitem__(0, v)), why)
    for sym, why, src in horizon_trivial.STUBBED:   # not ported yet: answers 0, and the first call of each is logged
        if sym in TABLE: continue
        TABLE[sym] = (_stub(sym), f'not ported from {src}: answers 0 ({why})')
    exported = set().union(*(m.exports for m in horizon.ms.modules))
    return {sym: (lambda r, f=fn: f(horizon, r)) for sym, (fn, _) in TABLE.items() if sym in exported}
