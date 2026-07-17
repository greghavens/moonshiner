// Acceptance tests for the SQS batch worker feature.
//
// A fake client implementing the SDK's send() surface is injected; every
// dispatched command is an instance of the real @aws-sdk/client-sqs command
// classes, and the inputs asserted here are the wire contract pinned in
// docs/contract.json. No network, no real credentials.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  ChangeMessageVisibilityBatchCommand,
  DeleteMessageBatchCommand,
  DeleteMessageCommand,
  ReceiveMessageCommand,
  type Message,
} from "@aws-sdk/client-sqs";

import { processOne } from "./worker.ts";
import {
  MAX_BATCH_ENTRIES,
  MAX_RECEIVE_MESSAGES,
  MAX_WAIT_TIME_SECONDS,
  chunkEntries,
  deleteMessages,
  extendVisibility,
  receiveBatch,
  runOnce,
} from "./batch_worker.ts";

const CONTRACT = JSON.parse(
  readFileSync(new URL("./docs/contract.json", import.meta.url), "utf8"),
);

const QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/177715257436/render-jobs";

interface Call {
  name: string;
  input: any;
}

class FakeSQS {
  calls: Call[] = [];
  private scripts: Array<(cmd: any) => any> = [];

  script(fn: (cmd: any) => any): void {
    this.scripts.push(fn);
  }

  callsOf(name: string): Call[] {
    return this.calls.filter((c) => c.name === name);
  }

  async send(cmd: any): Promise<any> {
    this.calls.push({ name: cmd.constructor.name, input: cmd.input });
    const fn = this.scripts.shift();
    if (!fn) {
      throw new Error(`unexpected command: ${cmd.constructor.name}`);
    }
    return fn(cmd);
  }
}

function msg(n: number): Message {
  return {
    MessageId: `m${n}`,
    ReceiptHandle: `rh-${n}-AQEBzJna${n}==`,
    Body: JSON.stringify({ job: n }),
    MD5OfBody: `d41d8cd98f00b204e980099${n}`,
  };
}

function entry(n: number) {
  return { Id: `m${n}`, ReceiptHandle: `rh-${n}-AQEBzJna${n}==` };
}

// ------------------------------------------------------- existing behavior

test("processOne still receives a single message and deletes by receipt handle", async () => {
  const fake = new FakeSQS();
  fake.script((cmd) => {
    assert.ok(cmd instanceof ReceiveMessageCommand);
    assert.equal(cmd.input.QueueUrl, QUEUE_URL);
    assert.equal(cmd.input.MaxNumberOfMessages, 1);
    assert.equal(cmd.input.WaitTimeSeconds, 20);
    return { Messages: [msg(1)] };
  });
  fake.script((cmd) => {
    assert.ok(cmd instanceof DeleteMessageCommand);
    assert.equal(cmd.input.QueueUrl, QUEUE_URL);
    assert.equal(cmd.input.ReceiptHandle, msg(1).ReceiptHandle);
    return {};
  });
  const seen: string[] = [];
  const got = await processOne(fake, QUEUE_URL, (m) => {
    seen.push(m.MessageId!);
  });
  assert.equal(got, true);
  assert.deepEqual(seen, ["m1"]);
  assert.equal(fake.calls.length, 2);
});

test("processOne returns false and deletes nothing on an empty receive", async () => {
  const fake = new FakeSQS();
  fake.script(() => ({}));
  const got = await processOne(fake, QUEUE_URL, () => {
    throw new Error("handler must not run");
  });
  assert.equal(got, false);
  assert.equal(fake.callsOf("DeleteMessageCommand").length, 0);
});

// ----------------------------------------------------------- batch limits

test("documented batch limits are pinned as module constants", () => {
  assert.equal(MAX_BATCH_ENTRIES, CONTRACT.batch_actions.max_entries);
  assert.equal(
    MAX_RECEIVE_MESSAGES,
    CONTRACT.receive_message.max_number_of_messages.max,
  );
  assert.equal(
    MAX_WAIT_TIME_SECONDS,
    CONTRACT.receive_message.wait_time_seconds_max,
  );
});

test("chunkEntries splits at the batch maximum and rejects duplicate Ids", () => {
  const entries = Array.from({ length: 23 }, (_, i) => entry(i + 1));
  const chunks = chunkEntries(entries);
  assert.deepEqual(
    chunks.map((c) => c.length),
    [10, 10, 3],
  );
  assert.deepEqual(chunks.flat(), entries);
  assert.throws(() => chunkEntries([entry(1), entry(2), entry(1)]));
});

// ---------------------------------------------------------------- receive

