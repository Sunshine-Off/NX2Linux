"""nn::audio on the Linux host, silent (NX2Linux S7bis): the audio renderer and the audio output as the game's client API sees them, with nothing behind them but bookkeeping. The Mach host runs the SDK's own
renderer server on the CPU (hle_audren.c); here every call that would reach the audio services is replaced, the objects are tracked by the guest address of their opaque struct, and time passes as it would:
the renderer's system event is signalled every `sampleCount / sampleRate` seconds (the game's audio thread waits on it), a started voice consumes its wave buffers at the sample rate and releases them, memory pools
attach at once, an audio-out buffer is released after the time its samples would take. No sound is produced. Layouts used: AudioRendererParameter {u32 sampleRate; u32 sampleCount}, WaveBuffer {ptr; size;
s32 startSampleOffset; s32 endSampleOffset; bool loop}, AudioOutBuffer {ptr; capacity; size} (the SDK keeps nothing else in the first 24 bytes)."""
import os, struct, threading, time

from horizon_overrides import hle

RATE = 48000
POOL_DETACHED, POOL_ATTACHED = 3, 5   # MemoryPoolType::State: Invalid, New, RequestDetach, Detached, RequestAttach, Attached, Released
PLAY_START = 0
M32 = 0xFFFFFFFF


class Voice:
    def __init__(self, rate): self.rate, self.playing, self.waves, self.released, self.consumed, self.played = rate, False, [], [], 0, 0


class Audio:
    def __init__(self, hz):
        self.hz, self.lock = hz, threading.Lock()
        self.voices, self.pools, self.delays, self.outs = {}, {}, {}, {}
        self.event_wh, self.ticker, self.tick_samples, self.rate, self.running = 0, None, 240, RATE, False

    def start_ticker(self):
        if self.ticker: return
        self.running = True
        self.ticker = threading.Thread(target=self.run, name='audren-tick', daemon=True); self.ticker.start()

    def run(self):
        hz, period = self.hz, self.tick_samples / self.rate
        nxt = time.monotonic()
        while self.running:
            nxt += period
            self.tick()
            delay = nxt - time.monotonic()
            if delay > 0: time.sleep(delay)
            else: nxt = time.monotonic()

    def tick(self):
        with self.lock:
            for v in self.voices.values():
                if not v.playing or not v.waves: continue
                v.consumed += self.tick_samples; v.played += self.tick_samples
                while v.waves and v.waves[0][1] is not None and v.consumed >= v.waves[0][1]:
                    wb, dur = v.waves.pop(0); v.consumed -= dur; v.released.append(wb)
        ev = self.hz.handles.get(self.event_wh)
        if ev is not None:
            with self.hz.cv: ev.signaled = True; self.hz.cv.notify_all()


def state(hz):
    if not hasattr(hz, 'audio'): hz.audio = Audio(hz)
    return hz.audio


def ret(r, v=0): r[0] = v & 0xFFFFFFFFFFFFFFFF


def reg(symbol, why):
    """Register an nn::audio replacement; NX2LINUX_AUDIO_LOG=1 logs each call with its arguments and its result"""
    deco = hle(symbol, 'silent nn::audio: ' + why)
    def wrap(fn):
        def traced(hz, r):
            args = [hex(x) for x in r[:4]]
            fn(hz, r)
            if os.environ.get('NX2LINUX_AUDIO_LOG'): hz.log(f'[audio] {symbol.split("audio")[-1][:50]}({", ".join(args)}) -> {r[0]:#x}')
        deco(traced)
        return fn
    return wrap


