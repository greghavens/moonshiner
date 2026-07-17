#include "config.h"

#include <string.h>

/* --- tiny line scanner --------------------------------------------- */

static const char *skip_ws(const char *s, const char *end) {
    while (s < end && (*s == ' ' || *s == '\t'))
        s++;
    return s;
}

static const char *trim_end(const char *s, const char *end) {
    while (end > s &&
           (end[-1] == ' ' || end[-1] == '\t' || end[-1] == '\r'))
        end--;
    return end;
}

/* Parse one line. Returns 1 for a key=value pair, 0 for a line to skip,
 * -1 for a malformed or oversized one. */
static int parse_line(const char *s, const char *end, char *key, char *val) {
    s = skip_ws(s, end);
    end = trim_end(s, end);
    if (s == end || *s == '#')
        return 0;
    const char *eq = memchr(s, '=', (size_t)(end - s));
    if (eq == NULL)
        return -1;
    const char *ke = trim_end(s, eq);
    if (ke == s || (size_t)(ke - s) >= CFG_KEY_MAX)
        return -1;
    memcpy(key, s, (size_t)(ke - s));
    key[ke - s] = '\0';
    const char *vs = skip_ws(eq + 1, end);
    if ((size_t)(end - vs) >= POOL_SLOT_SIZE)
        return -1;
    memcpy(val, vs, (size_t)(end - vs));
    val[end - vs] = '\0';
    return 1;
}

typedef int (*pair_fn)(config *c, const char *key, const char *val);

static int for_each_pair(config *c, const char *text, pair_fn fn) {
    const char *s = text;
    while (*s != '\0') {
        const char *nl = strchr(s, '\n');
        const char *end = nl ? nl : s + strlen(s);
        char key[CFG_KEY_MAX];
        char val[POOL_SLOT_SIZE];
        int r = parse_line(s, end, key, val);
        if (r < 0)
            return -1;
        if (r > 0 && fn(c, key, val) != 0)
            return -1;
        s = nl ? nl + 1 : end;
    }
    return 0;
}

/* --- table helpers -------------------------------------------------- */

static cfg_entry *find_entry(config *c, const char *key) {
    for (size_t i = 0; i < c->count; i++)
        if (strcmp(c->entries[i].key, key) == 0)
            return &c->entries[i];
    return NULL;
}

static int add_entry(config *c, const char *key, const char *val) {
    if (c->count == CFG_MAX_ENTRIES)
        return -1;
    cfg_entry *e = &c->entries[c->count];
    memcpy(e->key, key, strlen(key) + 1);
    e->value = pool_take(c->pool, val);
    if (e->value == NULL)
        return -1;
    e->live = 1;
    c->count++;
    return 0;
}

/* --- initial load ---------------------------------------------------- */

static int load_pair(config *c, const char *key, const char *val) {
    if (find_entry(c, key) != NULL)
        return -1; /* duplicate key in the boot file */
    return add_entry(c, key, val);
}

int cfg_load(config *c, strpool *p, const char *text) {
    if (c == NULL || p == NULL || text == NULL)
        return -1;
    c->count = 0;
    c->pool = p;
    return for_each_pair(c, text, load_pair);
}

/* --- live reload ------------------------------------------------------ */

static int reload_pair(config *c, const char *key, const char *val) {
    cfg_entry *e = find_entry(c, key);
    if (e != NULL) {
        e->live = 1;
        if (strcmp(e->value, val) == 0)
            return 0; /* value unchanged, keep it as is */
        char *nv = pool_take(c->pool, val);
        if (nv == NULL)
            return -1;
        e->value = nv;
        return 0;
    }
    return add_entry(c, key, val);
}

int cfg_reload(config *c, const char *text) {
    if (c == NULL || text == NULL)
        return -1;

    /* Every setting is presumed gone until the new file mentions it, so
     * hand the value storage back up front and let the walk below take
     * whatever it still needs. */
    for (size_t i = 0; i < c->count; i++) {
        c->entries[i].live = 0;
        pool_drop(c->pool, c->entries[i].value);
    }

    if (for_each_pair(c, text, reload_pair) != 0)
        return -1;

    /* Settings the new file never mentioned: their storage was returned
     * up front already, so just close the gaps in the table. */
    size_t w = 0;
    for (size_t i = 0; i < c->count; i++) {
        if (c->entries[i].live)
            c->entries[w++] = c->entries[i];
    }
    c->count = w;
    return 0;
}

/* --- queries ---------------------------------------------------------- */

const char *cfg_get(const config *c, const char *key) {
    if (c == NULL || key == NULL)
        return NULL;
    for (size_t i = 0; i < c->count; i++)
        if (strcmp(c->entries[i].key, key) == 0)
            return c->entries[i].value;
    return NULL;
}

size_t cfg_count(const config *c) {
    return c == NULL ? 0 : c->count;
}
