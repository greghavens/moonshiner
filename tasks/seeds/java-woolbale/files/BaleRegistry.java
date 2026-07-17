import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Intake registry for the shearing-shed co-op. Bales are grouped by grower
 * as they come off the weighbridge; the broker report lists heaviest first.
 */
public final class BaleRegistry {

    // grower -> that grower's bales, in intake order
    private final Map lots = new HashMap();
    // "seq:baleId" tags, one per intake, in weighbridge order
    private final List intakeLog = new ArrayList();
    private int sequence = 0;

    /** Heaviest first; equal weights fall back to ticket id order. */
    static final Comparator BY_WEIGHT_DESC = new Comparator() {
        public int compare(Object a, Object b) {
            Bale left = (Bale) a;
            Bale right = (Bale) b;
            int byKg = Double.compare(right.kg(), left.kg());
            return byKg != 0 ? byKg : left.id().compareTo(right.id());
        }
    };

    /** Book a bale in off the weighbridge. */
    public void intake(Bale bale) {
        List pen = (List) lots.get(bale.grower());
        if (pen == null) {
            pen = new ArrayList();
            lots.put(bale.grower(), pen);
        }
        pen.add(bale);
        sequence++;
        intakeLog.add(sequence + ":" + bale.id());
    }

    /** Bales for one grower, in intake order (a copy; never null). */
    public List<Bale> lot(String grower) {
        Object pen = lots.get(grower);
        if (pen == null) {
            return new ArrayList<>();
        }
        return new ArrayList<>((List<Bale>) pen);
    }

    /** Every bale in the shed, heaviest first, ticket id breaking ties. */
    public List<Bale> heaviestFirst() {
        List all = new ArrayList();
        for (Object penObj : lots.values()) {
            all.addAll((List) penObj);
        }
        Collections.sort(all, BY_WEIGHT_DESC);
        return all;
    }

    /** Total greasy weight for a pen of bales. */
    public static double totalKg(List pen) {
        double sum = 0.0;
        for (Object o : pen) {
            sum += ((Bale) o).kg();
        }
        return sum;
    }

    /** Flatten several pens into one truck manifest, order preserved. */
    public static List<Bale> loadOrder(List<Bale>... pens) {
        List<Bale> out = new ArrayList<>();
        for (List<Bale> pen : pens) {
            out.addAll(pen);
        }
        return out;
    }

    /** Weighbridge tags in intake order, e.g. "1:W042". */
    public List<String> intakeTags() {
        return new ArrayList<>(intakeLog);
    }

    /** Number of distinct growers with bales in the shed. */
    public int growerCount() {
        return lots.size();
    }
}
