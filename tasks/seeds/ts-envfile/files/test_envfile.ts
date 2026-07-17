// test_envfile.ts — acceptance tests for the .env reader/writer.
import { test } from "node:test";
import assert from "node:assert/strict";
import { EnvParseError, parseEnv, serializeEnv } from "./envfile.ts";

test("unquoted values: trimming, empty, glued text", () => {
  const env = parseEnv(
    "HOST=db.internal\n" +
    "PORT=5432\n" +
    "SPACED =  padded value  \n" +
    "EMPTY=\n" +
    "BLANKY=   \n" +
    "APOS=it's fine\n"
  );
  assert.deepEqual(env, {
    HOST: "db.internal",
    PORT: "5432",
    SPACED: "padded value",
    EMPTY: "",
    BLANKY: "",
    APOS: "it's fine",
  });
});

test("dollar signs are kept literal everywhere", () => {
  const env = parseEnv(
    "PROMPT=$HOME is not expanded\n" +
    "SINGLE='cost: $5'\n" +
    'DOUBLE="rate: $12"\n'
  );
  assert.equal(env.PROMPT, "$HOME is not expanded");
  assert.equal(env.SINGLE, "cost: $5");
  assert.equal(env.DOUBLE, "rate: $12");
});

test("comment lines, inline comments and hash-in-value", () => {
  const env = parseEnv(
    "# full line comment\n" +
    "   # indented comment\n" +
    "\n" +
    "HOST=db.internal # primary\n" +
    "TOKEN=abc#def\n" +
    "NOTE= # value is empty, this is comment\n" +
    "QUOTED='keep # this'\n" +
    'DQUOTED="and # this"\n'
  );
  assert.deepEqual(env, {
    HOST: "db.internal",
    TOKEN: "abc#def",
    NOTE: "",
    QUOTED: "keep # this",
    DQUOTED: "and # this",
  });
});

test("single quotes are fully literal", () => {
  const env = parseEnv(
    "WIN='C:\\new\\table.txt'\n" +
    "TWO='a\\nb'\n" +
    "PAD='  kept  '\n"
  );
  assert.equal(env.WIN, "C:\\new\\table.txt"); // backslashes survive untouched
  assert.equal(env.TWO, "a\\nb");              // two chars, not a newline
  assert.equal(env.PAD, "  kept  ");
});

test("double quotes interpret exactly five escapes", () => {
  const env = parseEnv(
    'MOTD="line1\\nline2"\n' +
    'TABBED="a\\tb"\n' +
    'CR="x\\ry"\n' +
    'QUOTE="say \\"hi\\""\n' +
    'BACK="one\\\\two"\n'
  );
  assert.equal(env.MOTD, "line1\nline2");
  assert.equal(env.TABBED, "a\tb");
  assert.equal(env.CR, "x\ry");
  assert.equal(env.QUOTE, 'say "hi"');
  assert.equal(env.BACK, "one\\two");
});

test("the windows-path classic: double quotes eat the path", () => {
  // In the file: WIN="C:\new\table.txt" — inside double quotes \n and \t
  // are escapes, so this parses to C:<newline>ew<tab>able.txt.
  const env = parseEnv('WIN="C:\\new\\table.txt"\n');
  assert.equal(env.WIN, "C:\new\table.txt");
  assert.equal(env.WIN.length, 14); // two escapes each collapsed to one char
  assert.ok(env.WIN.includes("\n"));
});

test("comments allowed after a closing quote", () => {
  const env = parseEnv("NAME='dana'   # on call\nROLE=\"ops\"\t# team\n");
  assert.deepEqual(env, { NAME: "dana", ROLE: "ops" });
});

test("CRLF input and duplicate keys (last wins)", () => {
  const env = parseEnv("A=1\r\nB=2\r\nA=3\r\n");
  assert.deepEqual(env, { A: "3", B: "2" });
});

test("parse errors carry 1-based line numbers", () => {
  assert.throws(
    () => parseEnv("A=1\nBAD LINE\n"),
    (e: any) => e instanceof EnvParseError && e.line === 2
  );
  assert.throws(
    () => parseEnv("A=1\nB=2\n1KEY=x\n"),
    (e: any) => e instanceof EnvParseError && e.line === 3
  );
  assert.throws(
    () => parseEnv("GOOD=1\nOPEN='never closed\n"),
    (e: any) => e instanceof EnvParseError && e.line === 2
  );
  assert.throws(
    () => parseEnv('X="a\\qb"\n'),
    (e: any) => e instanceof EnvParseError && e.line === 1
  );
  assert.throws(
    () => parseEnv("A=1\nY='done'junk\n"),
    (e: any) => e instanceof EnvParseError && e.line === 2
  );
});

test("serializer picks plain, then single, then double — byte exact", () => {
  const out = serializeEnv({
    GREETING: "hello world",
    PASS: "p#ss word",
    MOTD: "line1\nline2",
    WIN_PATH: "C:\\new\\table.txt",
    EMPTY: "",
    OWNER: "O'Brien",
  });
  assert.equal(
    out,
    "GREETING=hello world\n" +
      "PASS='p#ss word'\n" +
      'MOTD="line1\\nline2"\n' +
      "WIN_PATH='C:\\new\\table.txt'\n" +
      "EMPTY=''\n" +
      "OWNER=\"O'Brien\"\n"
  );
});

test("serializer output details", () => {
  assert.equal(serializeEnv({}), "");
  assert.equal(serializeEnv({ A: "1" }), "A=1\n");
  // leading/trailing blanks force quoting
  assert.equal(serializeEnv({ P: " x " }), "P=' x '\n");
  // a value with both quote kinds goes double, escaping only the double
  assert.equal(serializeEnv({ B: `it's "fine"` }), 'B="it\'s \\"fine\\""\n');
  // tab and CR use their short escapes
  assert.equal(serializeEnv({ T: "a\tb\rc" }), 'T="a\\tb\\rc"\n');
});

test("serializer rejects bad keys and unsupported control chars", () => {
  assert.throws(() => serializeEnv({ "1BAD": "x" }), TypeError);
  assert.throws(() => serializeEnv({ "SPACE KEY": "x" }), TypeError);
  assert.throws(() => serializeEnv({ OK: "a\x01b" }), TypeError);
});

test("round trip: parse(serialize(x)) is identity", () => {
  const tricky: Record<string, string> = {
    WORDS: "hello world",
    HASHED: "p#ss word",
    APOS: "O'Brien",
    MULTI: "line1\nline2\nline3",
    WINPATH: "C:\\new\\table.txt",
    EMPTY: "",
    PADDED: "  padded  ",
    TABBED: "a\tb",
    DOLLARS: "$HOME stays",
    LEADHASH: "#leading",
    DQ: 'quote " inside',
    BOTH: `both ' and " quotes`,
    CRLF: "trail\r\nwin",
  };
  assert.deepEqual(parseEnv(serializeEnv(tricky)), tricky);
  // and each value alone survives too
  for (const [k, v] of Object.entries(tricky)) {
    assert.deepEqual(parseEnv(serializeEnv({ [k]: v })), { [k]: v }, k);
  }
});
