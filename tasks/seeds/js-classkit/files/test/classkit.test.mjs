// Design-system class composition contract — protected file. Runs under
// vitest (pinned devDependency): npx --no-install vitest run
import { describe, expect, it } from "vitest";

import { cx, variants } from "../src/classkit.mjs";

describe("cx", () => {
  it("flattens strings, arrays and truthy object keys in order", () => {
    expect(cx("btn", ["btn-lg", { active: true }], { disabled: false }))
      .toBe("btn btn-lg active");
  });

  it("skips everything that is not a non-empty string or truthy key", () => {
    expect(cx(null, undefined, false, 0, NaN, "", "ok", 7, true)).toBe("ok");
  });

  it("splits multi-token strings and object keys", () => {
    expect(cx("a  b", { "c d": true })).toBe("a b c d");
  });

  it("drops duplicate tokens, first occurrence wins", () => {
    expect(cx("a b", "b c", ["a", { c: true, e: true }])).toBe("a b c e");
  });

  it("recurses through nested arrays", () => {
    expect(cx([["a", ["b"]], "c"])).toBe("a b c");
  });

  it("returns the empty string for no usable input", () => {
    expect(cx()).toBe("");
    expect(cx(false, [], {}, "")).toBe("");
  });
});

describe("variants", () => {
  const button = variants({
    base: "btn",
    variants: {
      size: { sm: "btn-sm text-sm", lg: "btn-lg text-lg" },
      tone: { neutral: "bg-gray", danger: "bg-red text-white" },
    },
    defaults: { size: "sm", tone: "neutral" },
    compound: [{ when: { size: "lg", tone: "danger" }, use: "btn-loud" }],
  });

  it("renders base plus defaults when called bare", () => {
    expect(button()).toBe("btn btn-sm text-sm bg-gray");
  });

  it("lets options override defaults, axes in definition order", () => {
    expect(button({ tone: "danger" })).toBe("btn btn-sm text-sm bg-red text-white");
    expect(button({ size: "lg" })).toBe("btn btn-lg text-lg bg-gray");
  });

  it("treats an explicit undefined like an omitted option", () => {
    expect(button({ size: undefined })).toBe("btn btn-sm text-sm bg-gray");
  });

  it("fires compound rules only when every condition matches", () => {
    expect(button({ size: "lg", tone: "danger" }))
      .toBe("btn btn-lg text-lg bg-red text-white btn-loud");
    expect(button({ size: "lg" })).not.toContain("btn-loud");
    expect(button({ tone: "danger" })).not.toContain("btn-loud");
  });

  it("counts defaults toward compound matching", () => {
    const chip = variants({
      base: "chip",
      variants: { tone: { info: "chip-info", warn: "chip-warn" } },
      defaults: { tone: "warn" },
      compound: [{ when: { tone: "warn" }, use: "ring" }],
    });
    expect(chip()).toBe("chip chip-warn ring");
    expect(chip({ tone: "info" })).toBe("chip chip-info");
  });

  it("merges extra inputs through cx, deduped against the rest", () => {
    expect(button({ size: "lg" }, "shadow", { pressed: true, "btn": true }))
      .toBe("btn btn-lg text-lg bg-gray shadow pressed");
  });

  it("dedupes across base, axes and compound classes", () => {
    const chip = variants({
      base: "chip pad",
      variants: { kind: { info: "pad chip-info" } },
      defaults: { kind: "info" },
    });
    expect(chip()).toBe("chip pad chip-info");
  });

  it("skips axes that have no default and no option", () => {
    const badge = variants({
      base: "badge",
      variants: { size: { sm: "badge-sm" }, tone: { hot: "badge-hot" } },
      defaults: { size: "sm" },
    });
    expect(badge()).toBe("badge badge-sm");
    expect(badge({ tone: "hot" })).toBe("badge badge-sm badge-hot");
  });

  it("rejects unknown axes and unknown values with exact messages", () => {
    expect(() => button({ depth: "deep" })).toThrowError("unknown variant: depth");
    expect(() => button({ size: "xl" })).toThrowError("unknown size: xl");
  });

  it("works without base, defaults or compound", () => {
    const bare = variants({ variants: { on: { yes: "lit" } } });
    expect(bare()).toBe("");
    expect(bare({ on: "yes" })).toBe("lit");
  });
});
