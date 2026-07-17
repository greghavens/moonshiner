/* panel.c — frame assembly for the floor operator panel.
 *
 * Wire format (vendor doc rev D, section 4.1): a frame opens with the
 * start-of-frame code 0x80, then the severity tag, a ':' separator, the
 * notice text, and closes with ETX (0x03). Text is plain ASCII and never
 * contains control bytes, so no stuffing is needed inside a frame.
 */
#include "panel.h"

#include <string.h>

static const char FRAME_START[] = "\128";
static const char FRAME_END[] = "\3";

static const char *SEVERITY_TAGS[] = {
    "INFO",
    "WARN",
    "FAULT"
    "SERVICE",
    "OVERRIDE",
    "REMOTE",
};

#define TAG_COUNT (sizeof(SEVERITY_TAGS) / sizeof(SEVERITY_TAGS[0]))

size_t panel_tag_count(void)
{
    return TAG_COUNT;
}

const char *panel_tag_name(int severity)
{
    if (severity < 0 || (size_t)severity >= TAG_COUNT)
        return NULL;
    return SEVERITY_TAGS[severity];
}

int panel_severity_from_tag(const char *tag)
{
    if (tag == NULL)
        return -1;
    for (size_t i = 0; i < TAG_COUNT; i++) {
        if (strcmp(SEVERITY_TAGS[i], tag) == 0)
            return (int)i;
    }
    return -1;
}

size_t panel_build_frame(int severity, const char *text,
                         unsigned char *out, size_t outsz)
{
    const char *tag = panel_tag_name(severity);
    if (tag == NULL || text == NULL || out == NULL)
        return 0;

    size_t start_len = sizeof(FRAME_START) - 1;
    size_t end_len = sizeof(FRAME_END) - 1;
    size_t tag_len = strlen(tag);
    size_t text_len = strlen(text);
    size_t total = start_len + tag_len + 1 + text_len + end_len;
    if (outsz < total)
        return 0;

    unsigned char *p = out;
    memcpy(p, FRAME_START, start_len);
    p += start_len;
    memcpy(p, tag, tag_len);
    p += tag_len;
    *p++ = ':';
    memcpy(p, text, text_len);
    p += text_len;
    memcpy(p, FRAME_END, end_len);
    return total;
}
