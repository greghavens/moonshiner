import { test } from "node:test";
import assert from "node:assert/strict";
import { labelLines, labelTemplate, unitSize } from "./promoline.ts";

test("template carries the printer's price token through untouched", () => {
  assert.equal(
    labelTemplate({ name: "Rye Crisp", unit: "each" }),
    "Rye Crisp — ${price} / each"
  );
  assert.equal(
    labelTemplate({ name: "Olive Oil 1.5 l", unit: "btl" }),
    "Olive Oil 1.5 l — ${price} / btl"
  );
});

test("unit sizes come out of the product name", () => {
  assert.deepEqual(unitSize("Rye Crisp 250 g"), { qty: 250, unit: "g" });
  assert.deepEqual(unitSize("Olive Oil 1.5 l"), { qty: 1.5, unit: "l" });
  assert.deepEqual(unitSize("Yoghurt 450ml"), { qty: 450, unit: "ml" });
  assert.deepEqual(unitSize("Flour 2kg"), { qty: 2, unit: "kg" });
  assert.deepEqual(unitSize("Milk 1 L"), { qty: 1, unit: "l" });
});

test("names without a real size token give null", () => {
  assert.equal(unitSize("Grated Cheese"), null);
  assert.equal(unitSize("5 gum sticks"), null);
  assert.equal(unitSize("Vitamin B12 pack"), null);
  assert.equal(unitSize("Almonds 100 grams"), null);
});

test("labelLines composes template and size suffix", () => {
  const rows = [
    { name: "Rye Crisp 250 g", unit: "pkt" },
    { name: "Grated Cheese", unit: "bag" },
  ];
  assert.deepEqual(labelLines(rows), [
    "Rye Crisp 250 g — ${price} / pkt (250 g)",
    "Grated Cheese — ${price} / bag",
  ]);
});
