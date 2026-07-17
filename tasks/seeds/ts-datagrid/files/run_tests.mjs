// run_tests.mjs — acceptance harness for the fleet data grid.
// Protected test file: do not modify. Usage: node run_tests.mjs
//
// React 19.2.7 production builds are vendored under ./vendor/node_modules —
// nothing here touches the network. The harness shells out to the `esbuild`
// binary on PATH to bundle the seed's sources (grid_logic.ts + data_grid.tsx)
// together with the vendored React, imports the bundle, and then asserts on
// react-dom/server renderToStaticMarkup output and on the pure grid logic
// directly. Bundle output goes to a throwaway .bundle_out/ directory that is
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
export * from "../grid_logic.ts";
export * from "../data_grid.tsx";
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
      "\nExpected next to run_tests.mjs: grid_logic.ts and data_grid.tsx.",
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
  DataGrid,
  initGrid,
  gridReducer,
  applyView,
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

const COLUMNS = [
  { key: "host", label: "Host", kind: "text" },
  { key: "region", label: "Region", kind: "text" },
  { key: "cpus", label: "CPUs", kind: "number" },
];

const ROWS = [
  { host: "app-01", region: "eu-west", cpus: 4 },
  { host: "app-02", region: "eu-west", cpus: 8 },
  { host: "db-01", region: "eu-central", cpus: 16 },
  { host: "db-02", region: "us-east", cpus: 16 },
  { host: "cache-01", region: "us-east", cpus: 2 },
  { host: "cache-02", region: "eu-west", cpus: 2 },
  { host: "batch-01", region: "us-west", cpus: 32 },
  { host: "edge-01", region: "ap-south", cpus: 1 },
  { host: "edge-02", region: "ap-south", cpus: 1 },
];

const MINI = ROWS.slice(0, 4);

const render = (props) => normalizeHtml(renderToStaticMarkup(h(DataGrid, props)));
const grid = (over = {}) => ({ ...initGrid(), ...over });
const hosts = (view) => view.pageRows.map((r) => r.host);

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

// ---------------------------------------------------------- reducer logic

check("initGrid defaults and pageSize override", () => {
  assert.deepEqual(initGrid(), { sortKey: null, sortDir: "asc", filter: "", page: 0, pageSize: 10 });
  assert.deepEqual(initGrid(25), { sortKey: null, sortDir: "asc", filter: "", page: 0, pageSize: 25 });
});

check("sorting a new column starts ascending and resets the page", () => {
  const s = gridReducer(grid({ page: 2, sortKey: "host", sortDir: "desc" }), { type: "sort", key: "cpus" });
  assert.deepEqual(s, grid({ sortKey: "cpus", sortDir: "asc", page: 0 }));
});

check("sorting the same column toggles direction each time", () => {
  let s = gridReducer(grid(), { type: "sort", key: "host" });
  assert.equal(s.sortDir, "asc");
  s = gridReducer(s, { type: "sort", key: "host" });
  assert.equal(s.sortDir, "desc");
  s = gridReducer(s, { type: "sort", key: "host" });
  assert.equal(s.sortDir, "asc");
  assert.equal(s.sortKey, "host");
});

check("changing the filter resets the page", () => {
  const s = gridReducer(grid({ page: 4 }), { type: "filter", text: "eu" });
  assert.deepEqual(s, grid({ filter: "eu", page: 0 }));
});

check("page navigation: page floors at zero, next increments, prev floors at zero", () => {
  assert.equal(gridReducer(grid(), { type: "page", page: -3 }).page, 0);
  assert.equal(gridReducer(grid(), { type: "page", page: 5 }).page, 5);
  assert.equal(gridReducer(grid({ page: 2 }), { type: "next" }).page, 3);
  assert.equal(gridReducer(grid({ page: 2 }), { type: "prev" }).page, 1);
  assert.equal(gridReducer(grid(), { type: "prev" }).page, 0);
});

