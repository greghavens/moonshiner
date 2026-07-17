import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatCalendar, parseCalendar } from './ics.ts';

const CRLF = '\r\n';

test('a bare event formats to the exact wire text with CRLF endings', () => {
  const out = formatCalendar([
    { summary: 'Standup', dtstart: '20260712T090000Z', dtend: '20260712T091500Z' },
  ]);
  assert.equal(
    out,
    [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'BEGIN:VEVENT',
      'SUMMARY:Standup',
      'DTSTART:20260712T090000Z',
      'DTEND:20260712T091500Z',
      'END:VEVENT',
      'END:VCALENDAR',
      '',
    ].join(CRLF),
  );
});

test('an empty event list still produces a valid empty calendar', () => {
  const out = formatCalendar([]);
  assert.equal(out, 'BEGIN:VCALENDAR' + CRLF + 'VERSION:2.0' + CRLF + 'END:VCALENDAR' + CRLF);
  assert.deepEqual(parseCalendar(out), []);
});

test('DESCRIPTION is emitted only when the event has one', () => {
  const out = formatCalendar([
    { summary: 'A', dtstart: '20260101T000000Z', dtend: '20260101T010000Z' },
    { summary: 'B', dtstart: '20260102T000000Z', dtend: '20260102T010000Z', description: 'bring slides' },
  ]);
  const lines = out.split(CRLF);
  assert.equal(lines.filter((l) => l.startsWith('DESCRIPTION')).length, 1);
  assert.ok(out.includes('DESCRIPTION:bring slides'));
});

test('commas and semicolons in text values are backslash-escaped on the wire', () => {
  const out = formatCalendar([
    { summary: 'Lunch, maybe; RSVP', dtstart: '20260301T120000Z', dtend: '20260301T130000Z' },
  ]);
  assert.ok(out.includes('SUMMARY:Lunch\\, maybe\\; RSVP'));
});

test('newlines in a description become literal \\n sequences', () => {
  const out = formatCalendar([
    {
      summary: 'Agenda',
      dtstart: '20260301T120000Z',
      dtend: '20260301T130000Z',
      description: 'first line\nsecond line',
    },
  ]);
  assert.ok(out.includes('DESCRIPTION:first line\\nsecond line'));
  assert.ok(!out.includes('DESCRIPTION:first line\r'));
});

test('backslashes themselves are escaped and survive a round trip', () => {
  const events = [
    {
      summary: 'Deploy C:\\apps\\svc',
      dtstart: '20260301T120000Z',
      dtend: '20260301T130000Z',
      description: 'path is C:\\apps, then; done\nEOM',
    },
  ];
  assert.deepEqual(parseCalendar(formatCalendar(events)), events);
});

test('lines longer than 75 characters are folded', () => {
  const summary = 'Quarterly planning review with the extended platform, infra and data teams plus external stakeholders';
  const out = formatCalendar([
    { summary, dtstart: '20260401T090000Z', dtend: '20260401T100000Z' },
  ]);
  const lines = out.split(CRLF);
  for (const line of lines) {
    assert.ok(line.length <= 75, `line exceeds 75 chars: ${JSON.stringify(line)}`);
  }
  const summaryIdx = lines.findIndex((l) => l.startsWith('SUMMARY:'));
  assert.equal(lines[summaryIdx].length, 75);
  assert.ok(lines[summaryIdx + 1].startsWith(' '));
  assert.ok(!lines[summaryIdx + 1].startsWith('  '));
});

test('folded output parses back to the original text', () => {
  const events = [
    {
      summary: 'S'.repeat(200),
      dtstart: '20260401T090000Z',
      dtend: '20260401T100000Z',
      description: 'd'.repeat(300),
    },
  ];
  assert.deepEqual(parseCalendar(formatCalendar(events)), events);
});

test('parser unfolds continuation lines marked by space or tab', () => {
  const text = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'BEGIN:VEVENT',
    'SUMMARY:week',
    '\tly sync',
    'DTSTART:20260501T0900',
    ' 00Z',
    'DTEND:20260501T093000Z',
    'END:VEVENT',
    'END:VCALENDAR',
  ].join(CRLF);
  assert.deepEqual(parseCalendar(text), [
    { summary: 'weekly sync', dtstart: '20260501T090000Z', dtend: '20260501T093000Z' },
  ]);
});

