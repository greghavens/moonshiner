import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadSchema } from './schema.ts';
import { Wizard } from './wizard.ts';

const valid = () => ({
  pages: [
    {
      id: 'account',
      fields: [
        { id: 'email', type: 'text', required: true },
        { id: 'plan', type: 'choice', required: true, options: ['free', 'pro'] },
      ],
    },
    {
      id: 'billing',
      showIf: { field: 'plan', equals: 'pro' },
      fields: [{ id: 'card', type: 'text', required: true }],
    },
  ],
});

test('a well-formed schema loads', () => {
  const schema = loadSchema(valid());
  assert.equal(schema.pages.length, 2);
});

test('a schema needs at least one page', () => {
  assert.throws(() => loadSchema({ pages: [] }), /page/);
});

test('duplicate page ids are named', () => {
  const def = valid();
  def.pages[1].id = 'account';
  assert.throws(() => loadSchema(def), /account/);
});

test('a page needs at least one field', () => {
  const def = valid();
  def.pages[1].fields = [];
  assert.throws(() => loadSchema(def), /billing/);
});

test('field ids must be unique across the whole schema', () => {
  const def = valid();
  def.pages[1].fields[0].id = 'email';
  assert.throws(() => loadSchema(def), /email/);
});

test('choice fields need a non-empty options list', () => {
  const def = valid();
  delete (def.pages[0].fields[1] as { options?: string[] }).options;
  assert.throws(() => loadSchema(def), /plan/);
  const def2 = valid();
  def2.pages[0].fields[1].options = [];
  assert.throws(() => loadSchema(def2), /plan/);
});

test('unknown field types are named', () => {
  const def = valid();
  def.pages[0].fields[0].type = 'daterange';
  assert.throws(() => loadSchema(def), /daterange/);
});

test('showIf must point at a field from an earlier page', () => {
  const unknown = valid();
  unknown.pages[1].showIf = { field: 'ghost', equals: true } as never;
  assert.throws(() => loadSchema(unknown), /ghost/);

  const samePage = valid();
  samePage.pages[1].showIf = { field: 'card', equals: 'x' } as never;
  assert.throws(() => loadSchema(samePage), /card/);

  const laterPage = valid();
  laterPage.pages[0].showIf = { field: 'card', equals: 'x' } as never;
  assert.throws(() => loadSchema(laterPage), /card/);
});

test('the wizard refuses a broken schema up front', () => {
  const def = valid();
  def.pages[1].id = 'account';
  assert.throws(() => new Wizard(def), /account/);
});
