// Acceptance tests for the notification templating engine.
// Run: node --test test_template.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { compile, render, TemplateError } from "./template.ts";

// ---------------------------------------------------------------- plain text

test("text without tags passes through untouched, newlines included", () => {
  const src = "Hi there,\n\nnothing dynamic here — just text & symbols < >.\n";
  assert.equal(render(src, {}), src);
});

// ------------------------------------------------------------- interpolation

test("interpolates {{var}} with flexible inner whitespace", () => {
  assert.equal(render("Hello {{name}}!", { name: "Ada" }), "Hello Ada!");
  assert.equal(render("Hello {{ name }}!", { name: "Ada" }), "Hello Ada!");
  assert.equal(render("Hello {{  name  }}!", { name: "Ada" }), "Hello Ada!");
});

test("resolves dot paths into nested objects", () => {
  const data = { user: { profile: { city: "Oslo" } } };
  assert.equal(render("{{user.profile.city}}", data), "Oslo");
});

test("renders numbers and booleans with String(), including falsy ones", () => {
  assert.equal(render("{{count}}|{{ok}}|{{ratio}}", { count: 0, ok: false, ratio: 1.5 }),
    "0|false|1.5");
});

// ------------------------------------------------------------------ escaping

test("escapes & < > \" ' by default", () => {
  const out = render("{{snippet}}", { snippet: `<a href="x">Tom & Jerry's</a>` });
  assert.equal(out,
    "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#39;s&lt;/a&gt;");
});

test("triple braces {{{var}}} emit raw, unescaped output", () => {
  assert.equal(render("{{{snippet}}}", { snippet: "<b>hi</b>" }), "<b>hi</b>");
  assert.equal(render("{{{ snippet }}}", { snippet: "a & b" }), "a & b");
});

// -------------------------------------------------------- missing-key policy

test("missing paths render as empty string by default", () => {
  assert.equal(render("[{{nick}}]", {}), "[]");
  assert.equal(render("[{{user.address.city}}]", { user: { name: "Ada" } }), "[]");
});

test("null values count as missing", () => {
  assert.equal(render("[{{nick}}]", { nick: null }), "[]");
});

test("missing: 'error' throws a TemplateError naming the path", () => {
  assert.throws(
    () => render("{{user.nick}}", { user: {} }, { missing: "error" }),
    (err: unknown) =>
      err instanceof TemplateError && err.message.includes("user.nick"),
  );
});

test("missing: 'keep' leaves the tag in canonical {{path}} form", () => {
  assert.equal(render("Hi {{ user.nick }}!", {}, { missing: "keep" }),
    "Hi {{user.nick}}!");
  assert.equal(render("Hi {{ nick | upper }}!", {}, { missing: "keep" }),
    "Hi {{nick}}!");
});

// ------------------------------------------------------------------- filters

test("builtin filters upper, lower, trim", () => {
  assert.equal(render("{{name | upper}}", { name: "ada" }), "ADA");
  assert.equal(render("{{name | lower}}", { name: "ADA" }), "ada");
  assert.equal(render("{{name | trim}}", { name: "  ada  " }), "ada");
});

test("filters chain left to right", () => {
  assert.equal(render("{{ name | trim | upper }}", { name: "  ada " }), "ADA");
});

test("default filter takes a quoted argument and fills empty/missing values", () => {
  assert.equal(render(`{{nick | default:"anonymous"}}`, {}), "anonymous");
  assert.equal(render(`{{nick | default:"anonymous"}}`, { nick: "" }), "anonymous");
  assert.equal(render(`{{nick | default:"anonymous"}}`, { nick: "bob" }), "bob");
  assert.equal(render(`{{n | default:"none"}}`, { n: 0 }), "0");
  assert.equal(render(`{{nick | default:"no name given"}}`, {}), "no name given");
});

test("missing policy judges the value after filters ran", () => {
  // default rescues the missing key, so strict mode has nothing to complain about
  assert.equal(
    render(`{{nick | default:"anon"}}`, {}, { missing: "error" }),
    "anon");
});

test("json filter serializes values; output still honors escaping rules", () => {
  const data = { cfg: { tag: "<b>" } };
  assert.equal(render("{{{ cfg | json }}}", data), `{"tag":"<b>"}`);
  assert.equal(render("{{ cfg | json }}", data),
    "{&quot;tag&quot;:&quot;&lt;b&gt;&quot;}");
});

test("custom filters come from opts and win over builtins by name", () => {
  const filters = {
    shout: (v: unknown) => String(v).toUpperCase() + "!",
    upper: (_v: unknown) => "OVERRIDDEN",
  };
  assert.equal(render("{{name | shout}}", { name: "ada" }, { filters }), "ADA!");
  assert.equal(render("{{name | upper}}", { name: "ada" }, { filters }), "OVERRIDDEN");
});

test("unknown filter fails at render time, naming the filter", () => {
  const tpl = compile("{{name | mystery}}"); // compiling alone is fine
  assert.throws(
    () => tpl({ name: "x" }),
    (err: unknown) =>
      err instanceof TemplateError && err.message.includes("mystery"),
  );
  // ...because the filter might be supplied at render time
  assert.equal(
    tpl({ name: "x" }, { filters: { mystery: (v: unknown) => `${v}?` } }),
    "x?");
});

// -------------------------------------------------------------- conditionals

