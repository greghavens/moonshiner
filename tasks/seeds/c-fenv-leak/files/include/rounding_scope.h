#ifndef ROUNDING_SCOPE_H
#define ROUNDING_SCOPE_H

enum rounding_scope_result {
    ROUNDING_SCOPE_OK = 0,
    ROUNDING_SCOPE_REJECTED = 23,
    ROUNDING_SCOPE_ENV_ERROR = 70
};

typedef int (*rounding_scope_operation)(void *context);

/*
 * Run operation with rounding_mode active. On success, return the operation's
 * result. If the rounding mode cannot be changed or restored, return
 * ROUNDING_SCOPE_ENV_ERROR.
 */
int rounding_scope_run(int rounding_mode,
                       rounding_scope_operation operation,
                       void *context);

#endif
