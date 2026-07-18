/** REST-shaped adapter with a deliberately stable JSON representation. */
public final class BookingRestController {
    private final BookingService service;

    public BookingRestController(BookingService service) { this.service = service; }

    public String extend(String id, int additionalNights) {
        BookingDto dto = service.extend(id, additionalNights);
        return "{\"id\":" + jsonString(dto.id()) + ",\"guestName\":" + jsonString(dto.guestName())
                + ",\"nights\":" + dto.nights()
                + ",\"nightlyCents\":" + dto.nightlyCents()
                + ",\"totalCents\":" + dto.totalCents()
                + ",\"status\":" + jsonString(dto.status()) + "}";
    }

    private static String jsonString(String value) {
        char[] hex = "0123456789abcdef".toCharArray();
        StringBuilder out = new StringBuilder(value.length() + 2).append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append("\\u00").append(hex[(c >>> 4) & 0x0f]).append(hex[c & 0x0f]);
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.append('"').toString();
    }
}
