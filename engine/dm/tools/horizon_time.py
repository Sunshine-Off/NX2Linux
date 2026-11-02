"""nn::time on the Linux host (NX2Linux S7bis): the Python side of engine/dm/host/hle_time.c. The host has no time service: clocks are the host's own (user clock = the system clock, steady clock = CLOCK_MONOTONIC),
the time zone is the host's. Layouts (public Horizon ABI): PosixTime = s64 seconds; CalendarTime = {s16 year; s8 month, day, hour, minute, second; pad}; CalendarAdditionalInfo = {u32 wday, yday; char tz[8]; u32 dst; s32 offset};
SteadyClockTimePoint = {s64 seconds; 16-byte source id}. NX2LINUX_FIXED_TIME=<seconds> pins the user clock (deterministic runs)."""
import os, struct, time

from horizon_overrides import hle

RES_OK = 0
STEADY_SOURCE = b'nx2linux-steady1'


def user_time():
    f = os.environ.get('NX2LINUX_FIXED_TIME')
    return int(f, 0) if f else int(time.time())


def steady_seconds(): return int(time.monotonic())


hle('_ZN2nn4time10InitializeEv', 'connect to the time service; clocks come from the host')(lambda hz, r: r.__setitem__(0, RES_OK))
hle('_ZN2nn4time13IsInitializedEv', 'the time service is replaced, always initialized')(lambda hz, r: r.__setitem__(0, 1))


@hle('_ZN2nn4time19StandardSteadyClock19GetCurrentTimePointEPNS0_20SteadyClockTimePointE', 'steady clock: host monotonic seconds, fixed source id')
def steady_now(hz, r): hz.wr(r[0], struct.pack('<q', steady_seconds()) + STEADY_SOURCE); r[0] = RES_OK


@hle('_ZN2nn4time38GetStandardSteadyClockCurrentTimePointEPNS0_20SteadyClockTimePointE', 'steady clock (SDK 20 free function): host monotonic seconds')
def steady_now_fn(hz, r): steady_now(hz, r)


@hle('_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE', 'user clock: the host clock')
def user_now(hz, r): hz.wr64(r[0], user_time()); r[0] = RES_OK


@hle('_ZN2nn4time25AdjustableUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE', 'adjustable user clock: the host clock')
def adjustable_now(hz, r): user_now(hz, r)


@hle('_ZN2nn4time23StandardUserSystemClock3nowEv', 'user clock: the host clock')
def clock_now(hz, r): r[0] = user_time()


def _timespec(hz, addr, real):
    t = time.time_ns() if real else time.monotonic_ns()
    hz.wr(addr, struct.pack('<qq', t // 10**9, t % 10**9))


hle('_ZN2nn4time41GetStandardUserSystemClockCurrentTimespecEP8timespec', 'C library real-time clock: host real time')(lambda hz, r: (_timespec(hz, r[0], True), r.__setitem__(0, 0)))
hle('_ZN2nn4time43GetAdjustableUserSystemClockCurrentTimespecEP8timespec', 'C library real-time clock (adjustable): host real time')(lambda hz, r: (_timespec(hz, r[0], True), r.__setitem__(0, 0)))
hle('_ZN2nn4time37GetStandardSteadyClockCurrentTimespecEP8timespec', 'C library monotonic clock: host monotonic time')(lambda hz, r: (_timespec(hz, r[0], False), r.__setitem__(0, 0)))


def _calendar(hz, ct, ai, posix):
    tm = time.localtime(posix)
    if ct: hz.wr(ct, struct.pack('<hbbbbbb', tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0))
    if ai:
        hz.wr(ai, struct.pack('<II8sIi', (tm.tm_wday + 1) % 7, tm.tm_yday - 1, (tm.tm_zone or 'UTC').encode()[:7], int(tm.tm_isdst > 0), tm.tm_gmtoff or 0))


@hle('_ZN2nn4time14ToCalendarTimeEPNS0_12CalendarTimeEPNS0_22CalendarAdditionalInfoERKNS0_9PosixTimeE', 'time zone rules of the time service; the host zone')
def to_calendar(hz, r): _calendar(hz, r[0], r[1], hz.rd64(r[2])); r[0] = RES_OK


@hle('_ZN2nn4time14ToCalendarTimeEPNS0_12CalendarTimeEPNS0_22CalendarAdditionalInfoERKNS0_9PosixTimeERKNS0_12TimeZoneRuleE', 'explicit time zone rule: the host zone')
def to_calendar_rule(hz, r): _calendar(hz, r[0], r[1], hz.rd64(r[2])); r[0] = RES_OK


hle('_ZN2nn4time16LoadTimeZoneRuleEPNS0_12TimeZoneRuleERKNS0_12LocationNameE', 'time zone rule from the time service; the host zone is used instead')(lambda hz, r: r.__setitem__(0, RES_OK))
hle('_ZN2nn4time26StandardNetworkSystemClock20IsAccuracySufficientEv', 'network clock accuracy: offline, never sufficient')(lambda hz, r: r.__setitem__(0, 0))
