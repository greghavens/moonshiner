package com.ovenworks.bakeplanner;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class PurchasePlannerTest {

    private static List<Recipe> houseRecipes() {
        return List.of(
                new Recipe("baguette", 20, Map.of("flour", 6000, "water", 4200, "salt", 60)),
                new Recipe("rye", 12, Map.of("flour", 2200, "water", 1300, "salt", 30, "caraway", 40)));
    }

    @Test
    void weeklyOrderBookBecomesASortedPurchasingList() {
        // baguette 60 units / 20 per batch -> 3 batches; rye 25 / 12 -> 3 (round up).
        // salt: 60*3*1.05 = 189 exactly, 30*3*1.05 = 94.5 -> 95, so 284 total.
        List<String> plan = PurchasePlanner.plan(
                Map.of("baguette", 60, "rye", 25), houseRecipes(), 5);
        assertEquals(
                List.of(
                        "caraway: 126 g",
                        "flour: 25830 g",
                        "salt: 284 g",
                        "water: 17325 g"),
                plan);
    }

    @Test
    void zeroUnitOrdersAreSkipped() {
        List<String> plan = PurchasePlanner.plan(
                Map.of("baguette", 0, "rye", 12), houseRecipes(), 5);
        assertEquals(
                List.of(
                        "caraway: 42 g",
                        "flour: 2310 g",
                        "salt: 32 g",
                        "water: 1365 g"),
                plan);
    }

    @Test
    void orderingAnUnknownProductIsAnError() {
        IllegalArgumentException e = assertThrows(
                IllegalArgumentException.class,
                () -> PurchasePlanner.plan(Map.of("focaccia", 10), houseRecipes(), 5));
        assertEquals("no recipe for \"focaccia\"", e.getMessage());
    }

    @Test
    void emptyOrderBookGivesAnEmptyList() {
        assertTrue(PurchasePlanner.plan(Map.of(), houseRecipes(), 5).isEmpty());
        assertTrue(PurchasePlanner.plan(Map.of("baguette", 0), houseRecipes(), 5).isEmpty());
    }
}
