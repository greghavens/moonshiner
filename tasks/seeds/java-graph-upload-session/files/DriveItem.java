import java.util.Map;

/** Slim projection of a Microsoft Graph driveItem. */
public final class DriveItem {
    private final String id;
    private final String name;
    private final long size;

    public DriveItem(String id, String name, long size) {
        this.id = id;
        this.name = name;
        this.size = size;
    }

    public static DriveItem fromJson(Map<String, Object> obj) {
        Object id = obj.get("id");
        Object name = obj.get("name");
        Object size = obj.get("size");
        if (!(id instanceof String) || !(name instanceof String)) {
            throw new IllegalArgumentException("driveItem payload missing id/name: " + obj);
        }
        long bytes = size instanceof Number n ? (long) n.doubleValue() : 0L;
        return new DriveItem((String) id, (String) name, bytes);
    }

    public String id() {
        return id;
    }

    public String name() {
        return name;
    }

    public long size() {
        return size;
    }
}
