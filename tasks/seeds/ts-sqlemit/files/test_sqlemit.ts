// test_sqlemit.ts — acceptance tests for the report seed-script emitter.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { quoteIdent, renderLiteral, renderInsert, writeReport } from "./sqlemit.ts";

test("identifier quoting per dialect", () => {
  assert.equal(quoteIdent("staff", "ansi"), '"staff"');
  assert.equal(quoteIdent("staff", "mysql"), "`staff`");
  assert.equal(quoteIdent("order", "ansi"), '"order"'); // keywords just get quoted
  assert.equal(quoteIdent('report "final"', "ansi"), '"report ""final"""');
  assert.equal(quoteIdent("weird`tbl", "mysql"), "`weird``tbl`");
  // the other dialect's quote char is literal, not doubled
  assert.equal(quoteIdent("weird`tbl", "ansi"), '"weird`tbl"');
  assert.equal(quoteIdent('sel"ect', "mysql"), '`sel"ect`');
  assert.throws(() => quoteIdent("", "ansi"), TypeError);
});

test("string literals double single quotes and nothing else", () => {
  assert.equal(renderLiteral("plain"), "'plain'");
  assert.equal(renderLiteral(""), "''");
  assert.equal(renderLiteral("O'Brien"), "'O''Brien'");
  assert.equal(renderLiteral("''"), "''''''");
  // backslashes are NOT escape characters in these literals
  assert.equal(renderLiteral("C:\\new\\table.txt"), "'C:\\new\\table.txt'");
  // newlines stay literal inside the quotes
  assert.equal(renderLiteral("line1\nline2"), "'line1\nline2'");
});

test("NULL the value versus 'NULL' the string", () => {
  assert.equal(renderLiteral(null), "NULL");
  assert.equal(renderLiteral("NULL"), "'NULL'");
  assert.equal(renderLiteral("null"), "'null'");
});

test("numbers and booleans", () => {
  assert.equal(renderLiteral(42), "42");
  assert.equal(renderLiteral(3.5), "3.5");
  assert.equal(renderLiteral(-17), "-17");
  assert.equal(renderLiteral(-0), "0");
  assert.equal(renderLiteral(true), "TRUE");
  assert.equal(renderLiteral(false), "FALSE");
  assert.throws(() => renderLiteral(NaN), RangeError);
  assert.throws(() => renderLiteral(Infinity), RangeError);
  assert.throws(() => renderLiteral(-Infinity), RangeError);
});

test("bytes become uppercase hex literals", () => {
  assert.equal(renderLiteral(new Uint8Array([0xde, 0xad, 0xbe, 0xef])), "X'DEADBEEF'");
  assert.equal(renderLiteral(new Uint8Array([0, 255, 16])), "X'00FF10'");
  assert.equal(renderLiteral(new Uint8Array([])), "X''");
});

test("unsupported values are rejected", () => {
  assert.throws(() => renderLiteral(undefined as any), TypeError);
  assert.throws(() => renderLiteral({} as any), TypeError);
  assert.throws(() => renderLiteral([1] as any), TypeError);
});

test("renderInsert layout is exact", () => {
  const stmt = renderInsert(
    "staff",
    ["id", "name"],
    [
      [1, "Dana O'Brien"],
      [2, null],
    ],
    "ansi"
  );
  assert.equal(
    stmt,
    'INSERT INTO "staff" ("id", "name") VALUES\n' +
      "  (1, 'Dana O''Brien'),\n" +
      "  (2, NULL);"
  );
});

test("renderInsert honors the dialect for identifiers only", () => {
  const stmt = renderInsert("weird`tbl", ["from", 'sel"ect'], [[1, "x'y"]], "mysql");
  assert.equal(
    stmt,
    "INSERT INTO `weird``tbl` (`from`, `sel\"ect`) VALUES\n" +
      "  (1, 'x''y');"
  );
});

test("renderInsert validates its shape", () => {
  assert.throws(() => renderInsert("t", [], [[1]], "ansi"), Error);
  assert.throws(() => renderInsert("t", ["a"], [], "ansi"), Error);
  assert.throws(() => renderInsert("t", ["a", "b"], [[1]], "ansi"), Error);
  assert.throws(() => renderInsert("t", ["a"], [[1], [2, 3]], "ansi"), Error);
});

test("writeReport emits the whole .sql file byte-exact", () => {
  const dir = "test_out_sqlemit";
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "report.sql");
  try {
    writeReport(
      path,
      [
        {
          table: "staff",
          columns: ["id", "name", "note", "active", "badge"],
          rows: [
            [1, "Dana O'Brien", "night shift\nkeys in locker", true,
             new Uint8Array([0xde, 0xad, 0xbe, 0xef])],
            [2, "NULL", null, false, new Uint8Array([])],
          ],
        },
        {
          table: "metrics",
          columns: ["day", "reading"],
          rows: [
            ["2026-07-01", 3.5],
            ["2026-07-02", 0],
          ],
        },
      ],
      "ansi"
    );
    const got = readFileSync(path);
    const want = Buffer.from(
      'INSERT INTO "staff" ("id", "name", "note", "active", "badge") VALUES\n' +
        "  (1, 'Dana O''Brien', 'night shift\nkeys in locker', TRUE, X'DEADBEEF'),\n" +
        "  (2, 'NULL', NULL, FALSE, X'');\n" +
        "\n" +
        'INSERT INTO "metrics" ("day", "reading") VALUES\n' +
        "  ('2026-07-01', 3.5),\n" +
        "  ('2026-07-02', 0);\n",
      "utf8"
    );
    assert.deepEqual(got, want);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("writeReport with no tables writes an empty file", () => {
  const dir = "test_out_sqlemit_empty";
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "empty.sql");
  try {
    writeReport(path, [], "ansi");
    assert.deepEqual(readFileSync(path), Buffer.from("", "utf8"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
