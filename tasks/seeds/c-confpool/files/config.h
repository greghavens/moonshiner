/* config.h — key = value settings store for the controller daemon.
 *
 * Values are stored in a strpool (pool.h); the store itself is a small
 * fixed table. cfg_load parses the boot file once; cfg_reload applies a
 * new revision of the file to the live store: changed values are updated,
 * new keys appear, keys missing from the new file disappear.
 */
#ifndef CONFIG_H
#define CONFIG_H

#include <stddef.h>

#include "pool.h"

#define CFG_MAX_ENTRIES 16
#define CFG_KEY_MAX 24

typedef struct {
    char key[CFG_KEY_MAX];
    char *value; /* one pool slot */
    int live;    /* scratch flag used while a reload is applied */
} cfg_entry;

typedef struct {
    cfg_entry entries[CFG_MAX_ENTRIES];
    size_t count;
    strpool *pool;
} config;

/* Parse "key = value" lines ('#' comments and blank lines are skipped).
 * 0 on success, -1 on malformed input, duplicate keys, or a full table
 * or pool. */
int cfg_load(config *c, strpool *p, const char *text);

/* Apply a new revision of the config text to a loaded store. 0 on
 * success, -1 on malformed input or exhaustion. */
int cfg_reload(config *c, const char *text);

/* Current value for key, or NULL when the key is not present. */
const char *cfg_get(const config *c, const char *key);

size_t cfg_count(const config *c);

#endif /* CONFIG_H */
