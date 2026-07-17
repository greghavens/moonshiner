package store;

import java.util.TreeMap;

import domain.PortError;
import domain.Vessel;

/** Registered vessels keyed by name; the name is the public identifier on the desk. */
public final class VesselStore {
    private final TreeMap<String, Vessel> byName = new TreeMap<>();

    public void add(Vessel vessel) {
        if (byName.containsKey(vessel.name())) {
            throw new PortError("DUPLICATE_VESSEL", "vessel already registered: " + vessel.name());
        }
        byName.put(vessel.name(), vessel);
    }

    public boolean exists(String name) {
        return byName.containsKey(name);
    }

    public Vessel require(String name) {
        Vessel vessel = byName.get(name);
        if (vessel == null) {
            throw new PortError("UNKNOWN_VESSEL", "no vessel registered as " + name);
        }
        return vessel;
    }

    /** Re-index a vessel that was renamed in place. */
    public void rekey(String oldName, Vessel vessel) {
        byName.remove(oldName);
        byName.put(vessel.name(), vessel);
    }
}
