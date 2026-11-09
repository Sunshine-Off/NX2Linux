/* nx2gpu_pick_present_mode (gpu.c): FIFO unless NX2LINUX_PRESENT_MODE names a mode the surface supports. No window, no device. */
#include <stdio.h>
#include <string.h>

int nx2gpu_pick_present_mode(const char *want, const int *have, int n, const char **used);

static int failures;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)
enum { IMMEDIATE = 0, MAILBOX = 1, FIFO = 2, RELAXED = 3 };

int main(void) {
    const char *used;
    const int all[] = { FIFO, MAILBOX, IMMEDIATE, RELAXED }, only_fifo[] = { FIFO };
    CHECK(nx2gpu_pick_present_mode(NULL, all, 4, &used) == FIFO && !strcmp(used, "fifo"));            /* the default */
    CHECK(nx2gpu_pick_present_mode("", all, 4, &used) == FIFO && !strcmp(used, "fifo"));
    CHECK(nx2gpu_pick_present_mode("fifo", all, 4, &used) == FIFO);
    CHECK(nx2gpu_pick_present_mode("immediate", all, 4, &used) == IMMEDIATE && !strcmp(used, "immediate"));
    CHECK(nx2gpu_pick_present_mode("mailbox", all, 4, &used) == MAILBOX && !strcmp(used, "mailbox"));
    CHECK(nx2gpu_pick_present_mode("relaxed", all, 4, &used) == RELAXED && !strcmp(used, "relaxed"));
    CHECK(nx2gpu_pick_present_mode("immediate", only_fifo, 1, &used) == FIFO && strstr(used, "does not support"));   /* not offered: FIFO, and it says so */
    CHECK(nx2gpu_pick_present_mode("immediate", only_fifo, 0, &used) == FIFO);                         /* a surface that listed nothing */
    CHECK(nx2gpu_pick_present_mode("vsync-off", all, 4, &used) == FIFO && strstr(used, "is not fifo"));   /* a name that is not one */
    CHECK(nx2gpu_pick_present_mode("IMMEDIATE", all, 4, &used) == FIFO);                               /* names are lower case, as written */
    if (failures) { fprintf(stderr, "%d failures\n", failures); return 1; }
    puts("presentmode: ok");
    return 0;
}
