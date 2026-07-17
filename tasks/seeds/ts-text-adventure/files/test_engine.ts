import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Game } from './engine.ts';

// Fresh copy per test — the engine mutates room items as things are picked up.
function makeWorld() {
  return {
    start: 'hall',
    win: { room: 'hall', item: 'golden idol' },
    rooms: {
      hall: {
        name: 'Entrance Hall',
        description: 'Cobwebs hang from the rafters.',
        exits: {
          north: 'library',
          down: { to: 'cellar', requires: 'brass key', lockedMessage: 'An iron gate blocks the stairs.' },
        },
        items: ['lantern'],
      },
      library: {
        name: 'The Library',
        description: 'Shelves sag under mouldy books.',
        exits: { south: 'hall' },
        items: ['brass key', 'dusty tome'],
      },
      cellar: {
        name: 'The Cellar',
        description: 'It smells of wet stone.',
        exits: { up: 'hall', east: 'vault' },
      },
      vault: {
        name: 'The Vault',
        description: 'A pedestal stands in the middle.',
        exits: { west: 'cellar' },
        items: ['golden idol'],
      },
    },
  };
}

const HALL = 'Entrance Hall\nCobwebs hang from the rafters.\nYou see: lantern.\nExits: down, north.';
const LIBRARY = 'The Library\nShelves sag under mouldy books.\nYou see: brass key, dusty tome.\nExits: south.';
const CELLAR = 'The Cellar\nIt smells of wet stone.\nExits: east, up.';
const VAULT = 'The Vault\nA pedestal stands in the middle.\nYou see: golden idol.\nExits: west.';

test('look renders name, description, items and sorted exits', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('look'), HALL);
  assert.equal(g.location(), 'hall');
});

test('rooms without items skip the You see line', () => {
  const g = new Game({
    start: 'void',
    win: { room: 'void', item: 'nothing' },
    rooms: { void: { name: 'The Void', description: 'Nothing here.', exits: {} } },
  });
  assert.equal(g.execute('look'), 'The Void\nNothing here.\nExits: none.');
});

test('moving through an open exit returns the new room description', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('go north'), LIBRARY);
  assert.equal(g.location(), 'library');
  assert.equal(g.execute('go south'), HALL);
});

test('bare compass letters work end to end', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('n'), LIBRARY);
  assert.equal(g.location(), 'library');
});

test('a missing exit is refused politely', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('go west'), "You can't go west.");
  assert.equal(g.execute('w'), "You can't go west.");
  assert.equal(g.location(), 'hall');
});

test('locked exits use their custom message until the key is carried', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('go down'), 'An iron gate blocks the stairs.');
  assert.equal(g.location(), 'hall');
  g.execute('go north');
  g.execute('take brass key');
  g.execute('go south');
  assert.equal(g.execute('go down'), CELLAR);
  assert.equal(g.location(), 'cellar');
});

test('locked exits fall back to the default message', () => {
  const g = new Game({
    start: 'dock',
    win: { room: 'boat', item: 'ticket' },
    rooms: {
      dock: { name: 'The Dock', description: 'Gulls wheel overhead.', exits: { east: { to: 'boat', requires: 'ticket' } } },
      boat: { name: 'The Boat', description: 'It rocks gently.', exits: { west: 'dock' } },
    },
  });
  assert.equal(g.execute('go east'), 'The way east is locked.');
});

test('take moves an item from the room into inventory', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('take lantern'), 'You take the lantern.');
  assert.deepEqual(g.inventory(), ['lantern']);
  // the room no longer lists it
  assert.equal(g.execute('look'), 'Entrance Hall\nCobwebs hang from the rafters.\nExits: down, north.');
  assert.equal(g.execute('take lantern'), 'There is no lantern here.');
});

test('take reports missing items', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('take idol'), 'There is no idol here.');
  assert.deepEqual(g.inventory(), []);
});

test('drop puts a carried item into the current room', () => {
  const g = new Game(makeWorld());
  g.execute('take lantern');
  g.execute('go north');
  assert.equal(g.execute('drop lantern'), 'You drop the lantern.');
  assert.deepEqual(g.inventory(), []);
  assert.equal(
    g.execute('look'),
    'The Library\nShelves sag under mouldy books.\nYou see: brass key, dusty tome, lantern.\nExits: south.',
  );
});

test('dropping something you do not carry', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('drop sword'), "You aren't carrying a sword.");
});

test('inventory lists carried items sorted, or says it is empty', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('inventory'), 'You are carrying nothing.');
  g.execute('go north');
  g.execute('take dusty tome');
  g.execute('take brass key');
  assert.equal(g.execute('i'), 'You are carrying: brass key, dusty tome.');
});

test('inventory() returns a copy', () => {
  const g = new Game(makeWorld());
  g.execute('take lantern');
  const inv = g.inventory();
  inv.push('forged sword');
  assert.deepEqual(g.inventory(), ['lantern']);
});

test('empty and unknown input', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('   '), 'Say something.');
  assert.equal(g.execute('dance'), 'I don\'t understand "dance".');
  assert.equal(g.execute('  WAVE the  Lantern '), 'I don\'t understand "wave the lantern".');
});

test('the full quest: fetch the idol, bring it home', () => {
  const g = new Game(makeWorld());
  assert.equal(g.execute('go down'), 'An iron gate blocks the stairs.');
  assert.equal(g.execute('n'), LIBRARY);
  assert.equal(g.execute('take brass key'), 'You take the brass key.');
  assert.equal(g.execute('s'), HALL);
  assert.equal(g.execute('go down'), CELLAR);
  assert.equal(g.execute('e'), VAULT);
  assert.equal(g.isWon(), false);
  assert.equal(g.execute('take golden idol'), 'You take the golden idol.');
  assert.equal(g.isWon(), false); // idol in hand, but not home yet
  assert.equal(g.execute('w'), CELLAR);
  assert.equal(
    g.execute('go up'),
    'Entrance Hall\nCobwebs hang from the rafters.\nYou see: lantern.\nExits: down, north.\n\nYou have won!',
  );
  assert.equal(g.isWon(), true);
  assert.equal(g.execute('look'), 'The adventure is over.');
  assert.equal(g.execute('go north'), 'The adventure is over.');
  assert.equal(g.location(), 'hall');
});

test('winning by taking the item inside the win room', () => {
  const world = makeWorld();
  world.win = { room: 'vault', item: 'golden idol' };
  const g = new Game(world);
  g.execute('go north');
  g.execute('take brass key');
  g.execute('go south');
  g.execute('go down');
  g.execute('go east');
  assert.equal(g.execute('take golden idol'), 'You take the golden idol.\n\nYou have won!');
  assert.equal(g.isWon(), true);
  assert.equal(g.execute('i'), 'The adventure is over.');
});

test('worlds referencing unknown rooms are rejected at construction', () => {
  const noStart = makeWorld();
  noStart.start = 'nowhere';
  assert.throws(() => new Game(noStart), /unknown room "nowhere"/);

  const badExit = makeWorld();
  (badExit.rooms.hall.exits as Record<string, unknown>).up = 'attic';
  assert.throws(() => new Game(badExit), /unknown room "attic"/);

  const badWin = makeWorld();
  badWin.win = { room: 'penthouse', item: 'golden idol' };
  assert.throws(() => new Game(badWin), /unknown room "penthouse"/);
});
