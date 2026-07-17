package com.ovenworks.bakeplanner;

import java.util.List;
import java.util.Map;

/** Turns the week's order book into a purchasing list. */
public final class PurchasePlanner {

    private PurchasePlanner() {
    }

    /**
     * One line per ingredient, alphabetical, e.g. {@code "flour: 25830 g"}.
     *
     * <p>Orders map product name to units ordered; each ordered product
     * must have a recipe. Batch counts and the waste allowance come from
     * bake-core's BatchMath.
     */
    public static List<String> plan(Map<String, Integer> orders, List<Recipe> recipes, int wastePct) {
        throw new UnsupportedOperationException("TODO: came over in the module split unfinished");
    }
}
