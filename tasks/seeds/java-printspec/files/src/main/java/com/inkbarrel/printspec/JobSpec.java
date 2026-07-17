package com.inkbarrel.printspec;

/**
 * One print job as it crosses the order-intake wire.
 *
 * <p>TODO: this is still the placeholder from the intake rewrite kickoff.
 * The wire envelope carries a "kind" discriminator (cards / banner /
 * booklet) and each kind has its own payload record — see the wire tests
 * for the exact shapes.
 */
public interface JobSpec {

    /** Order reference, e.g. "ORD-2107". Every job kind carries one. */
    String orderRef();
}
