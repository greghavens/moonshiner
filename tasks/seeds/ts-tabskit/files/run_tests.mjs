// run_tests.mjs — acceptance harness for the tabskit disclosure widgets.
// Protected test file: do not modify. Usage: node run_tests.mjs
//
// React 19.2.7 production builds are vendored under ./vendor/node_modules —
// nothing here touches the network. The harness shells out to the `esbuild`
// binary on PATH to bundle tabskit.tsx together with the vendored React,
// imports the bundle, and asserts on react-dom/server renderToStaticMarkup
// output. Bundle output goes to a throwaway .bundle_out/ directory that is
// removed again before the assertions run.
//
// Markup comparisons are attribute-order-insensitive and attribute-name-case-
// insensitive: normalizeHtml() lowercases attribute names and sorts the
// attributes inside every start tag before comparing.

import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, ".bundle_out");
const VENDOR = path.join(HERE, "vendor", "node_modules");

const ENTRY = `
export { renderToStaticMarkup } from "react-dom/server";
export { createElement } from "react";
export * from "../tabskit.tsx";
`;

function fatal(msg) {
  console.error(msg);
  process.exit(1);
}

function buildBundle() {
  const probe = spawnSync("esbuild", ["--version"], { encoding: "utf8" });
  if (probe.error || probe.status !== 0) {
    fatal(
      "FATAL: the `esbuild` binary is not on PATH.\n" +
      "This harness uses esbuild to bundle the .tsx sources with the React\n" +
      "copy vendored in ./vendor/node_modules. Install esbuild or fix PATH,\n" +
      "then re-run: node run_tests.mjs",
    );
  }
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  writeFileSync(path.join(OUT, "entry.tsx"), ENTRY);
  const res = spawnSync(
    "esbuild",
    [
      path.join(OUT, "entry.tsx"),
      "--bundle",
      "--format=esm",
      "--jsx=automatic",
      '--define:process.env.NODE_ENV="production"',
      `--outfile=${path.join(OUT, "bundle.mjs")}`,
      "--log-level=warning",
    ],
    { encoding: "utf8", env: { ...process.env, NODE_PATH: VENDOR } },
  );
  if (res.status !== 0) {
    fatal(
      "FATAL: esbuild could not bundle the sources.\n" +
      (res.stderr || res.stdout || "") +
      "\nExpected next to run_tests.mjs: tabskit.tsx.",
    );
  }
  return path.join(OUT, "bundle.mjs");
}

let M;
try {
  M = await import(pathToFileURL(buildBundle()).href);
} catch (err) {
  console.error("FATAL: importing the bundle failed:\n" + ((err && err.stack) || err));
  process.exit(1);
} finally {
  rmSync(OUT, { recursive: true, force: true });
}

const {
  renderToStaticMarkup,
  createElement: h,
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  Accordion,
  AccordionItem,
} = M;

// ---------------------------------------------------------------- helpers

function normalizeHtml(html) {
  return html.replace(/<([a-zA-Z][^\s/>]*)((?:[^>"]|"[^"]*")*?)(\/?)>/g, (_m, tag, attrs, slash) => {
    const parts = (attrs.match(/[^\s=]+(?:="[^"]*")?/g) || []).map((part) => {
      const eq = part.indexOf("=");
      return eq === -1 ? part.toLowerCase() : part.slice(0, eq).toLowerCase() + part.slice(eq);
    });
    parts.sort();
    return `<${tag}${parts.length ? " " + parts.join(" ") : ""}${slash}>`;
  });
}

const render = (el) => normalizeHtml(renderToStaticMarkup(el));

// The opening tag of the element whose sole text child is `text`.
function tagFor(html, tagName, text) {
  const m = html.match(new RegExp(`<${tagName}([^>]*)>${text}</${tagName}>`));
  assert.ok(m, `no <${tagName}> wrapping exactly "${text}" in:\n${html}`);
  return `<${tagName}${m[1]}>`;
}

const tabsDemo = (props = {}) =>
  h(
    Tabs,
    props,
    h(
      TabList,
      { label: "Project sections" },
      h(Tab, null, "Overview"),
      h(Tab, null, "Metrics"),
      h(Tab, null, "Settings"),
    ),
    h(
      TabPanels,
      null,
      h(TabPanel, null, "overview body"),
      h(TabPanel, null, "metrics body"),
      h(TabPanel, null, "settings body"),
    ),
  );

const accDemo = (props = {}) =>
  h(
    Accordion,
    props,
    h(AccordionItem, { title: "Shipping" }, "ships in 2 days"),
    h(AccordionItem, { title: "Returns" }, "30 day window"),
    h(AccordionItem, { title: "Warranty" }, "1 year included"),
  );

let passed = 0;
let failed = 0;
function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`ok   ${name}`);
  } catch (err) {
    failed += 1;
    console.error(`FAIL ${name}\n${(err && err.stack) || err}`);
  }
}