test("if renders its body only for truthy values", () => {
  const src = "{{#if flag}}yes{{/if}}";
  for (const truthy of ["0", "x", 1, -1, [1], { a: 1 }] as const) {
    assert.equal(render(src, { flag: truthy }), "yes", `truthy: ${JSON.stringify(truthy)}`);
  }
  for (const falsy of [false, 0, "", null, undefined, []] as const) {
    assert.equal(render(src, { flag: falsy }), "", `falsy: ${JSON.stringify(falsy)}`);
  }
  assert.equal(render(src, {}), "", "missing key is falsy, not an error");
});

test("if/else picks exactly one branch", () => {
  const src = "{{#if paid}}Receipt attached.{{ else }}Payment pending.{{/if}}";
  assert.equal(render(src, { paid: true }), "Receipt attached.");
  assert.equal(render(src, { paid: false }), "Payment pending.");
});

test("if conditions accept dot paths", () => {
  const src = "{{#if user.admin}}root{{else}}guest{{/if}}";
  assert.equal(render(src, { user: { admin: true } }), "root");
  assert.equal(render(src, { user: {} }), "guest");
});

// --------------------------------------------------------------------- loops

test("each iterates arrays exposing this and @index", () => {
  const src = "{{#each names}}{{@index}}:{{this}};{{/each}}";
  assert.equal(render(src, { names: ["a", "b", "c"] }), "0:a;1:b;2:c;");
});

test("inside each, bare paths hit the item first, then outer scopes", () => {
  const src = "{{#each rows}}{{label}} {{/each}}";
  const data = { label: "fallback", rows: [{ label: "first" }, {}] };
  assert.equal(render(src, data), "first fallback ");
});

test("nested each keeps scopes straight", () => {
  const src =
    "{{#each teams}}{{name}}: {{#each members}}{{@index}}{{this}} {{/each}}| {{/each}}";
  const data = {
    teams: [
      { name: "Red", members: ["ann", "bo"] },
      { name: "Blue", members: ["cy"] },
    ],
  };
  assert.equal(render(src, data), "Red: 0ann 1bo | Blue: 0cy | ");
});

test("each/else renders the else branch for empty or missing arrays", () => {
  const src = "{{#each items}}<{{this}}>{{else}}(none){{/each}}";
  assert.equal(render(src, { items: [] }), "(none)");
  assert.equal(render(src, {}), "(none)");
  assert.equal(render(src, { items: ["a"] }), "<a>");
});

test("each over a present non-array is a TemplateError", () => {
  assert.throws(
    () => render("{{#each name}}x{{/each}}", { name: "bob" }),
    TemplateError);
});

test("blocks nest: if inside each", () => {
  const src = "{{#each tasks}}{{#if done}}[x]{{else}}[ ]{{/if}} {{title}}\n{{/each}}";
  const data = {
    tasks: [
      { title: "ship", done: true },
      { title: "test", done: false },
    ],
  };
  assert.equal(render(src, data), "[x] ship\n[ ] test\n");
});

// ------------------------------------------------------------ compile object

test("compile returns a reusable renderer; data and opts vary per call", () => {
  const tpl = compile("Hi {{name | upper}}{{#if vip}} (VIP){{/if}}");
  assert.equal(tpl({ name: "ada", vip: true }), "Hi ADA (VIP)");
  assert.equal(tpl({ name: "bo" }), "Hi BO");
  assert.equal(tpl({}, { missing: "keep" }), "Hi {{name}}");
  assert.equal(tpl({ name: "ada", vip: true }), "Hi ADA (VIP)"); // no state leak
});

// ------------------------------------------------------------- syntax errors

test("malformed templates fail to compile with TemplateError", () => {
  const bad = [
    "Hello {{name",              // unclosed tag
    "{{}}",                      // empty expression
    "{{#if x}}never closed",     // unclosed block
    "{{#if a}}x{{/each}}",       // mismatched close
    "text {{/if}}",              // stray close
    "{{else}} floating",         // else outside any block
    "{{#unless x}}y{{/unless}}", // unknown block helper
  ];
  for (const src of bad) {
    assert.throws(() => compile(src), TemplateError, `accepted: ${src}`);
  }
});

// --------------------------------------------------------------- integration

test("a realistic digest template renders end to end", () => {
  const src = [
    "Hello {{ user.name | default:\"there\" }},",
    "",
    "{{#if alerts}}You have {{count}} alert(s):",
    "{{#each alerts}}  {{@index}}. {{ title | trim | upper }}{{#if urgent}} !!{{/if}}",
    "{{else}}  (no alerts today)",
    "{{/each}}{{else}}All quiet. {{/if}}-- {{{ footer }}}",
  ].join("\n");
  const data = {
    user: { name: "Sam" },
    count: 2,
    alerts: [
      { title: "  disk full ", urgent: true },
      { title: "cert expiring", urgent: false },
    ],
    footer: "<i>ops-bot</i>",
  };
  assert.equal(render(src, data), [
    "Hello Sam,",
    "",
    "You have 2 alert(s):",
    "  0. DISK FULL !!",
    "  1. CERT EXPIRING",
    "-- <i>ops-bot</i>",
  ].join("\n"));
  // empty alert list: the array is falsy, so the outer else wins
  assert.equal(render(src, { alerts: [], footer: "ops" }),
    "Hello there,\n\nAll quiet. -- ops");
});
