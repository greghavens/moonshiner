import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deliveryRate, fanout } from './fanout.ts';
import type { SendFn } from './fanout.ts';

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const byUrl = (a: { url: string }, b: { url: string }) => a.url.localeCompare(b.url);

test('all healthy endpoints are delivered and none fail', async () => {
  const seen: string[] = [];
  const send: SendFn = async (url) => {
    await sleep(2);
    seen.push(url);
  };
  const report = await fanout(['https://a.test/hook', 'https://b.test/hook'], '{"e":1}', send);
  assert.deepEqual([...report.delivered].sort(), ['https://a.test/hook', 'https://b.test/hook']);
  assert.deepEqual(report.failed, []);
  assert.equal(deliveryRate(report), 1);
});

test('one dead endpoint does not take down the rest of the batch', async () => {
  const send: SendFn = async (url) => {
    if (url.includes('bad')) throw new Error('connect ECONNREFUSED');
    await sleep(2);
  };
  const report = await fanout(
    ['https://a.test/hook', 'https://bad.test/hook', 'https://c.test/hook'],
    '{"e":2}',
    send,
  );
  assert.deepEqual([...report.delivered].sort(), ['https://a.test/hook', 'https://c.test/hook']);
  assert.deepEqual(report.failed, [
    { url: 'https://bad.test/hook', reason: 'connect ECONNREFUSED' },
  ]);
});

test('each failing endpoint is reported with its own reason', async () => {
  const send: SendFn = async (url) => {
    if (url.includes('alpha')) throw new Error('dns lookup failed');
    if (url.includes('beta')) {
      await sleep(2);
      throw new Error('500 Internal Server Error');
    }
    await sleep(4);
  };
  const report = await fanout(
    ['https://alpha.test/hook', 'https://beta.test/hook', 'https://gamma.test/hook'],
    '{"e":3}',
    send,
  );
  assert.deepEqual(report.delivered, ['https://gamma.test/hook']);
  assert.deepEqual([...report.failed].sort(byUrl), [
    { url: 'https://alpha.test/hook', reason: 'dns lookup failed' },
    { url: 'https://beta.test/hook', reason: '500 Internal Server Error' },
  ]);
});

test('the report is final the moment fanout resolves', async () => {
  const send: SendFn = async (url) => {
    if (url.endsWith('/down')) throw new Error('410 Gone');
    await sleep(5);
  };
  const report = await fanout(['https://ok.test/hook', 'https://ok2.test/down'], 'x', send);
  const deliveredAtResolve = report.delivered.length;
  const failedAtResolve = report.failed.length;
  await sleep(25);
  assert.equal(report.delivered.length, deliveredAtResolve,
    'delivered list changed after the report was returned');
  assert.equal(report.failed.length, failedAtResolve,
    'failed list changed after the report was returned');
  assert.deepEqual(report.delivered, ['https://ok.test/hook']);
});

test('delivered and failed entries retain completion order', async () => {
  const delays = new Map([
    ['https://slow-ok.test/hook', 24],
    ['https://slow-bad.test/hook', 18],
    ['https://fast-ok.test/hook', 4],
    ['https://fast-bad.test/hook', 1],
  ]);
  const send: SendFn = async (url) => {
    await sleep(delays.get(url)!);
    if (url.includes('bad')) throw new Error(`failed ${url}`);
  };
  const report = await fanout([...delays.keys()], 'x', send);
  assert.deepEqual(report.delivered, [
    'https://fast-ok.test/hook',
    'https://slow-ok.test/hook',
  ]);
  assert.deepEqual(report.failed, [
    { url: 'https://fast-bad.test/hook', reason: 'failed https://fast-bad.test/hook' },
    { url: 'https://slow-bad.test/hook', reason: 'failed https://slow-bad.test/hook' },
  ]);
});

test('a synchronous sender throw is isolated to its endpoint', async () => {
  const send: SendFn = (url) => {
    if (url.includes('sync-bad')) throw new Error('synchronous setup failure');
    return Promise.resolve();
  };
  const report = await fanout(
    ['https://ok.test/hook', 'https://sync-bad.test/hook', 'https://ok2.test/hook'],
    'x',
    send,
  );
  assert.deepEqual([...report.delivered].sort(), [
    'https://ok.test/hook',
    'https://ok2.test/hook',
  ]);
  assert.deepEqual(report.failed, [
    { url: 'https://sync-bad.test/hook', reason: 'synchronous setup failure' },
  ]);
});
