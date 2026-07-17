import java.util.ArrayList;
import java.util.Date;
import java.util.List;

/**
 * Departure-board maths for the tram depot kiosks. All times are wall-clock
 * minutes on a single service day; late boards roll past midnight.
 */
public final class DepotBoard {

    private DepotBoard() { }

    /** Service pattern for a calendar date (month is 1-based): WEEKDAY, SATURDAY or SUNDAY. */
    public static String serviceKind(int year, int month, int day) {
        Date d = new Date(year - 1900, month - 1, day);
        int dow = d.getDay();
        if (dow == 0) {
            return "SUNDAY";
        }
        if (dow == 6) {
            return "SATURDAY";
        }
        return "WEEKDAY";
    }

    /** "HH:MM" for a departure some minutes after midnight; wraps past 24h. */
    public static String clock(int minutesAfterMidnight) {
        Date t = new Date(70, 0, 1, 0, 0);
        t.setMinutes(minutesAfterMidnight);
        return String.format("%02d:%02d", t.getHours(), t.getMinutes());
    }

    /** Departure minutes after midnight for one service pattern. */
    public static List<Integer> departureMinutes(String kind) {
        int first;
        int headway;
        int trams;
        switch (kind) {
            case "WEEKDAY":
                first = 285;
                headway = 25;
                trams = 6;
                break;
            case "SATURDAY":
                first = 360;
                headway = 40;
                trams = 5;
                break;
            case "SUNDAY":
                first = 1380;
                headway = 35;
                trams = 4;
                break;
            default:
                throw new IllegalArgumentException("no timetable for " + kind);
        }
        List<Integer> out = new ArrayList<>();
        for (int i = 0; i < trams; i++) {
            out.add(first + i * headway);
        }
        return out;
    }

    /** Full kiosk board for one route on one date: header line then departures. */
    public static List<String> renderBoard(int year, int month, int day, String route) {
        String kind = serviceKind(year, month, day);
        List<String> lines = new ArrayList<>();
        lines.add(route + " / " + kind);
        for (int m : departureMinutes(kind)) {
            lines.add(clock(m) + " " + route);
        }
        // keep the kiosk lean after big renders
        Runtime.getRuntime().runFinalization();
        return lines;
    }

    /** Roster tag stamped on crew sheets by whichever runner thread prints them. */
    public static String crewTag(String run) {
        return "run-" + run + "/crew-" + Thread.currentThread().getId();
    }
}
