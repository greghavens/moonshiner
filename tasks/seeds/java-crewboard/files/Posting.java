/**
 * One berth on the crewing board.
 *
 * dayRateCents is the advertised day rate; postedDay is the agency's running
 * day counter (higher = fresher posting). Both come from the booking desk.
 */
public record Posting(String id, String title, String description, String vesselType,
                      String homePort, String rank, int dayRateCents, int postedDay) {}
