package jobs;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Berth-desk notification fan-out. Agents subscribe to a vessel by name and
 * every booking event for that vessel lands in their inbox, oldest first.
 */
public final class NotifyHub {
    private final Map<String, List<String>> subscribersByVessel = new LinkedHashMap<>();
    private final Map<String, List<String>> inboxes = new LinkedHashMap<>();

    public void subscribe(String vesselName, String subscriberId) {
        subscribersByVessel.computeIfAbsent(vesselName, key -> new ArrayList<>()).add(subscriberId);
        inboxes.computeIfAbsent(subscriberId, key -> new ArrayList<>());
    }

    public void publish(String vesselName, String event) {
        List<String> subscribers = subscribersByVessel.get(vesselName);
        if (subscribers == null) {
            return;
        }
        for (String subscriberId : subscribers) {
            inboxes.computeIfAbsent(subscriberId, key -> new ArrayList<>()).add(event);
        }
    }

    /** Move every subscription from one vessel key to another (reflag support). */
    public void rekey(String fromVessel, String toVessel) {
        List<String> moving = subscribersByVessel.remove(fromVessel);
        if (moving == null || moving.isEmpty()) {
            return;
        }
        subscribersByVessel.computeIfAbsent(toVessel, key -> new ArrayList<>()).addAll(moving);
    }

    public List<String> inbox(String subscriberId) {
        return new ArrayList<>(inboxes.getOrDefault(subscriberId, new ArrayList<>()));
    }
}