test("receiveBatch long-polls with the documented parameters", async () => {
  const fake = new FakeSQS();
  fake.script((cmd) => {
    assert.ok(cmd instanceof ReceiveMessageCommand);
    assert.equal(cmd.input.QueueUrl, QUEUE_URL);
    assert.equal(cmd.input.MaxNumberOfMessages, 10);
    assert.equal(cmd.input.WaitTimeSeconds, 20);
    assert.deepEqual(
      cmd.input.MessageSystemAttributeNames,
      CONTRACT.receive_message.requested_system_attributes,
    );
    assert.equal(
      cmd.input.AttributeNames,
      undefined,
      "the deprecated AttributeNames parameter must not be sent",
    );
    assert.equal(cmd.input.VisibilityTimeout, 90);
    return { Messages: [msg(1), msg(2)] };
  });
  const got = await receiveBatch(fake, QUEUE_URL, { visibilityTimeout: 90 });
  assert.deepEqual(
    got.map((m) => m.MessageId),
    ["m1", "m2"],
  );
});

test("receiveBatch omits VisibilityTimeout by default and maps no messages to []", async () => {
  const fake = new FakeSQS();
  fake.script((cmd) => {
    assert.equal(cmd.input.VisibilityTimeout, undefined);
    return {}; // SQS omits Messages entirely on an empty long poll
  });
  const got = await receiveBatch(fake, QUEUE_URL);
  assert.deepEqual(got, []);
});

// ----------------------------------------------------------------- delete

test("deleteMessages chunks into ten-entry batches with mapped receipt handles", async () => {
  const entries = Array.from({ length: 12 }, (_, i) => entry(i + 1));
  const fake = new FakeSQS();
  for (const want of [10, 2]) {
    fake.script((cmd) => {
      assert.ok(cmd instanceof DeleteMessageBatchCommand);
      assert.equal(cmd.input.QueueUrl, QUEUE_URL);
      assert.equal(cmd.input.Entries.length, want);
      for (const e of cmd.input.Entries) {
        assert.equal(e.ReceiptHandle, `rh-${e.Id.slice(1)}-AQEBzJna${e.Id.slice(1)}==`);
      }
      return {
        Successful: cmd.input.Entries.map((e: any) => ({ Id: e.Id })),
        Failed: [],
      };
    });
  }
  const got = await deleteMessages(fake, QUEUE_URL, entries);
  assert.deepEqual(got.deleted, entries.map((e) => e.Id));
  assert.deepEqual(got.failures, []);
  assert.equal(fake.callsOf("DeleteMessageBatchCommand").length, 2);
});

test("deleteMessages retries non-sender-fault failures by Id, once", async () => {
  const entries = [entry(1), entry(2), entry(3)];
  const fake = new FakeSQS();
  fake.script((cmd) => {
    assert.equal(cmd.input.Entries.length, 3);
    return {
      Successful: [{ Id: "m1" }],
      Failed: [
        { Id: "m2", SenderFault: false, Code: "InternalError", Message: "try again" },
        { Id: "m3", SenderFault: true, Code: "ReceiptHandleIsInvalid", Message: "bad handle" },
      ],
    };
  });
  fake.script((cmd) => {
    assert.ok(cmd instanceof DeleteMessageBatchCommand);
    // Retry must carry exactly the retryable entry, same mapped handle.
    assert.deepEqual(cmd.input.Entries, [entry(2)]);
    return { Successful: [{ Id: "m2" }], Failed: [] };
  });
  const got = await deleteMessages(fake, QUEUE_URL, entries);
  assert.deepEqual([...got.deleted].sort(), ["m1", "m2"]);
  assert.deepEqual(got.failures, [
    { id: "m3", code: "ReceiptHandleIsInvalid", senderFault: true },
  ]);
  assert.equal(fake.callsOf("DeleteMessageBatchCommand").length, 2);
});

test("deleteMessages reports entries that fail again on the retry", async () => {
  const entries = [entry(1), entry(2)];
  const fake = new FakeSQS();
  fake.script(() => ({
    Successful: [{ Id: "m1" }],
    Failed: [{ Id: "m2", SenderFault: false, Code: "InternalError", Message: "later" }],
  }));
  fake.script((cmd) => {
    assert.deepEqual(cmd.input.Entries, [entry(2)]);
    return {
      Successful: [],
      Failed: [{ Id: "m2", SenderFault: false, Code: "InternalError", Message: "still" }],
    };
  });
  const got = await deleteMessages(fake, QUEUE_URL, entries);
  assert.deepEqual(got.deleted, ["m1"]);
  assert.deepEqual(got.failures, [
    { id: "m2", code: "InternalError", senderFault: false },
  ]);
  assert.equal(fake.callsOf("DeleteMessageBatchCommand").length, 2);
});

