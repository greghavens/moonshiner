#ifndef PANEL_H
#define PANEL_H

#include <stddef.h>

/* Severity levels for operator-panel notices, lowest to highest.
 * The numeric values are part of the wire contract with the panel. */
enum {
    PANEL_INFO = 0,
    PANEL_WARN,
    PANEL_FAULT,
    PANEL_SERVICE,
    PANEL_OVERRIDE,
    PANEL_REMOTE
};

/* Number of severities the feeder knows about. */
size_t panel_tag_count(void);

/* Wire tag for a severity, or NULL if the severity is out of range. */
const char *panel_tag_name(int severity);

/* Reverse lookup: severity for a wire tag, or -1 if the tag is unknown. */
int panel_severity_from_tag(const char *tag);

/* Assemble one wire frame for the panel into out.
 * Returns the frame length in bytes, or 0 if the severity is out of range,
 * text/out is NULL, or the frame does not fit in outsz bytes. */
size_t panel_build_frame(int severity, const char *text,
                         unsigned char *out, size_t outsz);

#endif /* PANEL_H */
