import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Inflector, inflector, pluralize, singularize } from './inflect.ts';

test('regular nouns just take an s', () => {
  assert.equal(pluralize('cat'), 'cats');
  assert.equal(pluralize('report'), 'reports');
  assert.equal(singularize('cats'), 'cat');
  assert.equal(singularize('reports'), 'report');
});

test('sibilant endings take es', () => {
  assert.equal(pluralize('bus'), 'buses');
  assert.equal(pluralize('box'), 'boxes');
  assert.equal(pluralize('church'), 'churches');
  assert.equal(pluralize('dish'), 'dishes');
  assert.equal(pluralize('status'), 'statuses');
  assert.equal(singularize('buses'), 'bus');
  assert.equal(singularize('boxes'), 'box');
  assert.equal(singularize('churches'), 'church');
  assert.equal(singularize('dishes'), 'dish');
  assert.equal(singularize('statuses'), 'status');
});

test('consonant + y flips to ies, vowel + y does not', () => {
  assert.equal(pluralize('city'), 'cities');
  assert.equal(pluralize('party'), 'parties');
  assert.equal(pluralize('day'), 'days');
  assert.equal(pluralize('key'), 'keys');
  assert.equal(singularize('cities'), 'city');
  assert.equal(singularize('parties'), 'party');
  assert.equal(singularize('days'), 'day');
  assert.equal(singularize('keys'), 'key');
});

test('f and fe endings become ves and back', () => {
  assert.equal(pluralize('knife'), 'knives');
  assert.equal(pluralize('wife'), 'wives');
  assert.equal(pluralize('life'), 'lives');
  assert.equal(pluralize('leaf'), 'leaves');
  assert.equal(pluralize('wolf'), 'wolves');
  assert.equal(singularize('knives'), 'knife');
  assert.equal(singularize('wives'), 'wife');
  assert.equal(singularize('leaves'), 'leaf');
  assert.equal(singularize('wolves'), 'wolf');
});

test('-is words become -es and back', () => {
  assert.equal(pluralize('analysis'), 'analyses');
  assert.equal(pluralize('crisis'), 'crises');
  assert.equal(singularize('analyses'), 'analysis');
  assert.equal(singularize('crises'), 'crisis');
});

test('irregular nouns come from the irregular table', () => {
  assert.equal(pluralize('person'), 'people');
  assert.equal(pluralize('child'), 'children');
  assert.equal(pluralize('mouse'), 'mice');
  assert.equal(pluralize('foot'), 'feet');
  assert.equal(pluralize('tooth'), 'teeth');
  assert.equal(singularize('people'), 'person');
  assert.equal(singularize('children'), 'child');
  assert.equal(singularize('mice'), 'mouse');
  assert.equal(singularize('feet'), 'foot');
  assert.equal(singularize('teeth'), 'tooth');
});

test('pluralizing an already-plural irregular is a no-op', () => {
  assert.equal(pluralize('people'), 'people');
  assert.equal(pluralize('children'), 'children');
});

test('uncountables never change in either direction', () => {
  for (const word of ['sheep', 'fish', 'series', 'information', 'equipment']) {
    assert.equal(pluralize(word), word, `pluralize(${word})`);
    assert.equal(singularize(word), word, `singularize(${word})`);
  }
});

test('a count of exactly one selects the singular, anything else the plural', () => {
  assert.equal(pluralize('file', 1), 'file');
  assert.equal(pluralize('file', 3), 'files');
  assert.equal(pluralize('file', 0), 'files');
  assert.equal(pluralize('person', 1), 'person');
  assert.equal(pluralize('person', 2), 'people');
});

test('capitalization is preserved', () => {
  assert.equal(pluralize('Person'), 'People');
  assert.equal(pluralize('City'), 'Cities');
  assert.equal(pluralize('CHILD'), 'CHILDREN');
  assert.equal(pluralize('BOX'), 'BOXES');
  assert.equal(singularize('People'), 'Person');
  assert.equal(singularize('CITIES'), 'CITY');
});

test('the default instance and the bound helpers agree', () => {
  assert.equal(inflector.pluralize('box'), pluralize('box'));
  assert.equal(inflector.singularize('boxes'), singularize('boxes'));
});

test('custom irregulars work in both directions on a fresh instance', () => {
  const inf = new Inflector();
  inf.addIrregular('octopus', 'octopi');
  assert.equal(inf.pluralize('octopus'), 'octopi');
  assert.equal(inf.singularize('octopi'), 'octopus');
  assert.equal(inf.pluralize('octopi'), 'octopi');
});

test('custom uncountables freeze a word', () => {
  const inf = new Inflector();
  inf.addUncountable('bread');
  assert.equal(inf.pluralize('bread'), 'bread');
  assert.equal(inf.singularize('bread'), 'bread');
});

test('rules added later beat the built-in table', () => {
  const inf = new Inflector();
  assert.equal(inf.pluralize('index'), 'indexes', 'default table says indexes');
  inf.addPluralRule(/(ind)ex$/i, '$1ices');
  inf.addSingularRule(/(ind)ices$/i, '$1ex');
  assert.equal(inf.pluralize('index'), 'indices');
  assert.equal(inf.singularize('indices'), 'index');
});

test('instances are isolated from each other and from the default', () => {
  const a = new Inflector();
  a.addIrregular('octopus', 'octopi');
  const b = new Inflector();
  assert.equal(b.pluralize('octopus'), 'octopuses');
  assert.equal(pluralize('octopus'), 'octopuses');
});

test('fresh instances still carry the built-in tables', () => {
  const inf = new Inflector();
  assert.equal(inf.pluralize('person'), 'people');
  assert.equal(inf.pluralize('city'), 'cities');
  assert.equal(inf.singularize('wolves'), 'wolf');
  assert.equal(inf.pluralize('sheep'), 'sheep');
});
