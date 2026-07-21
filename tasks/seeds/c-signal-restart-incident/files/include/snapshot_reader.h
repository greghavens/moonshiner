#ifndef SNAPSHOT_READER_H
#define SNAPSHOT_READER_H

#include <stddef.h>

/*
 * Fill destination with exactly length bytes from fd.
 *
 * Returns 0 on success. A zero-length request succeeds even when destination
 * is NULL. Otherwise, a NULL destination fails with EINVAL. End-of-stream
 * before length bytes have arrived fails with ECONNRESET, and other read
 * errors retain their errno value.
 *
 * The input is a byte stream: a successful short read is partial progress,
 * not a snapshot boundary.
 */
int snapshot_read_exact(int fd, unsigned char *destination, size_t length);

#endif
