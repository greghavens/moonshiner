import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { TeamResult } from './standings.ts';
import { formatBoard, podium, positionOf, rankTeams } from './standings.ts';

function entry(team: string, points: number, finishedAt: number): TeamResult {
  return { team, points, finishedAt };
}

test('standings order teams by points, best first', () => {
  const night = [
    entry('Sharks', 31, 100),
    entry('Owls', 47, 90),
    entry('Foxes', 52, 80),
    entry('Bears', 40, 70),
    entry('Crows', 61, 60),
  ];
  assert.deepEqual(
    rankTeams(night).map((r) => r.team),
    ['Crows', 'Foxes', 'Owls', 'Bears', 'Sharks'],
  );
});

test('teams tied on points rank by earlier hand-in', () => {
  const night = [
    entry('Night Shift', 50, 200),
    entry('Quizzly Bears', 50, 100),
    entry('Sofa Kings', 50, 150),
  ];
  assert.deepEqual(
    rankTeams(night).map((r) => r.team),
    ['Quizzly Bears', 'Sofa Kings', 'Night Shift'],
  );
});

test('points and hand-in time combine across a realistic night', () => {
  const night = [
    entry('Alpha', 10, 5),
    entry('Bravo', 30, 5),
    entry('Chill', 20, 5),
    entry('Delta', 30, 1),
    entry('Echo', 20, 2),
  ];
  assert.deepEqual(
    rankTeams(night).map((r) => r.team),
    ['Delta', 'Bravo', 'Echo', 'Chill', 'Alpha'],
  );
});

test('rankTeams leaves the input array untouched', () => {
  const night = [entry('Zed', 5, 10), entry('Amp', 9, 10)];
  rankTeams(night);
  assert.deepEqual(night.map((r) => r.team), ['Zed', 'Amp']);
});

test('podium names the top three, winner first', () => {
  const night = [
    entry('Sharks', 31, 100),
    entry('Owls', 47, 90),
    entry('Foxes', 52, 80),
    entry('Bears', 40, 70),
    entry('Crows', 61, 60),
  ];
  assert.deepEqual(podium(night), ['Crows', 'Foxes', 'Owls']);
});

test('positionOf reports the 1-based standing', () => {
  const night = [
    entry('Night Shift', 50, 200),
    entry('Quizzly Bears', 50, 100),
    entry('Sofa Kings', 50, 150),
    entry('Walk-ins', 12, 90),
  ];
  assert.equal(positionOf(night, 'Quizzly Bears'), 1);
  assert.equal(positionOf(night, 'Night Shift'), 3);
  assert.equal(positionOf(night, 'Walk-ins'), 4);
  assert.equal(positionOf(night, 'No Shows'), -1);
});

test('the printed board lists teams in standing order', () => {
  const night = [
    entry('Sharks', 31, 100),
    entry('Crows', 61, 60),
  ];
  assert.deepEqual(formatBoard(night), [
    ' 1. Crows   61',
    ' 2. Sharks  31',
  ]);
});
