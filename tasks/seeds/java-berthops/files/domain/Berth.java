package domain;

/** A berth with its physical limits and the tariff rate the desk quotes from. */
public final class Berth {
    private final String code;
    private final int maxDraftDm;
    private final int maxLengthM;
    private final long rateCentsPerHour;

    public Berth(String code, int maxDraftDm, int maxLengthM, long rateCentsPerHour) {
        this.code = code;
        this.maxDraftDm = maxDraftDm;
        this.maxLengthM = maxLengthM;
        this.rateCentsPerHour = rateCentsPerHour;
    }

    public String code() {
        return code;
    }

    public int maxDraftDm() {
        return maxDraftDm;
    }

    public int maxLengthM() {
        return maxLengthM;
    }

    public long rateCentsPerHour() {
        return rateCentsPerHour;
    }
}
