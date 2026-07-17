package domain;

/** A vessel on the port register. Draft is tracked in decimetres so it stays integral. */
public final class Vessel {
    private String name;
    private final int draftDm;
    private final int lengthM;

    public Vessel(String name, int draftDm, int lengthM) {
        this.name = name;
        this.draftDm = draftDm;
        this.lengthM = lengthM;
    }

    public String name() {
        return name;
    }

    public int draftDm() {
        return draftDm;
    }

    public int lengthM() {
        return lengthM;
    }

    /** Reflagging renames the vessel in place; stores re-index around this. */
    public void rename(String newName) {
        this.name = newName;
    }
}
