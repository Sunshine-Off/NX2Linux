#include <stdint.h>
#include <stddef.h>
typedef struct { float x, y, z, w; } v4;
typedef struct { int id; double mass; struct { int a, b; } p; char name[24]; } Obj;
static int g_lock; static uint64_t g_counter; static volatile int g_flag;
int sw(int x, int y) { switch (x) { case 0: return y + 1; case 1: return y * 3; case 2: return y ^ 0x55; case 3: return y - 9; case 4: return y << 3; case 5: return y >> 2; case 6: return -y; default: return y; } }
float dot(const v4 *a, const v4 *b) { return a->x*b->x + a->y*b->y + a->z*b->z + a->w*b->w; }
void scale(v4 *v, float s, int n) { for (int i = 0; i < n; i++) { v[i].x *= s; v[i].y *= s; v[i].z *= s; v[i].w *= s; } }
double norm(const double *p, int n) { double s = 0; for (int i = 0; i < n; i++) s += p[i]*p[i]; return s; }
void vadd(float *restrict a, const float *restrict b, const float *restrict c, int n) { for (int i = 0; i < n; i++) a[i] = b[i] + c[i] * 2.0f; }
int cmpf(const void *a, const void *b) { float x = *(const float *)a, y = *(const float *)b; return (x > y) - (x < y); }
void sort(int *a, int n) { for (int i = 1; i < n; i++) { int k = a[i], j = i - 1; while (j >= 0 && a[j] > k) { a[j+1] = a[j]; j--; } a[j+1] = k; } }
uint64_t mul128(uint64_t a, uint64_t b, uint64_t *hi) { __uint128_t r = (__uint128_t)a * b; *hi = r >> 64; return (uint64_t)r; }
void lock(void) { while (__atomic_exchange_n(&g_lock, 1, __ATOMIC_ACQUIRE)) while (__atomic_load_n(&g_lock, __ATOMIC_RELAXED)) ; }
void unlock(void) { __atomic_store_n(&g_lock, 0, __ATOMIC_RELEASE); }
uint64_t inc(void) { return __atomic_fetch_add(&g_counter, 1, __ATOMIC_SEQ_CST); }
int cas(int *p, int e, int n) { return __atomic_compare_exchange_n(p, &e, n, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); }
uint8_t inc8(uint8_t *p) { return __atomic_fetch_add(p, 1, __ATOMIC_RELAXED); }
uint16_t cas16(uint16_t *p, uint16_t e, uint16_t n) { __atomic_compare_exchange_n(p, &e, n, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE); return e; }
void objcopy(Obj *d, const Obj *s) { *d = *s; }
int strlen_(const char *s) { int n = 0; while (s[n]) n++; return n; }
uint32_t hash(const uint8_t *p, size_t n) { uint32_t h = 2166136261u; for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 16777619u; return h; }
int64_t conv(double d, float f) { return (int64_t)d + (int64_t)f + (int)(d > f ? 1 : 0); }
uint32_t bits(uint32_t x) { return __builtin_clz(x) + __builtin_ctz(x) + __builtin_popcount(x) + __builtin_bswap32(x); }
int callback(int (*f)(int), int x) { return f(x) + f(x + 1); }

int sw2(int x, int y) {
  switch (x) {
    case 0: return y * 3 + 0;
    case 1: return y * 4 + 4;
    case 2: return y * 5 + 8;
    case 3: return y * 6 + 1;
    case 4: return y * 7 + 5;
    case 5: return y * 8 + 9;
    case 6: return y * 9 + 2;
    case 7: return y * 10 + 6;
    case 8: return y * 11 + 10;
    case 9: return y * 12 + 3;
    case 10: return y * 13 + 7;
    case 11: return y * 14 + 0;
    case 12: return y * 15 + 4;
    case 13: return y * 16 + 8;
    case 14: return y * 17 + 1;
    case 15: return y * 18 + 5;
    case 16: return y * 19 + 9;
    case 17: return y * 20 + 2;
    case 18: return y * 21 + 6;
    case 19: return y * 22 + 10;
    default: return -1;
  }
}
