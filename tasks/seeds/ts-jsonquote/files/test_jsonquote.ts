import { test } from "node:test";
import assert from "node:assert/strict";
import { encodeJsonString, eventLine } from "./jsonquote.ts";

test("plain text is just quoted", () => {
  assert.equal(encodeJsonString("door opened"), '"door opened"');
  assert.equal(encodeJsonString(""), '""');
});

test("quotes and backslashes", () => {
  assert.equal(encodeJsonString('say "hi"'), '"say \\"hi\\""');
  assert.equal(encodeJsonString("C:\\new\\table.txt"), '"C:\\\\new\\\\table.txt"');
});

test("the five short escapes", () => {
  assert.equal(encodeJsonString("\b\t\n\f\r"), '"\\b\\t\\n\\f\\r"');
});

test("remaining C0 controls use four-digit lowercase hex", () => {
  assert.equal(
    encodeJsonString("\u0000\u0001\u000b\u001f"),
    '"\\u0000\\u0001\\u000b\\u001f"'
  );
  assert.equal(encodeJsonString("bell\u0007"), '"bell\\u0007"');
  assert.equal(encodeJsonString(" "), '" "'); // space is NOT a control
});

test("line and paragraph separators are escaped for inline-script safety", () => {
  assert.equal(encodeJsonString("a\u2028b\u2029c"), '"a\\u2028b\\u2029c"');
  assert.equal(encodeJsonString("‧"), '"‧"'); // the neighbour stays literal
});

test("non-ascii text passes through literally", () => {
  assert.equal(encodeJsonString("café 東京 é"), '"café 東京 é"');
  assert.equal(encodeJsonString("😀"), '"😀"'); // proper surrogate pair
});

test("lone surrogates are replaced with U+FFFD", () => {
  assert.equal(encodeJsonString("\ud800"), '"�"');
  assert.equal(encodeJsonString("a\udc00b"), '"a�b"');
  assert.equal(encodeJsonString("\ud800\ud800"), '"��"');
  assert.equal(encodeJsonString("x\udfffy\ud900"), '"x�y�"');
  assert.equal(encodeJsonString("\udc00😀"), '"�😀"');
});

test("well-formed output parses back with JSON.parse", () => {
  const samples = [
    "plain",
    'q"q',
    "back\\slash",
    "tab\tnl\n",
    "café 😀",
    "u\u2028v\u2029w",
    "\u0003",
  ];
  for (const s of samples) {
    assert.equal(JSON.parse(encodeJsonString(s)), s);
  }
});

test("eventLine emits one compact object with sorted keys", () => {
  assert.equal(
    eventLine({ door: "D-2", note: 'left "ajar"\n' }),
    '{"door":"D-2","note":"left \\"ajar\\"\\n"}'
  );
  assert.equal(eventLine({}), "{}");
  assert.equal(
    eventLine({ b: "2", a: "1", c: "\u2028" }),
    '{"a":"1","b":"2","c":"\\u2028"}'
  );
  assert.equal(eventLine({ "k\t1": "v" }), '{"k\\t1":"v"}');
});