// ==========================================================================
// EXISTING BEHAVIOR — these pass against today's tabskit.tsx and MUST stay
// green. They deliberately tolerate extra attributes so the new work does
// not break them.
// ==========================================================================

check("existing: three tabs render in order, first active by default", () => {
  const html = render(tabsDemo());
  const order = [...html.matchAll(/<button[^>]*>(Overview|Metrics|Settings)<\/button>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["Overview", "Metrics", "Settings"]);
  assert.ok(tagFor(html, "button", "Overview").includes('class="tab is-active"'));
  assert.ok(tagFor(html, "button", "Metrics").includes('class="tab"'));
  assert.ok(tagFor(html, "button", "Settings").includes('class="tab"'));
  for (const label of ["Overview", "Metrics", "Settings"]) {
    assert.ok(tagFor(html, "button", label).includes('type="button"'));
  }
});

check("existing: only the active panel is visible, the rest are hidden", () => {
  const html = render(tabsDemo());
  const active = tagFor(html, "div", "overview body");
  assert.ok(active.includes('class="tab-panel"'));
  assert.ok(!active.includes("hidden"), "active panel must not be hidden");
  for (const body of ["metrics body", "settings body"]) {
    const tag = tagFor(html, "div", body);
    assert.ok(tag.includes('class="tab-panel"'));
    assert.ok(tag.includes('hidden=""'), `${body} should be hidden`);
  }
});

check("existing: defaultIndex picks the initially active tab", () => {
  const html = render(tabsDemo({ defaultIndex: 2 }));
  assert.ok(tagFor(html, "button", "Settings").includes('class="tab is-active"'));
  assert.ok(tagFor(html, "button", "Overview").includes('class="tab"'));
  assert.ok(!tagFor(html, "div", "settings body").includes("hidden"));
  assert.ok(tagFor(html, "div", "overview body").includes('hidden=""'));
});

check("existing: accordion renders triggers and honors defaultOpen", () => {
  const html = render(accDemo({ defaultOpen: [1] }));
  const order = [...html.matchAll(/<button[^>]*>(Shipping|Returns|Warranty)<\/button>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["Shipping", "Returns", "Warranty"]);
  for (const title of ["Shipping", "Returns", "Warranty"]) {
    const tag = tagFor(html, "button", title);
    assert.ok(tag.includes('class="acc-trigger"'));
    assert.ok(tag.includes('type="button"'));
  }
  assert.ok(html.includes('<h3 class="acc-header">'));
  assert.ok(!tagFor(html, "div", "30 day window").includes("hidden"), "defaultOpen panel is visible");
  assert.ok(tagFor(html, "div", "ships in 2 days").includes('hidden=""'));
  assert.ok(tagFor(html, "div", "1 year included").includes('hidden=""'));
});

check("existing: accordion with no defaultOpen renders fully collapsed", () => {
  const html = render(accDemo());
  for (const body of ["ships in 2 days", "30 day window", "1 year included"]) {
    assert.ok(tagFor(html, "div", body).includes('hidden=""'));
  }
});

// ==========================================================================
// NEW BEHAVIOR — the feature under test. See the ticket in the prompt:
// WAI-ARIA tabs/accordion attributes plus controlled/uncontrolled support.
// ==========================================================================

check("feature: the tab strip is a labelled tablist", () => {
  const html = render(tabsDemo());
  const m = html.match(/<div[^>]*class="tab-list"[^>]*>/);
  assert.ok(m, `no tab-list container in:\n${html}`);
  assert.equal(m[0], '<div aria-label="Project sections" class="tab-list" role="tablist">');
});

check("feature: tabs carry role, ids, aria-selected and aria-controls wiring", () => {
  const html = render(tabsDemo());
  assert.equal(
    tagFor(html, "button", "Overview"),
    '<button aria-controls="tabs-panel-0" aria-selected="true" class="tab is-active" id="tabs-tab-0" role="tab" tabindex="0" type="button">',
  );
  assert.equal(
    tagFor(html, "button", "Metrics"),
    '<button aria-controls="tabs-panel-1" aria-selected="false" class="tab" id="tabs-tab-1" role="tab" tabindex="-1" type="button">',
  );
  assert.equal(
    tagFor(html, "button", "Settings"),
    '<button aria-controls="tabs-panel-2" aria-selected="false" class="tab" id="tabs-tab-2" role="tab" tabindex="-1" type="button">',
  );
});

check("feature: roving tabindex follows the active tab", () => {
  const html = render(tabsDemo({ defaultIndex: 1 }));
  assert.ok(tagFor(html, "button", "Metrics").includes('tabindex="0"'));
  assert.ok(tagFor(html, "button", "Metrics").includes('aria-selected="true"'));
  assert.ok(tagFor(html, "button", "Overview").includes('tabindex="-1"'));
  assert.ok(tagFor(html, "button", "Overview").includes('aria-selected="false"'));
  assert.ok(tagFor(html, "button", "Settings").includes('tabindex="-1"'));
});

check("feature: panels are tabpanels labelled by their tab, focusable, hidden when inactive", () => {
  const html = render(tabsDemo());
  assert.equal(
    tagFor(html, "div", "overview body"),
    '<div aria-labelledby="tabs-tab-0" class="tab-panel" id="tabs-panel-0" role="tabpanel" tabindex="0">',
  );
  assert.equal(
    tagFor(html, "div", "metrics body"),
    '<div aria-labelledby="tabs-tab-1" class="tab-panel" hidden="" id="tabs-panel-1" role="tabpanel" tabindex="0">',
  );
});

check("feature: a controlled Tabs honors index and ignores defaultIndex", () => {
  const html = render(tabsDemo({ index: 2, defaultIndex: 0 }));
  assert.ok(tagFor(html, "button", "Settings").includes('aria-selected="true"'));
  assert.ok(tagFor(html, "button", "Settings").includes('class="tab is-active"'));
  assert.ok(tagFor(html, "button", "Overview").includes('aria-selected="false"'));
  assert.ok(!tagFor(html, "div", "settings body").includes("hidden"));
  assert.ok(tagFor(html, "div", "overview body").includes('hidden=""'));
  const zero = render(tabsDemo({ index: 0, defaultIndex: 2 }));
  assert.ok(tagFor(zero, "button", "Overview").includes('aria-selected="true"'), "index 0 is still controlled");
});

check("feature: the id prop prefixes every tab/panel id", () => {
  const html = render(tabsDemo({ id: "billing" }));
  assert.ok(tagFor(html, "button", "Overview").includes('id="billing-tab-0"'));
  assert.ok(tagFor(html, "button", "Overview").includes('aria-controls="billing-panel-0"'));
  assert.ok(tagFor(html, "div", "metrics body").includes('id="billing-panel-1"'));
  assert.ok(tagFor(html, "div", "metrics body").includes('aria-labelledby="billing-tab-1"'));
});

check("feature: accordion triggers expose expanded state and control their panel", () => {
  const html = render(accDemo({ defaultOpen: [1] }));
  assert.equal(
    tagFor(html, "button", "Shipping"),
    '<button aria-controls="accordion-panel-0" aria-expanded="false" class="acc-trigger" id="accordion-trigger-0" type="button">',
  );
  assert.equal(
    tagFor(html, "button", "Returns"),
    '<button aria-controls="accordion-panel-1" aria-expanded="true" class="acc-trigger" id="accordion-trigger-1" type="button">',
  );
});

check("feature: accordion panels are labelled regions", () => {
  const html = render(accDemo({ defaultOpen: [1] }));
  assert.equal(
    tagFor(html, "div", "30 day window"),
    '<div aria-labelledby="accordion-trigger-1" class="acc-panel" id="accordion-panel-1" role="region">',
  );
  assert.equal(
    tagFor(html, "div", "ships in 2 days"),
    '<div aria-labelledby="accordion-trigger-0" class="acc-panel" hidden="" id="accordion-panel-0" role="region">',
  );
});

check("feature: a controlled Accordion honors open and ignores defaultOpen", () => {
  const html = render(accDemo({ open: [0, 2], defaultOpen: [1] }));
  assert.ok(tagFor(html, "button", "Shipping").includes('aria-expanded="true"'));
  assert.ok(tagFor(html, "button", "Warranty").includes('aria-expanded="true"'));
  assert.ok(tagFor(html, "button", "Returns").includes('aria-expanded="false"'));
  assert.ok(!tagFor(html, "div", "ships in 2 days").includes("hidden"));
  assert.ok(!tagFor(html, "div", "1 year included").includes("hidden"));
  assert.ok(tagFor(html, "div", "30 day window").includes('hidden=""'));
  const empty = render(accDemo({ open: [], defaultOpen: [1] }));
  assert.ok(tagFor(empty, "div", "30 day window").includes('hidden=""'), "open=[] is controlled, not absent");
});

check("feature: several accordion panels can be open at once, ids follow the id prop", () => {
  const html = render(accDemo({ defaultOpen: [0, 1], id: "faq" }));
  assert.ok(!tagFor(html, "div", "ships in 2 days").includes("hidden"));
  assert.ok(!tagFor(html, "div", "30 day window").includes("hidden"));
  assert.ok(tagFor(html, "div", "1 year included").includes('hidden=""'));
  assert.ok(tagFor(html, "button", "Shipping").includes('id="faq-trigger-0"'));
  assert.ok(tagFor(html, "button", "Shipping").includes('aria-controls="faq-panel-0"'));
  assert.ok(tagFor(html, "div", "ships in 2 days").includes('id="faq-panel-0"'));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