# ---- sizes the game allocates before it opens the renderer ----
reg('_ZN2nn5audio30GetAudioRendererWorkBufferSizeERKNS0_22AudioRendererParameterE', 'no server work buffer is needed; small enough for the 1 MiB pools the game carves it from')(lambda hz, r: ret(r, 0x60000))
reg('_ZN2nn5audio36GetAudioRendererConfigWorkBufferSizeERKNS0_22AudioRendererParameterE', 'the config is bookkeeping on the host; a generous size')(lambda hz, r: ret(r, 0x30000))
reg('_ZN2nn5audio29IsValidAudioRendererParameterERKNS0_22AudioRendererParameterE', 'every parameter is accepted')(lambda hz, r: ret(r, 1))
reg('_ZN2nn5audio29InitializeAudioRendererConfigEPNS0_19AudioRendererConfigERKNS0_22AudioRendererParameterEPvm', 'the config is tracked by the host')(lambda hz, r: ret(r))
reg('_ZN2nn5audio29GetRequiredBufferSizeForDelayENS_8TimeSpanEii', 'delay line size: the time, in samples of 4 bytes per channel, with room to spare')(
    lambda hz, r: ret(r, ((r[0] * (r[1] & M32) // 10**9 * (r[2] & M32) * 4 * 2 + 0x2000) + 0x3F) & ~0x3F))
reg('_ZN2nn5audio35GetRequiredBufferSizeForI3dl2ReverbEii', 'reverb state size: a generous constant')(lambda hz, r: ret(r, 0x40000))


# ---- the renderer ----
@reg('_ZN2nn5audio17OpenAudioRendererEPNS0_19AudioRendererHandleEPNS_2os11SystemEventERKNS0_22AudioRendererParameterEPvm', 'the renderer is a token; its system event ticks every sampleCount / sampleRate seconds')
def open_renderer(hz, r):
    a = state(hz); handle, ev, param = r[0], r[1], r[2]
    a.rate, a.tick_samples = (hz.rd32(param) or RATE), (hz.rd32(param + 4) or 240)
    hz.wr(handle, struct.pack('<QQ', 0xA0D10000, 0))
    if ev:   # the SDK's own CreateSystemEvent (auto clear, inter-process) makes the kernel event; the host signals its writable end
        hz.call_guest('_ZN2nn2os17CreateSystemEventEPNS0_15SystemEventTypeENS0_14EventClearModeEb', {'x0': ev, 'x1': 1, 'x2': 1})
        a.event_wh = hz.call_guest('_ZN2nn2os30GetWritableHandleOfSystemEventEPKNS0_15SystemEventTypeE', {'x0': ev}) & M32
    a.start_ticker(); ret(r)


reg('_ZN2nn5audio18StartAudioRendererENS0_19AudioRendererHandleE', 'nothing to start')(lambda hz, r: ret(r))
reg('_ZN2nn5audio17StopAudioRendererENS0_19AudioRendererHandleE', 'nothing to stop')(lambda hz, r: ret(r))
reg('_ZN2nn5audio26RequestUpdateAudioRendererENS0_19AudioRendererHandleEPKNS0_19AudioRendererConfigE', 'the update is accepted; voices and pools are advanced by the host')(lambda hz, r: ret(r))


@reg('_ZN2nn5audio18CloseAudioRendererENS0_19AudioRendererHandleE', 'stops the host tick')
def close_renderer(hz, r): state(hz).running = False; state(hz).ticker = None


# ---- mixing graph: accepted, nothing is mixed ----
reg('_ZN2nn5audio15AcquireFinalMixEPNS0_19AudioRendererConfigEPNS0_12FinalMixTypeEi', 'bool: acquired')(lambda hz, r: ret(r, 1))
for _s in ('_ZN2nn5audio13AddDeviceSinkEPNS0_19AudioRendererConfigEPNS0_14DeviceSinkTypeEPNS0_12FinalMixTypeEPKaiPKc',
           '_ZN2nn5audio14AddBufferMixerEPNS0_19AudioRendererConfigEPNS0_15BufferMixerTypeEPNS0_12FinalMixTypeE',
           ):
    reg(_s, 'Result: success')(lambda hz, r: ret(r))


@reg('_ZN2nn5audio8AddDelayEPNS0_19AudioRendererConfigEPNS0_9DelayTypeEPvmPNS0_12FinalMixTypeENS_8TimeSpanEi', 'remembers the delay line the game gave, RemoveDelay hands it back')
def add_delay(hz, r): state(hz).delays[r[1]] = r[2]; ret(r)


@reg('_ZN2nn5audio14AddI3dl2ReverbEPNS0_19AudioRendererConfigEPNS0_15I3dl2ReverbTypeEPvmPNS0_12FinalMixTypeEi', 'remembers the reverb buffer the game gave, RemoveI3dl2Reverb hands it back')
def add_reverb(hz, r): state(hz).delays[r[1]] = r[2]; ret(r)


reg('_ZN2nn5audio11RemoveDelayEPNS0_19AudioRendererConfigEPNS0_9DelayTypeEPNS0_12FinalMixTypeE', 'returns the buffer given to AddDelay')(lambda hz, r: ret(r, state(hz).delays.pop(r[1], 0)))
reg('_ZN2nn5audio17RemoveI3dl2ReverbEPNS0_19AudioRendererConfigEPNS0_15I3dl2ReverbTypeEPNS0_12FinalMixTypeE', 'returns the buffer given to AddI3dl2Reverb')(lambda hz, r: ret(r, state(hz).delays.pop(r[1], 0)))
reg('_ZN2nn5audio17RemoveBufferMixerEPNS0_19AudioRendererConfigEPNS0_15BufferMixerTypeEPNS0_12FinalMixTypeE', 'nothing to remove')(lambda hz, r: ret(r))
for _s in ('_ZN2nn5audio16IsDelayRemovableEPKNS0_9DelayTypeE', '_ZN2nn5audio22IsBufferMixerRemovableEPKNS0_15BufferMixerTypeE', '_ZN2nn5audio22IsI3dl2ReverbRemovableEPKNS0_15I3dl2ReverbTypeE'):
    reg(_s, 'always removable')(lambda hz, r: ret(r, 1))
_SETTERS = ('SetDelayTimeEPNS0_9DelayTypeENS_8TimeSpanE', 'SetDelayInGainEPNS0_9DelayTypeEf', 'SetDelayDryGainEPNS0_9DelayTypeEf', 'SetDelayEnabledEPNS0_9DelayTypeEb', 'SetDelayFeedbackGainEPNS0_9DelayTypeEf',
           'SetDelayChannelSpreadEPNS0_9DelayTypeEf', 'SetDelayLowPassAmountEPNS0_9DelayTypeEf', 'SetDelayInputOutputEPNS0_9DelayTypeEPKaS4_i', 'SetBufferMixerVolumeEPNS0_15BufferMixerTypeEif',
           'SetBufferMixerEnabledEPNS0_15BufferMixerTypeEb', 'SetBufferMixerInputOutputEPNS0_15BufferMixerTypeEPKaS4_i', 'SetI3dl2ReverbDryGainEPNS0_15I3dl2ReverbTypeEf', 'SetI3dl2ReverbEnabledEPNS0_15I3dl2ReverbTypeEb',
           'SetI3dl2ReverbRoomGainEPNS0_15I3dl2ReverbTypeEf', 'SetI3dl2ReverbReverbGainEPNS0_15I3dl2ReverbTypeEf', 'SetI3dl2ReverbRoomHfGainEPNS0_15I3dl2ReverbTypeEf', 'SetI3dl2ReverbHfReferenceEPNS0_15I3dl2ReverbTypeEf',
           'SetI3dl2ReverbInputOutputEPNS0_15I3dl2ReverbTypeEPKaS4_i', 'SetI3dl2ReverbReflectionsGainEPNS0_15I3dl2ReverbTypeEf', 'SetI3dl2ReverbLateReverbDensityEPNS0_15I3dl2ReverbTypeEf',
           'SetI3dl2ReverbLateReverbDecayTimeEPNS0_15I3dl2ReverbTypeENS_8TimeSpanE', 'SetI3dl2ReverbLateReverbDelayTimeEPNS0_15I3dl2ReverbTypeENS_8TimeSpanE', 'SetI3dl2ReverbLateReverbDiffusionEPNS0_15I3dl2ReverbTypeEf',
           'SetI3dl2ReverbReflectionDelayTimeEPNS0_15I3dl2ReverbTypeENS_8TimeSpanE', 'SetI3dl2ReverbLateReverbHfDecayRatioEPNS0_15I3dl2ReverbTypeEf', 'SetVoicePitchEPNS0_9VoiceTypeEf',
           'SetVoiceMixVolumeEPNS0_9VoiceTypeEPNS0_12FinalMixTypeEfii', 'SetVoiceDestinationEPNS0_19AudioRendererConfigEPNS0_9VoiceTypeEPNS0_12FinalMixTypeE')
for _s in _SETTERS:
    _name, _rest = _s.split('EPNS0_', 1)   # the name ends where the first parameter type (a pointer to an nn::audio type) begins
    reg(f'_ZN2nn5audio{len(_name)}{_name}EPNS0_' + _rest, 'an effect or voice setting: nothing is mixed')(lambda hz, r: ret(r))


# ---- memory pools ----
@reg('_ZN2nn5audio17AcquireMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeEPvm', 'bool: acquired, detached until attached')
def acquire_pool(hz, r): state(hz).pools[r[1]] = POOL_DETACHED; ret(r, 1)


@reg('_ZN2nn5audio17ReleaseMemoryPoolEPNS0_19AudioRendererConfigEPNS0_14MemoryPoolTypeE', 'bool: released')
def release_pool(hz, r): state(hz).pools.pop(r[1], None); ret(r, 1)


@reg('_ZN2nn5audio23RequestAttachMemoryPoolEPNS0_14MemoryPoolTypeE', 'attached at once')
def attach_pool(hz, r): state(hz).pools[r[0]] = POOL_ATTACHED; ret(r, 1)


@reg('_ZN2nn5audio23RequestDetachMemoryPoolEPNS0_14MemoryPoolTypeE', 'detached at once')
def detach_pool(hz, r): state(hz).pools[r[0]] = POOL_DETACHED; ret(r, 1)


reg('_ZN2nn5audio18GetMemoryPoolStateEPKNS0_14MemoryPoolTypeE', 'the state the requests left')(lambda hz, r: ret(r, state(hz).pools.get(r[0], POOL_DETACHED)))


# ---- voices ----
@reg('_ZN2nn5audio16AcquireVoiceSlotEPNS0_19AudioRendererConfigEPNS0_9VoiceTypeEiiNS0_12SampleFormatEiPKvm', 'bool: acquired')
def acquire_voice(hz, r):
    a = state(hz)
    with a.lock: a.voices[r[1]] = Voice(r[2] & M32 or RATE)
    ret(r, 1)


@reg('_ZN2nn5audio16ReleaseVoiceSlotEPNS0_19AudioRendererConfigEPNS0_9VoiceTypeE', 'the voice is forgotten')
def release_voice(hz, r):
    a = state(hz)
    with a.lock: a.voices.pop(r[1], None)


reg('_ZN2nn5audio12IsVoiceValidEPKNS0_9VoiceTypeE', 'valid while acquired')(lambda hz, r: ret(r, int(r[0] in state(hz).voices)))


@reg('_ZN2nn5audio17SetVoicePlayStateEPNS0_9VoiceTypeENS1_9PlayStateE', 'start plays, anything else stops consuming')
def set_play_state(hz, r):
    v = state(hz).voices.get(r[0])
    if v: v.playing = (r[1] & M32) == PLAY_START


@reg('_ZN2nn5audio16AppendWaveBufferEPNS0_9VoiceTypeEPKNS0_10WaveBufferE', 'queued; released when the voice has played its samples')
def append_wave(hz, r):
    a = state(hz); v = a.voices.get(r[0])
    if v is None: ret(r, 0); return
    start, end = struct.unpack('<ii', hz.rd(r[1] + 0x10, 8)); loop = hz.rd(r[1] + 0x18, 1)[0]
    dur = None if loop else max(1, end - start)   # a looping buffer is never released
    with a.lock: v.waves.append((r[1], dur))
    ret(r, 1)


@reg('_ZN2nn5audio21GetReleasedWaveBufferEPNS0_9VoiceTypeE', 'the oldest buffer the voice finished, or null')
def released_wave(hz, r):
    a = state(hz); v = a.voices.get(r[0])
    with a.lock: ret(r, v.released.pop(0) if v and v.released else 0)


reg('_ZN2nn5audio18GetWaveBufferCountEPKNS0_9VoiceTypeE', 'buffers queued or playing')(lambda hz, r: ret(r, len(state(hz).voices[r[0]].waves) if r[0] in state(hz).voices else 0))
reg('_ZN2nn5audio25GetVoicePlayedSampleCountEPKNS0_9VoiceTypeE', 'samples consumed while playing')(lambda hz, r: ret(r, state(hz).voices[r[0]].played if r[0] in state(hz).voices else 0))


# ---- the audio output: a buffer is released after the time its samples take ----
class Out:
    def __init__(self): self.queue, self.free_at, self.started = [], 0.0, False


def out_of(hz, addr):
    a = state(hz)
    if addr not in a.outs: a.outs[addr] = Out()
    return a.outs[addr]


reg('_ZN2nn5audio27InitializeAudioOutParameterEPNS0_17AudioOutParameterE', 'defaults: zeros')(lambda hz, r: hz.wr(r[0], bytes(8)))


@reg('_ZN2nn5audio19OpenDefaultAudioOutEPNS0_8AudioOutERKNS0_17AudioOutParameterE', 'a token; 48 kHz stereo PCM16')
def open_out(hz, r): out_of(hz, r[0]); hz.wr(r[0], struct.pack('<Q', 0xA0D20000)); ret(r)


reg('_ZN2nn5audio21GetAudioOutSampleRateEPKNS0_8AudioOutE', '48 kHz')(lambda hz, r: ret(r, RATE))
reg('_ZN2nn5audio23GetAudioOutChannelCountEPKNS0_8AudioOutE', 'stereo')(lambda hz, r: ret(r, 2))
reg('_ZN2nn5audio23GetAudioOutSampleFormatEPKNS0_8AudioOutE', 'PCM16')(lambda hz, r: ret(r, 2))
reg('_ZN2nn5audio13StartAudioOutEPNS0_8AudioOutE', 'plays (silently)')(lambda hz, r: (setattr(out_of(hz, r[0]), 'started', True), ret(r)))
reg('_ZN2nn5audio12StopAudioOutEPNS0_8AudioOutE', 'stops')(lambda hz, r: setattr(out_of(hz, r[0]), 'started', False))
reg('_ZN2nn5audio13CloseAudioOutEPNS0_8AudioOutE', 'closes')(lambda hz, r: state(hz).outs.pop(r[0], None))


@reg('_ZN2nn5audio21SetAudioOutBufferInfoEPNS0_14AudioOutBufferEPvmm', 'pointer, capacity and size in the first 24 bytes of the struct')
def set_out_buf(hz, r): hz.wr(r[0], struct.pack('<QQQ', r[1], r[2], r[3]))


reg('_ZN2nn5audio28GetAudioOutBufferDataPointerEPKNS0_14AudioOutBufferE', 'pointer')(lambda hz, r: ret(r, hz.rd64(r[0])))
reg('_ZN2nn5audio25GetAudioOutBufferDataSizeEPKNS0_14AudioOutBufferE', 'size')(lambda hz, r: ret(r, hz.rd64(r[0] + 16)))


@reg('_ZN2nn5audio20AppendAudioOutBufferEPNS0_8AudioOutEPNS0_14AudioOutBufferE', 'queued; playing takes the time of its samples (4 bytes a frame)')
def append_out(hz, r):
    o = out_of(hz, r[0]); size = hz.rd64(r[1] + 16)
    now = time.monotonic(); o.free_at = max(o.free_at, now) + size / 4 / RATE
    o.queue.append((r[1], o.free_at)); ret(r, 1)


@reg('_ZN2nn5audio25GetReleasedAudioOutBufferEPNS0_8AudioOutE', 'the oldest buffer whose time has passed, or null')
def released_out(hz, r):
    o = out_of(hz, r[0])
    if o.queue and o.queue[0][1] <= time.monotonic(): ret(r, o.queue.pop(0)[0])
    else: ret(r, 0)


# ---- device names ----
@reg('_ZN2nn5audio19ListAudioDeviceNameEPNS0_15AudioDeviceNameEi', 'one built-in speaker')
def list_devices(hz, r):
    if (r[1] & M32) <= 0 or not r[0]: ret(r, 0); return
    hz.wr(r[0], b'AudioBuiltInSpeakerOutput'.ljust(0x80, b'\0')); ret(r, 1)
