/**
 * One toll plaza crossing as it arrives on the month-end feed:
 * transponder account, plaza code, day of month, toll in cents.
 */
public record Crossing(String account, String plaza, int day, long cents) {
}
