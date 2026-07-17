// run_tests.mjs — acceptance harness for the accessible form kit.
// Protected test file: do not modify. Usage: node run_tests.mjs
//
// React 19.2.7 production builds are vendored under ./vendor/node_modules —
// nothing here touches the network. The harness shells out to the `esbuild`
// binary on PATH to bundle the seed's sources (form_reducer.ts +
// accessible_form.tsx) together with the vendored React, imports the bundle,
// and then asserts on react-dom/server renderToStaticMarkup output and on
// the pure reducer directly. Bundle output goes to a throwaway .bundle_out/
// directory that is removed again before the assertions run.
//
// Markup comparisons are attribute-order-insensitive: normalizeHtml() sorts
// the attributes inside every start tag alphabetically before comparing, so
// any JSX prop order that produces the required attributes passes.

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
export * from "../form_reducer.ts";
export * from "../accessible_form.tsx";
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
      "\nExpected next to run_tests.mjs: form_reducer.ts and accessible_form.tsx.",
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
  AccessibleForm,
  initForm,
  createFormReducer,
  validateField,
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

const render = (state) =>
  normalizeHtml(renderToStaticMarkup(h(AccessibleForm, { specs: SPECS, state, legend: "Workshop registration" })));

const SPECS = [
  { name: "fullName", label: "Full name", kind: "text", required: true },
  { name: "email", label: "Work email", kind: "email", required: true, hint: "We only use this for booking updates." },
  { name: "notes", label: "Access needs", kind: "multiline", maxLength: 120 },
];

const reduce = () => createFormReducer(SPECS);
const seq = (state, actions) => {
  const r = reduce();
  for (const a of actions) state = r(state, a);
  return state;
};

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

check("initForm builds a pristine state in spec order", () => {
  const s = initForm(SPECS);
  assert.deepEqual(s, {
    fields: {
      fullName: { value: "", touched: false, error: null },
      email: { value: "", touched: false, error: null },
      notes: { value: "", touched: false, error: null },
    },
    submitAttempted: false,
    status: "editing",
  });
  assert.deepEqual(Object.keys(s.fields), ["fullName", "email", "notes"]);
});

check("validateField: required beats format, messages are exact", () => {
  const email = SPECS[1];
  assert.equal(validateField(email, ""), "Work email is required.");
  assert.equal(validateField(email, "   "), "Work email is required.");
  assert.equal(validateField(email, "ada@example"), "Work email must be a valid email address.");
  assert.equal(validateField(email, "ada lovelace@example.com"), "Work email must be a valid email address.");
  assert.equal(validateField(email, "@example.com"), "Work email must be a valid email address.");
  assert.equal(validateField(email, "ada@example.com"), null);
  assert.equal(validateField(email, "  ada@example.com  "), null, "surrounding whitespace is trimmed before checks");
  assert.equal(validateField(email, "a@b.co"), null);
});

check("validateField: maxLength uses trimmed length, boundary inclusive", () => {
  const notes = SPECS[2];
  assert.equal(validateField(notes, ""), null, "optional field may stay empty");
  assert.equal(validateField(notes, "x".repeat(120)), null);
  assert.equal(validateField(notes, "x".repeat(121)), "Access needs must be 120 characters or fewer.");
  assert.equal(validateField(notes, "  " + "x".repeat(120) + "  "), null, "padding does not count");
});

check("change before first blur records the value but no error", () => {
  const s = seq(initForm(SPECS), [{ type: "change", name: "email", value: "not-an-email" }]);
  assert.equal(s.fields.email.value, "not-an-email");
  assert.equal(s.fields.email.touched, false);
  assert.equal(s.fields.email.error, null);
});

check("blur marks touched and validates the current value", () => {
  const s = seq(initForm(SPECS), [
    { type: "change", name: "email", value: "not-an-email" },
    { type: "blur", name: "email" },
  ]);
  assert.equal(s.fields.email.touched, true);
  assert.equal(s.fields.email.error, "Work email must be a valid email address.");
});

