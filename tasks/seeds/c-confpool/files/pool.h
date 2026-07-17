/* pool.h — fixed-slot string pool.
 *
 * The controller build has no allocator, so every config value lives in
 * one slot of a caller-provided slab. Slots are handed out and returned
 * through a freelist; in_use tracks how many slots are currently out.
 */
#ifndef POOL_H
#define POOL_H

#include <stddef.h>

#define POOL_SLOT_SIZE 32 /* bytes per slot, terminator included */

typedef struct {
    char *slab;     /* caller storage: nslots * POOL_SLOT_SIZE bytes */
    int *next;      /* caller storage: nslots freelist links */
    int free_head;  /* first free slot, -1 when the pool is full */
    size_t nslots;
    size_t in_use;
} strpool;

/* 0 on success, -1 on bad arguments. Builds the freelist over the slab. */
int pool_init(strpool *p, char *slab, int *next, size_t nslots);

/* Copy s into a free slot and return the slot's storage, or NULL when the
 * pool is exhausted or s does not fit a slot. */
char *pool_take(strpool *p, const char *s);

/* Hand a slot back to the pool. s must be a pointer previously returned
 * by pool_take on the same pool. */
void pool_drop(strpool *p, char *s);

size_t pool_in_use(const strpool *p);

#endif /* POOL_H */