check("page-size clamps to at least 1 and resets the page", () => {
  assert.deepEqual(gridReducer(grid({ page: 3 }), { type: "page-size", size: 5 }), grid({ pageSize: 5, page: 0 }));
  assert.equal(gridReducer(grid(), { type: "page-size", size: 0 }).pageSize, 1);
  assert.equal(gridReducer(grid(), { type: "page-size", size: -2 }).pageSize, 1);
});

check("the reducer never mutates and returns unknown actions unchanged", () => {
  const before = grid({ page: 2, filter: "eu" });
  const snapshot = structuredClone(before);
  gridReducer(before, { type: "sort", key: "host" });
  gridReducer(before, { type: "filter", text: "x" });
  assert.deepEqual(before, snapshot);
  assert.equal(gridReducer(before, { type: "noop" }), before, "same reference for unknown action types");
});

// ------------------------------------------------------------- applyView

check("no filter, no sort: rows pass through in order and paginate", () => {
  const v = applyView(ROWS, COLUMNS, grid({ pageSize: 3 }));
  assert.equal(v.totalMatching, 9);
  assert.equal(v.pageCount, 3);
  assert.equal(v.page, 0);
  assert.deepEqual(hosts(v), ["app-01", "app-02", "db-01"]);
  const p1 = applyView(ROWS, COLUMNS, grid({ pageSize: 3, page: 1 }));
  assert.deepEqual(hosts(p1), ["db-02", "cache-01", "cache-02"]);
});

check("filtering is a case-insensitive substring match across all listed columns", () => {
  const eu = applyView(ROWS, COLUMNS, grid({ filter: "EU" }));
  assert.deepEqual(hosts(eu), ["app-01", "app-02", "db-01", "cache-02"]);
  const sixteen = applyView(ROWS, COLUMNS, grid({ filter: "16" }));
  assert.deepEqual(hosts(sixteen), ["db-01", "db-02"], "number cells participate in text matching");
  const none = applyView(ROWS, COLUMNS, grid({ filter: "zzz" }));
  assert.deepEqual(none, { pageRows: [], totalMatching: 0, pageCount: 1, page: 0 });
});

check("number columns sort numerically, not lexicographically", () => {
  const v = applyView(ROWS, COLUMNS, grid({ sortKey: "cpus", sortDir: "asc" }));
  assert.deepEqual(hosts(v), [
    "edge-01", "edge-02", "cache-01", "cache-02", "app-01", "app-02", "db-01", "db-02", "batch-01",
  ]);
});

check("text sort is stable ascending: equal keys keep their incoming order", () => {
  const v = applyView(ROWS, COLUMNS, grid({ sortKey: "region", sortDir: "asc" }));
  assert.deepEqual(hosts(v), [
    "edge-01", "edge-02", "db-01", "app-01", "app-02", "cache-02", "db-02", "cache-01", "batch-01",
  ]);
});

check("descending flips the comparison but equal keys still keep incoming order", () => {
  const v = applyView(ROWS, COLUMNS, grid({ sortKey: "region", sortDir: "desc" }));
  assert.deepEqual(hosts(v), [
    "batch-01", "db-02", "cache-01", "app-01", "app-02", "cache-02", "db-01", "edge-01", "edge-02",
  ]);
});

check("a page past the end clamps to the last page", () => {
  const v = applyView(ROWS, COLUMNS, grid({ pageSize: 4, page: 9 }));
  assert.equal(v.pageCount, 3);
  assert.equal(v.page, 2);
  assert.deepEqual(hosts(v), ["edge-02"]);
});

check("applyView never mutates the rows it is given", () => {
  const input = structuredClone(ROWS);
  const snapshot = structuredClone(ROWS);
  applyView(input, COLUMNS, grid({ sortKey: "cpus", sortDir: "desc", filter: "0" }));
  assert.deepEqual(input, snapshot, "sorting must work on a copy");
});