check("touched fields revalidate live on every change", () => {
  let s = seq(initForm(SPECS), [
    { type: "change", name: "email", value: "nope" },
    { type: "blur", name: "email" },
  ]);
  assert.equal(s.fields.email.error, "Work email must be a valid email address.");
  s = seq(s, [{ type: "change", name: "email", value: "ada@example.com" }]);
  assert.equal(s.fields.email.error, null, "fixing a touched field clears its error immediately");
});

check("submit validates everything, touches everything, keeps editing on failure", () => {
  const s = seq(initForm(SPECS), [{ type: "submit" }]);
  assert.equal(s.submitAttempted, true);
  assert.equal(s.status, "editing");
  for (const name of ["fullName", "email", "notes"]) assert.equal(s.fields[name].touched, true);
  assert.equal(s.fields.fullName.error, "Full name is required.");
  assert.equal(s.fields.email.error, "Work email is required.");
  assert.equal(s.fields.notes.error, null);
});

check("after a failed submit every field validates live, even untouched-at-the-time ones", () => {
  let s = seq(initForm(SPECS), [{ type: "submit" }]);
  s = seq(s, [{ type: "change", name: "fullName", value: "Ada Lovelace" }]);
  assert.equal(s.fields.fullName.error, null);
  s = seq(s, [{ type: "change", name: "fullName", value: "   " }]);
  assert.equal(s.fields.fullName.error, "Full name is required.");
});

check("a fully valid submit flips status to submitted; the next edit flips it back", () => {
  let s = seq(initForm(SPECS), [
    { type: "change", name: "fullName", value: "Ada Lovelace" },
    { type: "change", name: "email", value: "ada@example.com" },
    { type: "change", name: "notes", value: "step-free access" },
    { type: "submit" },
  ]);
  assert.equal(s.status, "submitted");
  assert.equal(s.submitAttempted, true);
  s = seq(s, [{ type: "change", name: "notes", value: "step-free access please" }]);
  assert.equal(s.status, "editing");
});

check("reset returns a pristine state", () => {
  const s = seq(initForm(SPECS), [
    { type: "change", name: "email", value: "x@y.zz" },
    { type: "blur", name: "email" },
    { type: "submit" },
    { type: "reset" },
  ]);
  assert.deepEqual(s, initForm(SPECS));
});

check("the reducer never mutates its input state", () => {
  const r = reduce();
  const before = initForm(SPECS);
  const snapshot = structuredClone(before);
  const mid = r(before, { type: "change", name: "email", value: "nope" });
  r(mid, { type: "blur", name: "email" });
  r(mid, { type: "submit" });
  assert.deepEqual(before, snapshot);
  assert.deepEqual(mid, seq(initForm(SPECS), [{ type: "change", name: "email", value: "nope" }]));
});

check("unknown action types are returned unchanged, unknown fields throw RangeError", () => {
  const r = reduce();
  const s = initForm(SPECS);
  assert.equal(r(s, { type: "noop" }), s, "same reference for unknown action types");
  assert.throws(() => r(s, { type: "change", name: "ghost", value: "x" }), (err) => {
    return err instanceof RangeError && err.message === "unknown field: ghost";
  });
  assert.throws(() => r(s, { type: "blur", name: "ghost" }), RangeError);
});

// ------------------------------------------------------------ SSR markup

check("pristine form renders the exact markup contract", () => {
  const expected =
    '<form class="acc-form" novalidate=""><fieldset><legend>Workshop registration</legend>' +
    '<div class="form-field"><label for="field-fullName">Full name</label>' +
    '<input id="field-fullName" name="fullName" type="text" value=""/></div>' +
    '<div class="form-field"><label for="field-email">Work email</label>' +
    '<p class="field-hint" id="field-email-hint">We only use this for booking updates.</p>' +
    '<input aria-describedby="field-email-hint" id="field-email" name="email" type="email" value=""/></div>' +
    '<div class="form-field"><label for="field-notes">Access needs</label>' +
    '<textarea id="field-notes" name="notes"></textarea></div>' +
    '<button type="submit">Submit</button></fieldset></form>';
  assert.equal(render(initForm(SPECS)), normalizeHtml(expected));
});

