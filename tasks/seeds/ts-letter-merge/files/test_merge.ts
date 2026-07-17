import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fillTemplate, isFullyRedacted, missingPlaceholders, redact } from './merge.ts';

const LETTER = [
  'Dear {{candidate}},',
  'We are delighted to offer you the {{role}} position.',
  'As {{role}}, you will report to the VP of Engineering.',
  'Your starting salary will be {{salary}} per year.',
  'This offer of {{salary}} expires on {{deadline}}.',
  'Welcome aboard, {{candidate}}!',
  "-- People Ops, for {{candidate}}'s future team",
].join('\n');

const VARS = {
  candidate: 'Casey Harper',
  role: 'Staff Engineer',
  salary: '$185,000',
  deadline: 'July 31',
};

function countOf(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

test('every occurrence of every placeholder gets filled', () => {
  const filled = fillTemplate(LETTER, VARS);
  assert.ok(!filled.includes('{{'), `unfilled placeholder left behind:\n${filled}`);
  assert.equal(countOf(filled, 'Casey Harper'), 3);
  assert.equal(countOf(filled, 'Staff Engineer'), 2);
  assert.equal(countOf(filled, '$185,000'), 2);
  assert.equal(countOf(filled, 'July 31'), 1);
});

test('filling is idempotent for vars that do not appear', () => {
  const filled = fillTemplate('No placeholders here.', VARS);
  assert.equal(filled, 'No placeholders here.');
});

test('missingPlaceholders lists exactly what vars lack', () => {
  assert.deepEqual(missingPlaceholders(LETTER, VARS), []);
  assert.deepEqual(
    missingPlaceholders(LETTER, { candidate: 'Casey Harper' }),
    ['deadline', 'role', 'salary'],
  );
});

test('redaction masks every occurrence of a plain term', () => {
  const text = 'Casey Harper accepted. Casey Harper starts Monday. Go Casey Harper!';
  const out = redact(text, ['Casey Harper']);
  assert.ok(!out.includes('Casey Harper'), out);
  assert.equal(countOf(out, '█'.repeat('Casey Harper'.length)), 3);
});

test('redaction masks emails, comp figures, and phone numbers', () => {
  const filled = fillTemplate(LETTER, VARS);
  const contact = `${filled}\nQuestions? Write casey.h+offers@example.com or call (415) 555-0143.`;
  const terms = ['Casey Harper', 'casey.h+offers@example.com', '$185,000', '(415) 555-0143'];
  const out = redact(contact, terms);
  for (const term of terms) {
    assert.ok(!out.includes(term), `"${term}" survived redaction:\n${out}`);
  }
  assert.ok(isFullyRedacted(out, terms));
});

test('masked spans keep the original length', () => {
  const out = redact('base pay: $9,500 monthly', ['$9,500']);
  assert.equal(out, 'base pay: ██████ monthly');
});
