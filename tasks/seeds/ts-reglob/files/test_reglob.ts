import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeRegExp, globToRegExp } from "./reglob.ts";

const SPECIALS = ".*+?^${}()|[]\\";

function matches(glob: string, name: string): boolean {
  return globToRegExp(glob).test(name);
}

test("every special character round-trips through escapeRegExp", () => {
  for (const ch of SPECIALS) {
    const re = new RegExp("^" + escapeRegExp(ch) + "$");
    assert.equal(re.test(ch), true, "should match " + JSON.stringify(ch));
    assert.equal(re.test("x"), false, "matched x for " + JSON.stringify(ch));
    assert.equal(re.test(ch + ch), false, "matched doubled " + JSON.stringify(ch));
  }
});

test("escapeRegExp escapes exactly the listed set, nothing more", () => {
  assert.equal(escapeRegExp("weekly report 7"), "weekly report 7");
  assert.equal(escapeRegExp("a-b/c,d"), "a-b/c,d");
  assert.equal(escapeRegExp("IMG (3).jpeg"), "IMG \\(3\\)\\.jpeg");
  assert.equal(escapeRegExp("50% up^2"), "50% up\\^2");
  assert.equal(escapeRegExp("a\\b"), "a\\\\b");
});

test("escapeRegExp output stays literal inside a bigger pattern", () => {
  const re = new RegExp("^copy of " + escapeRegExp("budget v2.4 (final)") + "$");
  assert.equal(re.test("copy of budget v2.4 (final)"), true);
  assert.equal(re.test("copy of budget v2X4 (final)"), false);
});

test("* stays inside one path segment", () => {
  assert.equal(matches("*.txt", "notes.txt"), true);
  assert.equal(matches("*.txt", ".txt"), true);
  assert.equal(matches("*.txt", "notes.txt.bak"), false);
  assert.equal(matches("*.txt", "drafts/notes.txt"), false);
});

test("? is exactly one non-slash character", () => {
  assert.equal(matches("data-??.csv", "data-07.csv"), true);
  assert.equal(matches("data-??.csv", "data-7.csv"), false);
  assert.equal(matches("data-??.csv", "data-123.csv"), false);
  assert.equal(matches("a?b", "a/b"), false);
});

test("matching is whole-name, not substring", () => {
  assert.equal(matches("notes", "notes"), true);
  assert.equal(matches("notes", "my notes"), false);
  assert.equal(matches("notes", "notes!"), false);
});

test("dots and other punctuation in globs are literal", () => {
  assert.equal(matches("*.log", "app.log"), true);
  assert.equal(matches("*.log", "appxlog"), false);
  assert.equal(matches("x{3}.dat", "x{3}.dat"), true);
  assert.equal(matches("x{3}.dat", "xxx.dat"), false);
  assert.equal(matches("sales (final)+.md", "sales (final)+.md"), true);
  assert.equal(matches("sales (final)+.md", "sales xfinaly+.md"), false);
  assert.equal(matches("a|b.txt", "a|b.txt"), true);
  assert.equal(matches("a|b.txt", "a.txt"), false);
  assert.equal(matches("cost^2.csv", "cost^2.csv"), true);
});

test("character classes: ranges, sets, negation", () => {
  assert.equal(matches("report[0-9].pdf", "report3.pdf"), true);
  assert.equal(matches("report[0-9].pdf", "reportx.pdf"), false);
  assert.equal(matches("v[12].[05]", "v1.0"), true);
  assert.equal(matches("v[12].[05]", "v3.0"), false);
  assert.equal(matches("[!a-m]*.log", "zeta.log"), true);
  assert.equal(matches("[!a-m]*.log", "alpha.log"), false);
  assert.equal(matches("pic[abc].raw", "picb.raw"), true);
  assert.equal(matches("pic[abc].raw", "picd.raw"), false);
});

test("class contents can be escaped", () => {
  assert.equal(matches("lit[\\]]end", "lit]end"), true);
  assert.equal(matches("lit[\\]]end", "litxend"), false);
  assert.equal(matches("[a\\-c]", "-"), true);
  assert.equal(matches("[a\\-c]", "b"), false);
  assert.equal(matches("[a\\-c]", "a"), true);
});

test("glob backslash makes the next character literal", () => {
  assert.equal(matches("\\*.txt", "*.txt"), true);
  assert.equal(matches("\\*.txt", "a.txt"), false);
  assert.equal(matches("say\\?", "say?"), true);
  assert.equal(matches("say\\?", "sayx"), false);
});

test("translation is anchored and slash-safe", () => {
  assert.equal(globToRegExp("*.txt").source, "^[^/]*\\.txt$");
  assert.equal(globToRegExp("a?c").source, "^a[^/]c$");
});

test("bad globs are rejected loudly", () => {
  assert.throws(() => globToRegExp("data[0-9"), /unterminated character class/);
  assert.throws(() => globToRegExp("oops\\"), /trailing backslash/);
  assert.throws(() => globToRegExp("a[]b"), /empty character class/);
});
