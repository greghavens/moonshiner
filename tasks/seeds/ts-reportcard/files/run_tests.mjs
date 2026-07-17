// run_tests.mjs — regression harness for the quarterly vendor-spend report.
// Protected test file: do not modify. Usage: node run_tests.mjs
//
// React 19.2.7 production builds are vendored under ./vendor/node_modules —
// nothing here touches the network. The harness shells out to the `esbuild`
// binary on PATH to bundle report.tsx (+ format.ts) together with the
// vendored React, imports the bundle, and asserts on react-dom/server
// renderToStaticMarkup output. Bundle output goes to a throwaway
// .bundle_out/ directory that is removed again before the assertions run.
//
// Markup comparisons are attribute-order-insensitive and attribute-name-case-
// insensitive: normalizeHtml() lowercases attribute names and sorts the
// attributes inside every start tag before comparing. Text content is never
// rewritten.

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
export * from "../report.tsx";
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
      "\nExpected next to run_tests.mjs: report.tsx and format.ts.",
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

const { renderToStaticMarkup, createElement: h, QuarterlyReport } = M;

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

const render = (props) => normalizeHtml(renderToStaticMarkup(h(QuarterlyReport, props)));

// Fresh fixture per test: exactly what the planning-sheet importer hands us.
// vendors[i] lines up with budgets[i].
const baseProps = () => ({
  quarter: "Q2 FY26",
  vendors: [
    { name: "O'Brien & Co", spend: 18200 },
    { name: "Fenwick Print", spend: 4100 },
    { name: "Larkspur Media", spend: 9600 },
    { name: "Quill Supply", spend: 12500 },
  ],
  budgets: [15000, 3800, 9600, 12000],
  footnote: "Figures exclude *pending* invoices & credits.",
});

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

// ------------------------------------------------------------------ tests

check("rendering the same props twice produces byte-identical markup", () => {
  const props = baseProps();
  const first = render(props);
  const second = render(props);
  assert.equal(second, first, "a second render of unchanged props must not change the report");
});

check("summary figures are correct on a re-render, not just the first paint", () => {
  const props = baseProps();
  render(props);
  const second = render(props);
  assert.ok(
    second.includes(
      '<ul class="summary"><li>Total spend: $44,400</li><li>Vendors: 4</li><li>Over budget: 3</li></ul>',
    ),
    `summary block wrong on second render:\n${second}`,
  );
});

check("variance lines stay aligned with the planning sheet on a re-render", () => {
  const props = baseProps();
  render(props);
  const second = render(props);
  assert.ok(
    second.includes(
      '<ul class="variance">' +
        "<li>O&#x27;Brien &amp; Co: over by $3,200</li>" +
        "<li>Fenwick Print: over by $300</li>" +
        "<li>Larkspur Media: on budget</li>" +
        "<li>Quill Supply: over by $500</li>" +
        "</ul>",
    ),
    `variance block wrong on second render:\n${second}`,
  );
});

check("vendor names render their special characters exactly once", () => {
  const html = render(baseProps());
  assert.ok(html.includes("O&#x27;Brien &amp; Co: over by $3,200"), `name mangled in:\n${html}`);
  assert.ok(!html.includes("&amp;amp;"), "ampersand was escaped twice somewhere");
  assert.ok(!html.includes("&amp;#39;"), "apostrophe was escaped twice somewhere");
});

check("top-vendors table ranks by spend without reordering the caller's data", () => {
  const props = baseProps();
  const html = render(props);
  assert.ok(
    html.includes(
      '<table class="top-vendors"><caption>Top 3 by spend</caption><tbody>' +
        "<tr><td>O&#x27;Brien &amp; Co</td><td>$18,200</td></tr>" +
        "<tr><td>Quill Supply</td><td>$12,500</td></tr>" +
        "<tr><td>Larkspur Media</td><td>$9,600</td></tr>" +
        "</tbody></table>",
    ),
    `top-vendors table wrong in:\n${html}`,
  );
  assert.deepEqual(
    props.vendors,
    baseProps().vendors,
    "rendering must not mutate the vendors array it was given",
  );
});

check("header and footnote: emphasis is real markup, footnote text escaped exactly once", () => {
  const html = render(baseProps());
  assert.ok(html.includes("<h1>Vendor spend, Q2 FY26</h1>"));
  assert.ok(
    html.includes('<p class="footnote">Figures exclude <strong>pending</strong> invoices &amp; credits.</p>'),
    `footnote wrong in:\n${html}`,
  );
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
