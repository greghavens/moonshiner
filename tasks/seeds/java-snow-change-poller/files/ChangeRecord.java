import java.util.Map;

/** One change_request row, as raw (non-display) values. */
public final class ChangeRecord {
    public final String sysId;
    public final String number;
    public final String shortDescription;
    public final String assignmentGroup; // sys_id of the group
    public final String approval;
    public final String sysUpdatedOn;    // "yyyy-MM-dd HH:mm:ss" (UTC)

    public ChangeRecord(String sysId, String number, String shortDescription,
                        String assignmentGroup, String approval, String sysUpdatedOn) {
        this.sysId = sysId;
        this.number = number;
        this.shortDescription = shortDescription;
        this.assignmentGroup = assignmentGroup;
        this.approval = approval;
        this.sysUpdatedOn = sysUpdatedOn;
    }

    public static ChangeRecord from(Map<String, Object> raw) {
        return new ChangeRecord(
                str(raw, "sys_id"),
                str(raw, "number"),
                str(raw, "short_description"),
                str(raw, "assignment_group"),
                str(raw, "approval"),
                str(raw, "sys_updated_on"));
    }

    private static String str(Map<String, Object> raw, String key) {
        Object v = raw.get(key);
        if (v == null) return "";
        if (!(v instanceof String)) {
            throw new IllegalArgumentException("field " + key + " is not a plain string value");
        }
        return (String) v;
    }
}
