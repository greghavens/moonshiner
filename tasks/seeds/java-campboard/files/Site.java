public abstract class Site {
    private final String label;
    private final double baseRate;

    protected Site(String label, double baseRate) {
        this.label = label;
        this.baseRate = baseRate;
    }

    public String label() {
        return label;
    }

    public double baseRate() {
        return baseRate;
    }

    /** Total charge for a stay of the given number of nights. */
    public abstract double total(int nights);

    /** One of the hookup classes listed in CampBoard.amenities(). */
    public abstract String hookupClass();
}

class TentSite extends Site {
    TentSite(String label, double baseRate) {
        super(label, baseRate);
    }

    @Override
    public double total(int nights) {
        return baseRate() * nights;
    }

    @Override
    public String hookupClass() {
        return "none";
    }
}

class CabinSite extends Site {
    private final double turnoverFee;

    CabinSite(String label, double baseRate, double turnoverFee) {
        super(label, baseRate);
        this.turnoverFee = turnoverFee;
    }

    /** Cabins charge one turnover clean per stay on top of the nightly rate. */
    public double totalDue(int nights) {
        return baseRate() * nights + turnoverFee;
    }

    @Override
    public String hookupClass() {
        return "full";
    }
}

class RvSite extends Site {
    RvSite(String label, double baseRate) {
        super(label, baseRate);
    }

    @Override
    public double total(int nights) {
        double gross = baseRate() * nights;
        return nights >= 7 ? gross * 0.9 : gross;
    }

    @Override
    public String hookupClass() {
        return "partial";
    }
}