check("failed submit renders the error summary block exactly, in field order", () => {
  const html = render(seq(initForm(SPECS), [{ type: "submit" }]));
  const summary =
    '<div aria-labelledby="error-summary-heading" class="error-summary" role="alert">' +
    '<h2 id="error-summary-heading">There is a problem</h2><ul>' +
    '<li><a href="#field-fullName">Full name is required.</a></li>' +
    '<li><a href="#field-email">Work email is required.</a></li>' +
    "</ul></div>";
  assert.ok(html.includes(normalizeHtml(summary)), `missing/incorrect error summary in:\n${html}`);
  const legendIdx = html.indexOf("</legend>");
  assert.ok(html.indexOf('class="error-summary"') > legendIdx, "summary sits after the legend");
  assert.ok(html.indexOf('class="error-summary"') < html.indexOf('class="form-field"'), "summary sits before the fields");
});

check("erroring fields get aria-invalid and a described error paragraph", () => {
  const html = render(seq(initForm(SPECS), [{ type: "submit" }]));
  assert.ok(
    html.includes('<input aria-describedby="field-fullName-error" aria-invalid="true" id="field-fullName" name="fullName" type="text" value=""/>'),
    `fullName input wrong in:\n${html}`,
  );
  assert.ok(
    html.includes('<input aria-describedby="field-email-hint field-email-error" aria-invalid="true" id="field-email" name="email" type="email" value=""/>'),
    "email combines hint id and error id, hint first",
  );
  assert.ok(html.includes('<p class="field-error" id="field-fullName-error">Full name is required.</p>'));
  assert.ok(html.includes('<p class="field-error" id="field-email-error">Work email is required.</p>'));
  const noteTag = html.match(/<textarea[^>]*>/)[0];
  assert.ok(!noteTag.includes("aria-invalid"), "clean fields carry no aria-invalid");
  assert.ok(!noteTag.includes("aria-describedby"), "no hint and no error means no aria-describedby");
});

check("a blurred invalid field shows inline error but no summary before submit", () => {
  const html = render(seq(initForm(SPECS), [
    { type: "change", name: "email", value: "nope" },
    { type: "blur", name: "email" },
  ]));
  assert.ok(!html.includes("error-summary"), "summary only appears once submit was attempted");
  assert.ok(html.includes('<p class="field-error" id="field-email-error">Work email must be a valid email address.</p>'));
  assert.ok(html.includes('aria-invalid="true"'));
});

check("values echo back into the controls (controlled inputs)", () => {
  const html = render(seq(initForm(SPECS), [
    { type: "change", name: "fullName", value: "Ada Lovelace" },
    { type: "change", name: "email", value: "ada@example.com" },
    { type: "change", name: "notes", value: "step-free access\nfor two" },
  ]));
  assert.ok(html.includes('value="Ada Lovelace"'));
  assert.ok(html.includes('value="ada@example.com"'));
  assert.ok(html.includes("<textarea id=\"field-notes\" name=\"notes\">step-free access\nfor two</textarea>"),
    `textarea renders its value as content:\n${html}`);
});

check("successful submit renders the status line and drops all error UI", () => {
  const html = render(seq(initForm(SPECS), [
    { type: "change", name: "fullName", value: "Ada Lovelace" },
    { type: "change", name: "email", value: "ada@example.com" },
    { type: "submit" },
  ]));
  assert.ok(html.includes('<p class="form-success" role="status">Thanks, your details were submitted.</p>'));
  assert.ok(html.indexOf('class="form-success"') > html.indexOf("</fieldset>"), "status line renders after the fieldset");
  assert.ok(!html.includes("error-summary"));
  assert.ok(!html.includes("field-error"));
  assert.ok(!html.includes("aria-invalid"));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
