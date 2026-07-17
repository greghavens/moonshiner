package store;

import java.util.TreeMap;

import domain.Berth;
import domain.PortError;

/** The berth register, keyed by berth code. */
public final class BerthStore {
    private final TreeMap<String, Berth> byCode = new TreeMap<>();

    public void add(Berth berth) {
        if (byCode.containsKey(berth.code())) {
            throw new PortError("DUPLICATE_BERTH", "berth already registered: " + berth.code());
        }
        byCode.put(berth.code(), berth);
    }

    public Berth require(String code) {
        Berth berth = byCode.get(code);
        if (berth == null) {
            throw new PortError("UNKNOWN_BERTH", "no berth registered as " + code);
        }
        return berth;
    }
}
