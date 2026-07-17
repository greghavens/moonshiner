/** One classed wool bale as it comes over the weighbridge. */
public final class Bale {
    private final String id;       // bale ticket, e.g. "W042"
    private final String grower;
    private final double kg;       // greasy weight
    private final int micron;

    public Bale(String id, String grower, double kg, int micron) {
        this.id = id;
        this.grower = grower;
        this.kg = kg;
        this.micron = micron;
    }

    public String id() { return id; }
    public String grower() { return grower; }
    public double kg() { return kg; }
    public int micron() { return micron; }

    @Override
    public String toString() {
        return id + " " + grower + " " + kg + "kg " + micron + "u";
    }
}
