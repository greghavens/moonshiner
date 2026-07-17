package api;

import domain.PortError;

/** Input checks shared by every desk operation. */
final class Validation {
    private Validation() {
    }

    static void requireName(String value, String what) {
        if (value == null || value.isBlank()) {
            throw new PortError("BAD_REQUEST", what + " must not be blank");
        }
    }

    static void requireWindow(int startSlot, int endSlot) {
        if (startSlot < 0 || endSlot <= startSlot) {
            throw new PortError("BAD_WINDOW", "window must satisfy 0 <= start < end, got "
                    + startSlot + ".." + endSlot);
        }
    }

    static void requirePositive(int value, String what) {
        if (value <= 0) {
            throw new PortError("BAD_REQUEST", what + " must be positive, got " + value);
        }
    }
}
