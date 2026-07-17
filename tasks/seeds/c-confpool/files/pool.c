#include "pool.h"

#include <string.h>

int pool_init(strpool *p, char *slab, int *next, size_t nslots) {
    if (p == NULL || slab == NULL || next == NULL || nslots == 0)
        return -1;
    p->slab = slab;
    p->next = next;
    p->nslots = nslots;
    p->in_use = 0;
    for (size_t i = 0; i + 1 < nslots; i++)
        next[i] = (int)(i + 1);
    next[nslots - 1] = -1;
    p->free_head = 0;
    return 0;
}

char *pool_take(strpool *p, const char *s) {
    if (p == NULL || s == NULL)
        return NULL;
    size_t len = strlen(s);
    if (len >= POOL_SLOT_SIZE)
        return NULL;
    if (p->free_head < 0)
        return NULL;
    int slot = p->free_head;
    p->free_head = p->next[slot];
    p->next[slot] = -2; /* marks the slot as taken */
    char *dst = p->slab + (size_t)slot * POOL_SLOT_SIZE;
    memcpy(dst, s, len + 1);
    p->in_use++;
    return dst;
}

void pool_drop(strpool *p, char *s) {
    if (p == NULL || s == NULL)
        return;
    size_t off = (size_t)(s - p->slab);
    int slot = (int)(off / POOL_SLOT_SIZE);
    p->next[slot] = p->free_head;
    p->free_head = slot;
    p->in_use--;
}

size_t pool_in_use(const strpool *p) {
    return p == NULL ? 0 : p->in_use;
}
