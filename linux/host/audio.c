/* The mixer and the output of nn::audio on the Linux host (NX2Linux S7bis, milestone E). Python keeps the game's objects (voices, wave buffers, mix volumes) and asks this library for the work that loops over samples:
 * nx2audio_mix resamples one channel of a wave buffer into a bus, nx2audio_submit hands a block of interleaved stereo to the device. The device is an SDL3 audio stream; for a machine with none (and for the tests)
 * "wav:<file>" writes a WAV file and "null" counts only. What reached the output is counted (frames, frames that were not silent, the peak), so a run can say whether it made sound. */
#include <SDL3/SDL.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { MODE_NULL, MODE_SDL, MODE_WAV };

static struct {
    int mode, open; uint32_t rate; SDL_AudioStream *stream; FILE *wav; uint64_t wav_bytes;
    uint64_t frames, loud_frames, dropped, wav_header_at; float peak; char err[256], device[128];
    pthread_mutex_t mu;
} A = { .mu = PTHREAD_MUTEX_INITIALIZER };

const char *nx2audio_error(void) { return A.err; }
const char *nx2audio_device(void) { return A.device; }

static void wav_header(FILE *f, uint32_t rate, uint64_t data_bytes)
{
    uint32_t v32; uint16_t v16;
    fwrite("RIFF", 1, 4, f); v32 = (uint32_t)(36 + data_bytes); fwrite(&v32, 4, 1, f); fwrite("WAVEfmt ", 1, 8, f);
    v32 = 16; fwrite(&v32, 4, 1, f); v16 = 1; fwrite(&v16, 2, 1, f); v16 = 2; fwrite(&v16, 2, 1, f);
    v32 = rate; fwrite(&v32, 4, 1, f); v32 = rate * 4; fwrite(&v32, 4, 1, f); v16 = 4; fwrite(&v16, 2, 1, f); v16 = 16; fwrite(&v16, 2, 1, f);
    fwrite("data", 1, 4, f); v32 = (uint32_t)data_bytes; fwrite(&v32, 4, 1, f);
}

/* mode: "" or "auto" the default SDL device, "null" nothing, "wav:<path>" a 16-bit stereo WAV file. 0, or -1 with nx2audio_error */
int nx2audio_open(uint32_t rate, const char *mode)
{
    pthread_mutex_lock(&A.mu);
    int r = -1;
    if (A.open) { r = 0; goto out; }
    memset(A.err, 0, sizeof A.err); memset(A.device, 0, sizeof A.device);
    A.rate = rate; A.frames = A.loud_frames = A.dropped = A.wav_header_at = 0; A.peak = 0;
    if (mode && !strncmp(mode, "wav:", 4)) {
        A.wav = fopen(mode + 4, "wb");
        if (!A.wav) { snprintf(A.err, sizeof A.err, "cannot write %s", mode + 4); goto out; }
        wav_header(A.wav, rate, 0); A.wav_bytes = 0; A.mode = MODE_WAV; snprintf(A.device, sizeof A.device, "%s", mode);
    } else if (mode && !strcmp(mode, "null")) { A.mode = MODE_NULL; snprintf(A.device, sizeof A.device, "null"); }
    else {
        if (!SDL_Init(SDL_INIT_AUDIO)) { snprintf(A.err, sizeof A.err, "SDL_Init(audio): %s", SDL_GetError()); goto out; }
        SDL_AudioSpec spec = { SDL_AUDIO_F32, 2, (int)rate };
        A.stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, NULL, NULL);
        if (!A.stream) { snprintf(A.err, sizeof A.err, "no audio device: %s", SDL_GetError()); goto out; }
        SDL_ResumeAudioStreamDevice(A.stream);
        const char *name = SDL_GetAudioDeviceName(SDL_GetAudioStreamDevice(A.stream));
        snprintf(A.device, sizeof A.device, "sdl: %s", name ? name : "default"); A.mode = MODE_SDL;
    }
    A.open = 1; r = 0;
out:
    pthread_mutex_unlock(&A.mu);
    return r;
}

void nx2audio_close(void)
{
    pthread_mutex_lock(&A.mu);
    if (A.open) {
        if (A.mode == MODE_SDL && A.stream) { SDL_DestroyAudioStream(A.stream); A.stream = NULL; }
        if (A.mode == MODE_WAV && A.wav) { fseek(A.wav, 0, SEEK_SET); wav_header(A.wav, A.rate, A.wav_bytes); fclose(A.wav); A.wav = NULL; }
        A.open = 0;
    }
    pthread_mutex_unlock(&A.mu);
}