test('parser accepts LF-only line endings and blank lines between events', () => {
  const text =
    'BEGIN:VCALENDAR\nVERSION:2.0\n\nBEGIN:VEVENT\nSUMMARY:one\nDTSTART:20260601T080000Z\nDTEND:20260601T090000Z\nEND:VEVENT\n\nBEGIN:VEVENT\nSUMMARY:two\nDTSTART:20260602T080000Z\nDTEND:20260602T090000Z\nEND:VEVENT\nEND:VCALENDAR\n';
  const events = parseCalendar(text);
  assert.equal(events.length, 2);
  assert.equal(events[0].summary, 'one');
  assert.equal(events[1].summary, 'two');
});

test('unknown properties and property parameters are tolerated', () => {
  const text = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'BEGIN:VEVENT',
    'UID:abc-123@example.com',
    'SUMMARY;LANGUAGE=en:Team sync',
    'LOCATION:Room 4',
    'DTSTART:20260601T080000Z',
    'DTEND:20260601T090000Z',
    'X-CUSTOM:whatever:with:colons',
    'END:VEVENT',
    'END:VCALENDAR',
  ].join(CRLF);
  assert.deepEqual(parseCalendar(text), [
    { summary: 'Team sync', dtstart: '20260601T080000Z', dtend: '20260601T090000Z' },
  ]);
});

test('escaped text values are unescaped on parse', () => {
  const text = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'BEGIN:VEVENT',
    'SUMMARY:Lunch\\, maybe\\; RSVP',
    'DTSTART:20260601T080000Z',
    'DTEND:20260601T090000Z',
    'DESCRIPTION:line one\\nline two\\\\end',
    'END:VEVENT',
    'END:VCALENDAR',
  ].join(CRLF);
  assert.deepEqual(parseCalendar(text), [
    {
      summary: 'Lunch, maybe; RSVP',
      dtstart: '20260601T080000Z',
      dtend: '20260601T090000Z',
      description: 'line one\nline two\\end',
    },
  ]);
});

test('a multi-event calendar round-trips through format and parse', () => {
  const events = [
    {
      summary: 'Design review: search, filters',
      dtstart: '20260701T140000Z',
      dtend: '20260701T150000Z',
      description: 'Topics:\n- ranking; recall\n- UI polish',
    },
    { summary: 'Focus block', dtstart: '20260702T090000Z', dtend: '20260702T120000Z' },
  ];
  assert.deepEqual(parseCalendar(formatCalendar(events)), events);
});

test('input that does not start with BEGIN:VCALENDAR is rejected', () => {
  assert.throws(() => parseCalendar('hello world'), /BEGIN:VCALENDAR/);
  assert.throws(() => parseCalendar(''), /BEGIN:VCALENDAR/);
});

test('a calendar missing its END:VCALENDAR is rejected', () => {
  const text = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n';
  assert.throws(() => parseCalendar(text), /END:VCALENDAR/);
});

test('an unterminated VEVENT is rejected', () => {
  const text = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'BEGIN:VEVENT',
    'SUMMARY:oops',
    'DTSTART:20260601T080000Z',
    'DTEND:20260601T090000Z',
    'END:VCALENDAR',
  ].join(CRLF);
  assert.throws(() => parseCalendar(text), /END:VEVENT/);
});

test('an event missing SUMMARY is rejected with a message naming the property', () => {
  const text = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'BEGIN:VEVENT',
    'DTSTART:20260601T080000Z',
    'DTEND:20260601T090000Z',
    'END:VEVENT',
    'END:VCALENDAR',
  ].join(CRLF);
  assert.throws(() => parseCalendar(text), /SUMMARY/);
});

test('a content line with no colon is rejected and the message quotes the line', () => {
  const text = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'BEGIN:VEVENT',
    'SUMMARY:ok',
    'THIS-IS-NOT-A-PROPERTY',
    'DTSTART:20260601T080000Z',
    'DTEND:20260601T090000Z',
    'END:VEVENT',
    'END:VCALENDAR',
  ].join(CRLF);
  assert.throws(() => parseCalendar(text), /THIS-IS-NOT-A-PROPERTY/);
});
