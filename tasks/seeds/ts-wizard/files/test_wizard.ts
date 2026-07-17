import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wizard } from './wizard.ts';

const signup = () => ({
  pages: [
    {
      id: 'account',
      fields: [
        { id: 'email', type: 'text', required: true, pattern: '^\\S+@\\S+$' },
        { id: 'plan', type: 'choice', required: true, options: ['free', 'pro'] },
      ],
    },
    {
      id: 'billing',
      showIf: { field: 'plan', equals: 'pro' },
      fields: [
        { id: 'card', type: 'text', required: true, minLength: 12 },
        { id: 'seats', type: 'number', required: true, min: 1, max: 50 },
      ],
    },
    {
      id: 'profile',
      fields: [
        { id: 'nickname', type: 'text', maxLength: 8 },
        { id: 'updates', type: 'boolean' },
      ],
    },
  ],
});

test('the wizard opens on the first page', () => {
  const w = new Wizard(signup());
  assert.equal(w.current().id, 'account');
});

test('invalid answers keep you on the page and report per field, in page order', () => {
  const w = new Wizard(signup());
  const r = w.next({ email: 'not-an-email', plan: 'gold' });
  assert.equal(r.ok, false);
  assert.deepEqual(
    (r as { errors: { field: string }[] }).errors.map((e) => e.field),
    ['email', 'plan'],
  );
  assert.equal(w.current().id, 'account');
});

test('a required field left unanswered blocks the page', () => {
  const w = new Wizard(signup());
  const r = w.next({ email: 'a@b.com' });
  assert.equal(r.ok, false);
  const errors = (r as { errors: { field: string; messages: string[] }[] }).errors;
  assert.deepEqual(errors.map((e) => e.field), ['plan']);
  assert.match(errors[0].messages[0], /required/);
});

test('answers not belonging to the current page are refused by name', () => {
  const w = new Wizard(signup());
  assert.throws(() => w.next({ email: 'a@b.com', plan: 'free', card: 'x' }), /card/);
});

test('picking free skips the conditional billing page', () => {
  const w = new Wizard(signup());
  assert.deepEqual(w.next({ email: 'a@b.com', plan: 'free' }), { ok: true, done: false });
  assert.equal(w.current().id, 'profile');
});

test('picking pro routes through billing', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'pro' });
  assert.equal(w.current().id, 'billing');
});

test('finishing the last page reports done; the wizard is then closed', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'free' });
  assert.deepEqual(w.next({ nickname: 'sam' }), { ok: true, done: true });
  assert.throws(() => w.current(), /complete|done/i);
  assert.throws(() => w.next({}), /complete|done/i);
});

test('back returns to the previous visible page with answers intact', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'free' });
  w.back();
  assert.equal(w.current().id, 'account');
  assert.deepEqual(w.pageAnswers('account'), { email: 'a@b.com', plan: 'free' });
});

test('back skips pages that are hidden under the current answers', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'free' });
  assert.equal(w.current().id, 'profile');
  w.back();
  assert.equal(w.current().id, 'account');
});

test('back before the first page is an error', () => {
  const w = new Wizard(signup());
  assert.throws(() => w.back(), /first|back/i);
});

test('back reopens a finished wizard on its last visible page', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'free' });
  w.next({});
  w.back();
  assert.equal(w.current().id, 'profile');
});

test('resubmitting a revisited page with no changes reuses the stored answers', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'pro' });
  w.back();
  assert.deepEqual(w.next({}), { ok: true, done: false });
  assert.equal(w.current().id, 'billing');
});

test('partial resubmission merges over what was already there', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'pro' });
  w.back();
  w.next({ plan: 'free' });
  assert.equal(w.current().id, 'profile');
  assert.deepEqual(w.pageAnswers('account'), { email: 'a@b.com', plan: 'free' });
});

test('a hidden page keeps its answers in the drawer but out of the export', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'pro' });
  w.next({ card: '4111111111111111', seats: 5 });
  w.back();
  w.back();
  w.next({ plan: 'free' });
  assert.equal(w.current().id, 'profile');
  assert.deepEqual(w.export(), { email: 'a@b.com', plan: 'free' });
  assert.deepEqual(w.pageAnswers('billing'), { card: '4111111111111111', seats: 5 });
  w.back();
  w.next({ plan: 'pro' });
  assert.equal(w.current().id, 'billing');
  assert.deepEqual(w.pageAnswers('billing'), { card: '4111111111111111', seats: 5 });
});

test('export lists answered fields of visible pages in schema order', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'pro' });
  w.next({ card: '4111111111111111', seats: 5 });
  w.next({ updates: true });
  assert.deepEqual(Object.keys(w.export()), ['email', 'plan', 'card', 'seats', 'updates']);
  assert.equal(w.export().nickname, undefined);
});

test('export and pageAnswers hand out copies', () => {
  const w = new Wizard(signup());
  w.next({ email: 'a@b.com', plan: 'free' });
  const exp = w.export();
  exp.email = 'evil@example.com';
  assert.equal(w.export().email, 'a@b.com');
  const page = w.pageAnswers('account');
  page.plan = 'pro';
  assert.equal(w.pageAnswers('account').plan, 'free');
});

test('pageAnswers refuses unknown pages by name', () => {
  const w = new Wizard(signup());
  assert.throws(() => w.pageAnswers('checkout'), /checkout/);
});

test('showIf supports an in-list of values', () => {
  const w = new Wizard({
    pages: [
      {
        id: 'start',
        fields: [{ id: 'role', type: 'choice', required: true, options: ['dev', 'ops', 'pm'] }],
      },
      {
        id: 'oncall',
        showIf: { field: 'role', in: ['dev', 'ops'] },
        fields: [{ id: 'pager', type: 'text', required: true }],
      },
      { id: 'wrap', fields: [{ id: 'notes', type: 'text' }] },
    ],
  });
  w.next({ role: 'pm' });
  assert.equal(w.current().id, 'wrap');
  w.back();
  w.next({ role: 'ops' });
  assert.equal(w.current().id, 'oncall');
});
