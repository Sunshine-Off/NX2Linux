"""nn::oe (the applet environment) on the Linux host (NX2Linux S7bis): the Python side of engine/dm/host/hle_oe.c. The recompiled game asks the SDK for the operation mode, focus, language and region;
the SDK would ask the am/apm/set services, so these functions are replaced with host answers. Settings come from the environment: NX2LINUX_OPMODE (1 docked, the default; 0 handheld), NX2LINUX_LANG (default en-US)."""
import os, struct

from horizon_overrides import hle

M32 = 0xFFFFFFFF


def opmode(): return int(os.environ.get('NX2LINUX_OPMODE', '1'))
def lang(): return os.environ.get('NX2LINUX_LANG', 'en-US') or 'en-US'


class OeState:
    def __init__(self): self.nosleep, self.inactivity, self.cpu_oc, self.focus = 0, 0, 0, 1


def state(hz):
    if not hasattr(hz, 'oe'): hz.oe = OeState()
    return hz.oe


@hle('_ZN2nn2oe16GetOperationModeEv', 'operation mode query; a fixed value from NX2LINUX_OPMODE (1 docked)')
def get_opmode(hz, r): r[0] = opmode()


@hle('_ZN2nn2oe18GetPerformanceModeEv', 'performance mode query; same as the operation mode')
def get_perfmode(hz, r): r[0] = opmode()


@hle('_ZN2nn2oe27GetDefaultDisplayResolutionEPiS1_', 'default display resolution: 1920x1080 docked, 1280x720 handheld')
def default_resolution(hz, r):
    con = opmode() == 1
    if r[0]: hz.wr32(r[0], 1920 if con else 1280)
    if r[1]: hz.wr32(r[1], 1080 if con else 720)


@hle('_ZN2nn2oe27GetPerformanceConfigurationENS0_15PerformanceModeE', 'CPU/GPU clock config (through apm): the default for the mode')
def perf_config(hz, r): r[0] = 0x00010001 if r[0] & M32 == 1 else 0x00010000


@hle('_ZN2nn2oe21IsCpuOverclockEnabledEv', 'CPU overclock state: what SetCpuOverclockEnabled set, off at start')
def cpu_oc(hz, r): r[0] = state(hz).cpu_oc


@hle('_ZN2nn2oe22SetCpuOverclockEnabledEb', 'CPU overclock switch; the host does not scale clocks')
def set_cpu_oc(hz, r): state(hz).cpu_oc = r[0] & 0xFF


@hle('_ZN2nn2oe20GetCurrentFocusStateEv', 'foreground / background state: always in focus')
def focus_state(hz, r): r[0] = state(hz).focus


@hle('_ZN2nn2oe25TryPopNotificationMessageEPj', 'system notification queue: the host has no system notifications')
def try_pop(hz, r): r[0] = 0


@hle('_ZN2nn2oe22PopNotificationMessageEv', 'system notification queue (blocking): nothing ever arrives, the thread waits')
def pop_blocking(hz, r):
    with hz.cv:
        while True: hz.cv.wait()


@hle('_ZN2nn2oe18GetDesiredLanguageEv', 'system language query; nn::settings::LanguageCode, 8 bytes in x0')
def desired_language(hz, r): r[0] = struct.unpack('<Q', lang().encode()[:7].ljust(8, b'\0'))[0]


@hle('_ZN2nn2oe13GetRegionCodeEv', 'console region fitting the language: 0 Japan, 1 Americas, 2 Europe, 4 Hong Kong / Taiwan / Korea')
def region_code(hz, r):
    e = lang()
    r[0] = 0 if e.startswith('ja') else 4 if e.startswith(('ko', 'zh')) else 1 if e in ('en-US', 'fr-CA', 'es-419', 'pt-BR') else 2


@hle('_ZN2nn2oe17GetPseudoDeviceIdEPNS_4util4UuidE', 'pseudo device ID; a fixed host value')
def pseudo_device_id(hz, r): hz.wr(r[0], bytes([0x78, 0x62, 0x6c, 0x00, 0x53, 0x57, 0x4e, 0x41, 0x54, 0x49, 0x56, 0x45, 0x31, 0x30, 0x30, 0x31]))


@hle('_ZN2nn2oe29BeginAutoSleepDisabledSectionEv', 'open an auto-sleep disabled section; counted for the query')
def nosleep_begin(hz, r): state(hz).nosleep += 1


@hle('_ZN2nn2oe27EndAutoSleepDisabledSectionEv', 'close an auto-sleep disabled section')
def nosleep_end(hz, r): state(hz).nosleep = max(0, state(hz).nosleep - 1)


@hle('_ZN2nn2oe19IsAutoSleepDisabledEv', 'whether an auto-sleep disabled section is open')
def nosleep_query(hz, r): r[0] = int(state(hz).nosleep > 0)


@hle('_ZN2nn2oe23GetPreviousProgramIndexEv', 'previous program index: launched from HOME, -1')
def previous_program(hz, r): r[0] = M32


@hle('_ZN2nn2oe21TryPopLaunchParameterEPmPvm', 'launch parameter: none')
def pop_launch_param(hz, r):
    if r[0]: hz.wr64(r[0], 0)
    r[0] = 0


@hle('_ZN2nn6applet23GetAppletResourceUserIdEv', 'applet resource user ID; the host returns a fixed non-zero value')
def aruid(hz, r): r[0] = 0x80


# switches the game sets and the host has nothing to scale, record or notify for
for _sym, _why in (('_ZN2nn2oe42SetOperationModeChangedNotificationEnabledEb', 'operation mode change notification switch'),
                   ('_ZN2nn2oe44SetPerformanceModeChangedNotificationEnabledEb', 'performance mode change notification switch'),
                   ('_ZN2nn2oe28SetResumeNotificationEnabledEb', 'resume notification switch'),
                   ('_ZN2nn2oe20SetFocusHandlingModeENS0_17FocusHandlingModeE', 'focus handling mode; the host window is always in focus'),
                   ('_ZN2nn2oe44SetUserInactivityDetectionTimeExtendedUnsafeEb', 'inactivity extension; the host never turns the screen off'),
                   ('_ZN2nn2oe38SetUserInactivityDetectionTimeExtendedEb', 'inactivity extension; the host never turns the screen off'),
                   ('_ZN2nn2oe27SetPerformanceConfigurationENS0_15PerformanceModeEi', 'CPU/GPU clock config; the host does not scale clocks')):
    hle(_sym, _why)(lambda hz, r: None)

hle('_ZN2nn2oe43GetUserInactivityDetectionTimeExtensionModeEv', 'inactivity extension mode: none')(lambda hz, r: r.__setitem__(0, 0))
hle('_ZN2nn2oe37IsUserInactivityDetectionTimeExtendedEv', 'whether an inactivity extension is requested: no')(lambda hz, r: r.__setitem__(0, 0))
