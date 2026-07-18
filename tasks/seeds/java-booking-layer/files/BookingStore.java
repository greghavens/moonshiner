import java.util.HashMap;
import java.util.Map;

/** In-memory persistence adapter storing only stable BookingEntity records. */
public final class BookingStore {
    private final Map<String, BookingEntity> rows = new HashMap<>();
    private int saves;

    public void seed(BookingEntity entity) { rows.put(entity.id(), entity); }

    public BookingEntity load(String id) {
        BookingEntity entity = rows.get(id);
        if (entity == null) throw new IllegalArgumentException("unknown booking " + id);
        return entity;
    }

    public void save(BookingEntity entity) {
        saves++;
        rows.put(entity.id(), entity);
    }

    public int saves() { return saves; }
}