// ------------------------------------------------------------ SSR markup

check("default view renders the exact markup contract", () => {
  const expected =
    '<div class="data-grid"><table><caption>Fleet</caption><thead><tr>' +
    '<th scope="col"><button class="sort-toggle" type="button">Host</button></th>' +
    '<th scope="col"><button class="sort-toggle" type="button">Region</button></th>' +
    '<th scope="col"><button class="sort-toggle" type="button">CPUs</button></th>' +
    "</tr></thead><tbody>" +
    '<tr><td>app-01</td><td>eu-west</td><td class="num">4</td></tr>' +
    '<tr><td>app-02</td><td>eu-west</td><td class="num">8</td></tr>' +
    '<tr><td>db-01</td><td>eu-central</td><td class="num">16</td></tr>' +
    '<tr><td>db-02</td><td>us-east</td><td class="num">16</td></tr>' +
    "</tbody></table>" +
    '<p class="grid-status">Showing 1-4 of 4</p><p class="grid-page">Page 1 of 1</p></div>';
  assert.equal(render({ columns: COLUMNS, rows: MINI, caption: "Fleet" }), normalizeHtml(expected));
});

check("the sorted column header carries aria-sort, the others none", () => {
  const html = render({ columns: COLUMNS, rows: MINI, caption: "Fleet", init: grid({ sortKey: "cpus", sortDir: "desc" }) });
  assert.ok(html.includes('<th aria-sort="descending" scope="col"><button class="sort-toggle" type="button">CPUs</button></th>'), html);
  assert.equal(html.match(/aria-sort/g).length, 1, "exactly one header is marked sorted");
  const asc = render({ columns: COLUMNS, rows: MINI, caption: "Fleet", init: grid({ sortKey: "host", sortDir: "asc" }) });
  assert.ok(asc.includes('<th aria-sort="ascending" scope="col"><button class="sort-toggle" type="button">Host</button></th>'), asc);
});

check("sorted view renders rows in sorted order", () => {
  const html = render({ columns: COLUMNS, rows: MINI, caption: "Fleet", init: grid({ sortKey: "cpus", sortDir: "desc" }) });
  const order = [...html.matchAll(/<tr><td>([a-z0-9-]+)<\/td>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["db-01", "db-02", "app-02", "app-01"]);
});

check("no matching rows renders the empty-state row spanning every column", () => {
  const html = render({ columns: COLUMNS, rows: ROWS, caption: "Fleet", init: grid({ filter: "zzz" }) });
  assert.ok(html.includes('<tr class="empty-row"><td colspan="3">No matching rows</td></tr>'), html);
  assert.ok(html.includes('<p class="grid-status">Showing 0 of 0</p>'));
  assert.ok(html.includes('<p class="grid-page">Page 1 of 1</p>'));
});

check("page two of a filtered-and-paged view shows the right window and status", () => {
  const html = render({ columns: COLUMNS, rows: ROWS, caption: "Fleet", init: grid({ pageSize: 3, page: 1 }) });
  const order = [...html.matchAll(/<tr><td>([a-z0-9-]+)<\/td>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["db-02", "cache-01", "cache-02"]);
  assert.ok(html.includes('<p class="grid-status">Showing 4-6 of 9</p>'), html);
  assert.ok(html.includes('<p class="grid-page">Page 2 of 3</p>'));
});

check("an out-of-range page clamps in the rendered status line too", () => {
  const html = render({ columns: COLUMNS, rows: ROWS, caption: "Fleet", init: grid({ pageSize: 4, page: 9 }) });
  assert.ok(html.includes('<p class="grid-status">Showing 9-9 of 9</p>'), html);
  assert.ok(html.includes('<p class="grid-page">Page 3 of 3</p>'));
  const order = [...html.matchAll(/<tr><td>([a-z0-9-]+)<\/td>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["edge-02"]);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
