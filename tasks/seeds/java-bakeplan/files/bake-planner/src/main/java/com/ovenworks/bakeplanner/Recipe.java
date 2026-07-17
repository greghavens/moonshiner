package com.ovenworks.bakeplanner;

import java.util.Map;

/**
 * One product's batch recipe: how many sellable units a batch yields and
 * the grams of each ingredient a single batch consumes.
 */
public record Recipe(String product, int unitsPerBatch, Map<String, Integer> gramsPerBatch) {
}
