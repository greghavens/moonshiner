#ifndef ATOMIC_FILE_H
#define ATOMIC_FILE_H

#include <stddef.h>
#include <sys/types.h>

/*
 * Replace destination with exactly length bytes from data.
 *
 * Only the rwx permission bits from permissions are applied.  On success the
 * function returns zero.  On failure it returns an errno value and leaves an
 * existing destination untouched.
 */
int atomic_file_write(const char *destination, const void *data, size_t length,
                      mode_t permissions);

#endif