test("deleteMessages sends nothing for an empty entry list", async () => {
  const fake = new FakeSQS();
  const got = await deleteMessages(fake, QUEUE_URL, []);
  assert.deepEqual(got, { deleted: [], failures: [] });
  assert.equal(fake.calls.length, 0, "EmptyBatchRequest must be avoided locally");
});

// ------------------------------------------------------------- visibility

test("extendVisibility batches visibility changes with the requested timeout", async () => {
  const entries = [entry(1), entry(2)];
  const fake = new FakeSQS();
  fake.script((cmd) => {
    assert.ok(cmd instanceof ChangeMessageVisibilityBatchCommand);
    assert.equal(cmd.input.QueueUrl, QUEUE_URL);
    assert.deepEqual(cmd.input.Entries, [
      { ...entry(1), VisibilityTimeout: 300 },
      { ...entry(2), VisibilityTimeout: 300 },
    ]);
    return { Successful: [{ Id: "m1" }, { Id: "m2" }], Failed: [] };
  });
  const got = await extendVisibility(fake, QUEUE_URL, entries, 300);
  assert.deepEqual([...got.extended].sort(), ["m1", "m2"]);
  assert.deepEqual(got.failures, []);
});

// ------------------------------------------------------------------ runOnce

test("runOnce deletes only handled messages and defers the rest", async () => {
  const fake = new FakeSQS();
  fake.script((cmd) => {
    assert.ok(cmd instanceof ReceiveMessageCommand);
    return { Messages: [msg(1), msg(2), msg(3), msg(4), msg(5)] };
  });
  fake.script((cmd) => {
    assert.ok(cmd instanceof DeleteMessageBatchCommand);
    // Only the messages the handler finished, receipt handles mapped by Id.
    assert.deepEqual(cmd.input.Entries, [entry(1), entry(2), entry(5)]);
    return {
      Successful: cmd.input.Entries.map((e: any) => ({ Id: e.Id })),
      Failed: [],
    };
  });
  fake.script((cmd) => {
    assert.ok(cmd instanceof ChangeMessageVisibilityBatchCommand);
    assert.deepEqual(cmd.input.Entries, [{ ...entry(3), VisibilityTimeout: 120 }]);
    return { Successful: [{ Id: "m3" }], Failed: [] };
  });

  const outcome: Record<string, "done" | "defer" | "failed"> = {
    m1: "done",
    m2: "done",
    m3: "defer",
    m4: "failed",
    m5: "done",
  };
  const summary = await runOnce(
    fake,
    QUEUE_URL,
    async (m: Message) => outcome[m.MessageId!],
    { deferSeconds: 120 },
  );

  assert.equal(summary.received, 5);
  assert.deepEqual([...summary.deleted].sort(), ["m1", "m2", "m5"]);
  assert.deepEqual(summary.deferred, ["m3"]);
  assert.deepEqual(summary.failed, ["m4"]);
  assert.deepEqual(summary.deleteFailures, []);
  // The failed message must be left alone for redelivery: its receipt handle
  // may not appear in any delete or visibility call.
  for (const call of fake.calls) {
    const handles = (call.input.Entries ?? []).map((e: any) => e.ReceiptHandle);
    assert.ok(!handles.includes(msg(4).ReceiptHandle));
  }
});

test("runOnce with an empty receive sends no batch commands", async () => {
  const fake = new FakeSQS();
  fake.script(() => ({}));
  const summary = await runOnce(fake, QUEUE_URL, () => "done" as const, {
    deferSeconds: 60,
  });
  assert.deepEqual(summary, {
    received: 0,
    deleted: [],
    deferred: [],
    failed: [],
    deleteFailures: [],
  });
  assert.equal(fake.calls.length, 1);
});

test("runOnce surfaces per-entry delete failures without losing siblings", async () => {
  const fake = new FakeSQS();
  fake.script(() => ({ Messages: [msg(1), msg(2)] }));
  fake.script(() => ({
    Successful: [{ Id: "m1" }],
    Failed: [{ Id: "m2", SenderFault: true, Code: "ReceiptHandleIsInvalid", Message: "bad" }],
  }));
  const summary = await runOnce(fake, QUEUE_URL, () => "done" as const, {
    deferSeconds: 60,
  });
  assert.deepEqual(summary.deleted, ["m1"]);
  assert.deepEqual(summary.deleteFailures, [
    { id: "m2", code: "ReceiptHandleIsInvalid", senderFault: true },
  ]);
});
