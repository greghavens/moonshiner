/** Desk CLI adapter over the same application service. */
public final class BookingCli {
    private final BookingService service;

    public BookingCli(BookingService service) { this.service = service; }

    public String run(String line) {
        String[] parts = line.split(" ");
        if (parts.length != 3 || !parts[0].equals("extend")) {
            return "error: usage: extend <booking-id> <additional-nights>";
        }
        BookingDto dto = service.extend(parts[1], Integer.parseInt(parts[2]));
        return "extended " + dto.id() + ": " + dto.nights() + " nights at "
                + dto.nightlyCents() + " cents, total " + dto.totalCents()
                + ", status " + dto.status();
    }
}
