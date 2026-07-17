import test from "node:test";
import assert from "node:assert/strict";
import {
  buildConfig,
  EInkPanel,
  fleetDraw,
  LedBoard,
  Panel,
  TickerBoard,
} from "./signfleet.ts";

test("buildConfig only materializes keys that were actually given", () => {
  assert.deepEqual(Object.keys(buildConfig(40)), ["brightness"]);
  assert.deepEqual(Object.keys(buildConfig(undefined, "Welcome")), ["caption"]);
  assert.deepEqual(Object.keys(buildConfig()), []);
  assert.equal(buildConfig(40).brightness, 40);
  assert.equal(buildConfig(undefined, "Welcome").caption, "Welcome");
});

test("an empty patch leaves the config exactly as it was", () => {
  const panel = new Panel("lobby-1");
  panel.applyPatch({});
  assert.deepEqual(panel.configKeys(), []);
  panel.applyPatch({ brightness: 60 });
  panel.applyPatch({});
  assert.deepEqual(panel.configKeys(), ["brightness"]);
  assert.equal(panel.setting("brightness"), 60);
});

test("patches merge key by key", () => {
  const panel = new Panel("lobby-2");
  panel.applyPatch({ brightness: 30, caption: "Arrivals" });
  panel.applyPatch({ refreshSecs: 15 });
  assert.deepEqual(panel.configKeys(), ["brightness", "caption", "refreshSecs"]);
  assert.equal(panel.setting("caption"), "Arrivals");
  assert.equal(panel.setting("brightness"), 30);
});

test("clearing the caption removes the key, it does not park undefined there", () => {
  const panel = new Panel("gate-4");
  panel.applyPatch({ caption: "Track 9 closed", brightness: 55 });
  panel.clearCaption();
  assert.deepEqual(panel.configKeys(), ["brightness"]);
  assert.equal("caption" in buildConfig(1), false);
});

test("the class tree describes itself", () => {
  assert.equal(new Panel("p").describe(), "p: bare panel");
  assert.equal(new LedBoard("l").describe(), "l: LED board");
  assert.equal(new TickerBoard("t").describe(), "t: LED ticker");
  assert.equal(new EInkPanel("e").describe(), "e: e-ink panel");
});

test("wake messages: only the ticker adds its scroll test", () => {
  assert.equal(new LedBoard("l").wakeMessage(), "l online");
  assert.equal(new EInkPanel("e").wakeMessage(), "e online");
  assert.equal(new TickerBoard("t").wakeMessage(), "t online (scroll test ok)");
});

test("power draw follows brightness for LED boards", () => {
  const led = new LedBoard("l");
  assert.equal(led.powerDraw(), 45, "default assumes brightness 50");
  led.applyPatch({ brightness: 70 });
  assert.equal(led.powerDraw(), 47);
  assert.equal(new EInkPanel("e").powerDraw(), 3);
  assert.equal(new Panel("p").powerDraw(), 20);
});

test("fleet draw totals every panel", () => {
  const panels = [new Panel("a"), new EInkPanel("b"), new LedBoard("c")];
  assert.equal(fleetDraw(panels), 68);
});
