#include "rounding_scope.h"

#include <fenv.h>
#include <stddef.h>

int rounding_scope_run(int rounding_mode,
                       rounding_scope_operation operation,
                       void *context)
{
    int previous_mode;
    int operation_result;

    if (operation == NULL) {
        return ROUNDING_SCOPE_ENV_ERROR;
    }

    previous_mode = fegetround();
    if (previous_mode == -1) {
        return ROUNDING_SCOPE_ENV_ERROR;
    }
    if (fesetround(rounding_mode) != 0) {
        return ROUNDING_SCOPE_ENV_ERROR;
    }

    operation_result = operation(context);
    if (operation_result != ROUNDING_SCOPE_OK) {
        return operation_result;
    }

    if (fesetround(previous_mode) != 0) {
        return ROUNDING_SCOPE_ENV_ERROR;
    }
    return operation_result;
}