/* Interleaved float stereo to the output. Returns the frames the output took (fewer than offered when the device's queue is long: late audio is dropped, not delayed) */
uint32_t nx2audio_submit(const float *lr, uint32_t frames)
{
    pthread_mutex_lock(&A.mu);
    uint32_t took = 0;
    if (!A.open) goto out;
    float pk = 0; uint64_t loud = 0;
    for (uint32_t i = 0; i < frames; i++) {
        float l = fabsf(lr[2 * i]), r = fabsf(lr[2 * i + 1]), m = l > r ? l : r;
        if (m > pk) pk = m;
        if (m > 1e-4f) loud++;
    }
    A.frames += frames; A.loud_frames += loud; if (pk > A.peak) A.peak = pk;
    if (A.mode == MODE_SDL) {
        if (SDL_GetAudioStreamQueued(A.stream) > (int)(A.rate / 5 * 8)) { A.dropped += frames; goto out; }   /* more than 200 ms waiting: the device is behind */
        if (SDL_PutAudioStreamData(A.stream, lr, (int)(frames * 8))) took = frames;
    } else if (A.mode == MODE_WAV) {
        int16_t *buf = malloc((size_t)frames * 4);
        if (buf) {
            for (uint32_t i = 0; i < frames * 2; i++) { float v = lr[i]; v = v > 1.0f ? 1.0f : v < -1.0f ? -1.0f : v; buf[i] = (int16_t)lrintf(v * 32767.0f); }
            fwrite(buf, 4, frames, A.wav); A.wav_bytes += (uint64_t)frames * 4; free(buf); took = frames;
            if (A.frames - A.wav_header_at >= A.rate) { long at = ftell(A.wav); fseek(A.wav, 0, SEEK_SET); wav_header(A.wav, A.rate, A.wav_bytes); fseek(A.wav, at, SEEK_SET); fflush(A.wav); A.wav_header_at = A.frames; }   /* once a second: a run that ends without closing still leaves a readable file */
        }
    } else took = frames;
out:
    pthread_mutex_unlock(&A.mu);
    return took;
}

/* Nintendo's DSP-ADPCM (the format of nn::audio's SampleFormat_Adpcm): frames of 8 bytes, 14 samples each; the first byte is the predictor (high nibble, which of the 8 coefficient pairs) and the scale exponent (low
 * nibble), the other 7 bytes are 14 signed 4-bit differences, high nibble first. sample = (difference << scale) * 2048 + coef1 * h1 + coef2 * h2, rounded, shifted down by 11 and clamped to 16 bits. h1 and h2 (the
 * two previous samples) are read and updated, so the next buffer of a voice goes on from where this one ended. Returns the samples written (14 per frame; dst must hold 14 * (bytes / 8)) */
uint32_t nx2audio_adpcm(const uint8_t *src, uint32_t bytes, const int16_t *coef, int16_t *dst, int32_t *h1, int32_t *h2)
{
    uint32_t n = 0; int32_t y1 = *h1, y2 = *h2;
    for (uint32_t f = 0; f + 8 <= bytes; f += 8) {
        uint32_t pred = (src[f] >> 4) & 7, scale = src[f] & 15;
        int32_t c1 = coef[2 * pred], c2 = coef[2 * pred + 1];
        for (uint32_t i = 0; i < 14; i++) {
            uint8_t b = src[f + 1 + i / 2]; int32_t d = (i & 1) ? (b & 15) : (b >> 4);
            if (d >= 8) d -= 16;
            int64_t v = ((int64_t)(d * (1 << scale)) * 2048 + 1024 + (int64_t)c1 * y1 + (int64_t)c2 * y2) >> 11;
            if (v > 32767) v = 32767; else if (v < -32768) v = -32768;
            dst[n++] = (int16_t)v; y2 = y1; y1 = (int32_t)v;
        }
    }
    *h1 = y1; *h2 = y2;
    return n;
}

/* frames, frames that were not silent, frames dropped because the device was behind, the peak (thousandths of full scale) */
void nx2audio_stats(uint64_t *out)
{
    pthread_mutex_lock(&A.mu);
    out[0] = A.frames; out[1] = A.loud_frames; out[2] = A.dropped; out[3] = (uint64_t)(A.peak * 1000.0f);
    pthread_mutex_unlock(&A.mu);
}

/* One channel (ch of src_channels) of a wave buffer, resampled, scaled by gain and added to dst: n_out output samples at most, pos is the fractional frame in src (updated), step the frames per output sample.
 * fmt: 1 PCM 8-bit, 2 PCM 16-bit, 5 float 32. Stops when the buffer ends (the last frame is held for the interpolation, then the buffer is done). Returns the output samples produced. */
uint32_t nx2audio_mix(float *dst, uint32_t n_out, const void *src, uint32_t fmt, uint32_t src_frames, uint32_t src_channels, uint32_t ch, double *pos, double step, float gain)
{
    if (!src_frames || !src_channels || ch >= src_channels) return 0;
    uint32_t n = 0; double p = *pos;
    for (; n < n_out && p < (double)src_frames; n++, p += step) {
        uint32_t i0 = (uint32_t)p, i1 = i0 + 1 < src_frames ? i0 + 1 : i0; float f = (float)(p - (double)i0), a, b;
        switch (fmt) {
        case 1: a = ((const int8_t *)src)[i0 * src_channels + ch] / 128.0f; b = ((const int8_t *)src)[i1 * src_channels + ch] / 128.0f; break;
        case 5: a = ((const float *)src)[i0 * src_channels + ch]; b = ((const float *)src)[i1 * src_channels + ch]; break;
        default: a = ((const int16_t *)src)[i0 * src_channels + ch] / 32768.0f; b = ((const int16_t *)src)[i1 * src_channels + ch] / 32768.0f; break;
        }
        dst[n] += (a + (b - a) * f) * gain;
    }
    *pos = p;
    return n;
}
