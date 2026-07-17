import { test } from "node:test";
import assert from "node:assert/strict";
import {
  heading,
  changeLine,
  snippetBlock,
  sectionTitle,
  footer,
  render,
} from "./relnotes.ts";

const NOTES = {
  version: "2.4.0",
  date: "2026-07-01",
  changes: [
    { kind: "fixed", text: "Retry uploads on flaky links", pr: 412 },
    { kind: "added", text: "Dark-mode announcement banner", pr: 398 },
    { kind: "fixed", text: "Stop double-posting to the feed", pr: 415 },
  ],
  snippets: [{ lang: "ts", code: ["const html = render(notes);"] }],
};

test("heading carries version and date", () => {
  assert.equal(heading("2.4.0", "2026-07-01"), "## 2.4.0 (2026-07-01)");
});

test("change lines reference the PR", () => {
  assert.equal(
    changeLine({ kind: "fixed", text: "Retry uploads on flaky links", pr: 412 }),
    "- Retry uploads on flaky links (#412)"
  );
});

test("snippet blocks are fenced with the language tag", () => {
  assert.deepEqual(snippetBlock({ lang: "ts", code: ["const a = 1;", "use(a);"] }), [
    "```ts",
    "const a = 1;",
    "use(a);",
    "```",
  ]);
});

test("empty snippets still fence correctly", () => {
  assert.deepEqual(snippetBlock({ lang: "sh", code: [] }), ["```sh", "```"]);
});

test("section titles are capitalized", () => {
  assert.equal(sectionTitle("fixed"), "### Fixed");
});

test("render assembles the whole document in kind order", () => {
  const expected = [
    "## 2.4.0 (2026-07-01)",
    "",
    "### Added",
    "- Dark-mode announcement banner (#398)",
    "",
    "### Fixed",
    "- Retry uploads on flaky links (#412)",
    "- Stop double-posting to the feed (#415)",
    "",
    "```ts",
    "const html = render(notes);",
    "```",
    "",
    footer(),
  ].join("\n");
  assert.equal(render(NOTES), expected);
});

test("render skips empty sections and snippet lists", () => {
  const bare = { version: "2.4.1", date: "2026-07-08", changes: [], snippets: [] };
  assert.equal(render(bare), ["## 2.4.1 (2026-07-08)", "", footer()].join("\n"));
});
