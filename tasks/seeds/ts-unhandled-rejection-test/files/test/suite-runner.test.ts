import assert from "node:assert/strict";
import { runSuite } from "../src/suite-runner.ts";

const order: string[] = [];

const results = await runSuite([
  {
    name: "rejecting case",
    async run() {
      await Promise.resolve();
      order.push("rejecting case finished");
      throw new Error("rejection from the first case");
    },
  },
  {
    name: "following case",
    async run() {
      order.push("following case started");
      await Promise.resolve();
    },
  },
]);

assert.deepEqual(results, [
  {
    name: "rejecting case",
    status: "failed",
    error: "rejection from the first case",
  },
  { name: "following case", status: "passed" },
]);

assert.deepEqual(order, [
  "rejecting case finished",
  "following case started",
]);

const delayedOrder: string[] = [];

const delayedResults = await runSuite([
  {
    name: "delayed case",
    async run() {
      await new Promise<void>((resolve) => {
        setImmediate(() => setImmediate(resolve));
      });
      delayedOrder.push("delayed case finished");
    },
  },
  {
    name: "case after delay",
    run() {
      delayedOrder.push("case after delay started");
    },
  },
]);

assert.deepEqual(delayedResults, [
  { name: "delayed case", status: "passed" },
  { name: "case after delay", status: "passed" },
]);

assert.deepEqual(delayedOrder, [
  "delayed case finished",
  "case after delay started",
]);

process.stdout.write("suite-runner tests passed\n");
