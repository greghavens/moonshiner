/** One catalog entry on the sale-day lot sheet. Reserve is in whole dollars. */
public record Lot(String number, int ring, String seller, int reserve) {
    public Lot {
        if (number == null || number.isBlank()) {
            throw new IllegalArgumentException("lot number required");
        }
        if (ring < 1) {
            throw new IllegalArgumentException("ring numbers start at 1");
        }
        if (reserve < 0) {
            throw new IllegalArgumentException("reserve cannot be negative");
        }
    }
}
