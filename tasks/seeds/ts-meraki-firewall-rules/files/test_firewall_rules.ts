// Acceptance harness for the MX L3 firewall-rule reconciler in
// meraki/firewall.ts, exercised against a loopback fake of the Cisco Meraki
// Dashboard API v1. Wire shapes are pinned in docs/contract.json
// (provenance: docs/official_sources.json). Hermetic: no real dashboard,
// no real API key. Protected file -- do not modify.
// Run: node --test test_firewall_rules.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { MerakiApiError, MerakiClient } from "./meraki/client.ts";
import {
  normalizeRules,
  reconcileL3FirewallRules,
  stripDefaultRule,
  validateRules,
  type L3Rule,
} from "./meraki/firewall.ts";

const API_KEY = "cafe0123cafe0123cafe0123cafe0123cafe0123-fake";
const NETWORK_ID = "N_646502906128";
const RULES_PATH = `/api/v1/networks/${NETWORK_ID}/appliance/firewall/l3FirewallRules`;

interface Captured {
  method: string;
  path: string;
  auth: string | undefined;
  contentType: string | undefined;
  body: unknown;
}

interface Scripted {
  status: number;
  body: unknown;
}

class FakeDashboard {
  requests: Captured[] = [];
  private queues = new Map<string, Scripted[]>();
  private server: Server | undefined;
  private baseUrl = "";

  enqueue(method: string, path: string, status: number, body: unknown): void {
    const key = `${method} ${path}`;
    const queue = this.queues.get(key) ?? [];
    queue.push({ status, body });
    this.queues.set(key, queue);
  }

  url(): string {
    return this.baseUrl;
  }

  async start(): Promise<void> {
    this.server = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf8");
        let parsed: unknown = undefined;
        if (raw.length > 0) {
          try {
            parsed = JSON.parse(raw);
          } catch {
            parsed = raw;
          }
        }
        const path = new URL(req.url ?? "/", "http://localhost").pathname;
        this.requests.push({
          method: req.method ?? "",
          path,
          auth: req.headers.authorization,
          contentType: req.headers["content-type"],
          body: parsed,
        });
        const queue = this.queues.get(`${req.method} ${path}`);
        const scripted = queue?.shift() ?? {
          status: 500,
          body: { errors: [`unexpected request ${req.method} ${path}`] },
        };
        const data = JSON.stringify(scripted.body);
        res.writeHead(scripted.status, { "Content-Type": "application/json" });
        res.end(data);
      });
    });
    await new Promise<void>((resolve) =>
      this.server?.listen(0, "127.0.0.1", resolve),
    );
    const addr = this.server.address() as AddressInfo;
    this.baseUrl = `http://127.0.0.1:${addr.port}/api/v1`;
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve, reject) =>
      this.server?.close((err) => (err ? reject(err) : resolve())),
    );
  }
}

function client(fake: FakeDashboard): MerakiClient {
  return new MerakiClient({ baseUrl: fake.url(), apiKey: API_KEY });
}

// The dashboard appends its special default rule to every GET response.
const DEFAULT_RULE = {
  comment: "Default rule",
  policy: "allow",
  protocol: "Any",
  srcPort: "Any",
  srcCidr: "Any",
  destPort: "Any",
  destCidr: "Any",
  syslogEnabled: false,
};

const SERVER_RULES = [
  {
    comment: "Allow web to DMZ",
    policy: "allow",
    protocol: "tcp",
    srcPort: "Any",
    srcCidr: "Any",
    destPort: "443",
    destCidr: "192.168.128.0/24",
    syslogEnabled: false,
  },
  {
    comment: "Block guest to corp",
    policy: "deny",
    protocol: "any",
    srcPort: "Any",
    srcCidr: "10.30.0.0/16",
    destPort: "Any",
    destCidr: "10.10.0.0/16",
    syslogEnabled: true,
  },
];

// The same two rules as an operator would write them in config.
const DESIRED_MATCHING: L3Rule[] = [
  {
    comment: "Allow web to DMZ",
    policy: "allow",
    protocol: "tcp",
    srcCidr: "any",
    destPort: "443",
    destCidr: "192.168.128.0/24",
  },
  {
    comment: "Block guest to corp",
    policy: "deny",
    protocol: "any",
    srcPort: "any",
    srcCidr: "10.30.0.0/16",
    destPort: "any",
    destCidr: "10.10.0.0/16",
    syslogEnabled: true,
  },
];

test("existing client behavior: documented bearer auth and JSON decoding", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    fake.enqueue("GET", "/api/v1/organizations", 200, [{ id: "810001" }]);
    const res = await client(fake).get("/organizations");
    assert.deepEqual(res, [{ id: "810001" }]);
    assert.equal(fake.requests.length, 1);
    assert.equal(fake.requests[0].auth, `Bearer ${API_KEY}`);
    assert.equal(fake.requests[0].path, "/api/v1/organizations");
  } finally {
    await fake.stop();
  }
});

test("existing client behavior: error envelope decoded, key never leaked", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    fake.enqueue("GET", "/api/v1/organizations/990404", 404, {
      errors: ["Organization not found"],
    });
    await assert.rejects(
      client(fake).get("/organizations/990404"),
      (err: unknown) => {
        assert.ok(err instanceof MerakiApiError);
        assert.equal(err.status, 404);
        assert.deepEqual(err.errors, ["Organization not found"]);
        assert.ok(!err.message.includes(API_KEY));
        return true;
      },
    );
  } finally {
    await fake.stop();
  }
});

test("normalizeRules applies the documented rule defaults", () => {
  const input: L3Rule[] = [
    {
      policy: "allow",
      protocol: "tcp",
      srcPort: "Any",
      srcCidr: "Any",
      destPort: "443",
      destCidr: "192.168.128.0/24",
    },
  ];
  const snapshot = JSON.stringify(input);
  const out = normalizeRules(input);
  assert.deepEqual(out, [
    {
      comment: "",
      policy: "allow",
      protocol: "tcp",
      srcPort: "any",
      srcCidr: "any",
      destPort: "443",
      destCidr: "192.168.128.0/24",
      syslogEnabled: false,
    },
  ]);
  assert.equal(JSON.stringify(input), snapshot, "input must not be mutated");
  const defaults = normalizeRules([
    { policy: "deny", protocol: "Any", srcCidr: "10.0.0.0/8", destCidr: "any" },
  ]);
  assert.equal(defaults[0].srcPort, "any", "omitted srcPort defaults to any");
  assert.equal(defaults[0].destPort, "any", "omitted destPort defaults to any");
  assert.equal(defaults[0].protocol, "any", "protocol Any normalizes lowercase");
  assert.equal(defaults[0].syslogEnabled, false);
  assert.equal(
    defaults[0].destCidr,
    "any",
    "only the literal any is case-normalized",
  );
  const cidr = normalizeRules([
    {
      policy: "allow",
      protocol: "tcp",
      srcCidr: "10.0.0.0/8",
      destCidr: "Books.Example.COM",
    },
  ]);
  assert.equal(
    cidr[0].destCidr,
    "Books.Example.COM",
    "non-any values keep their exact spelling",
  );
});

test("stripDefaultRule removes only a trailing dashboard default rule", () => {
  const withDefault = [...SERVER_RULES, DEFAULT_RULE];
  const stripped = stripDefaultRule(withDefault);
  assert.equal(stripped.length, 2);
  assert.equal(stripped[1].comment, "Block guest to corp");
  assert.equal(withDefault.length, 3, "input must not be mutated");
  const noDefault = stripDefaultRule(SERVER_RULES);
  assert.equal(noDefault.length, 2, "list without a default rule is unchanged");
  const middle = [SERVER_RULES[0], DEFAULT_RULE, SERVER_RULES[1]];
  assert.equal(
    stripDefaultRule(middle).length,
    3,
    "only the FINAL rule may be treated as the default rule",
  );
});

test("validateRules flags contract violations before they reach the wire", () => {
  assert.deepEqual(validateRules(DESIRED_MATCHING), []);
  const problems = validateRules([
    {
      policy: "permit",
      protocol: "gre",
      srcPort: "0",
      srcCidr: "10.0.0.0/8",
      destPort: "80,abc",
      destCidr: "",
    } as L3Rule,
  ]);
  assert.ok(problems.length >= 4, `expected >=4 problems, got ${problems.length}`);
  assert.ok(problems.some((p) => p.includes("policy")));
  assert.ok(problems.some((p) => p.includes("protocol")));
  assert.ok(problems.some((p) => p.includes("srcPort")));
  assert.ok(problems.some((p) => p.includes("destPort")));
  assert.ok(problems.some((p) => p.includes("destCidr")));
  const highPort = validateRules([
    {
      policy: "allow",
      protocol: "udp",
      srcPort: "70000",
      srcCidr: "any",
      destPort: "53",
      destCidr: "any",
    },
  ]);
  assert.ok(highPort.some((p) => p.includes("srcPort")), "ports are 1-65535");
  const commaPorts = validateRules([
    {
      policy: "allow",
      protocol: "tcp",
      srcPort: "any",
      srcCidr: "any",
      destPort: "80,443,8443",
      destCidr: "any",
    },
  ]);
  assert.deepEqual(commaPorts, [], "comma-separated port lists are legal");
});

test("reconcile is a no-op when normalized desired matches current", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    fake.enqueue("GET", RULES_PATH, 200, {
      rules: [...SERVER_RULES, DEFAULT_RULE],
    });
    const result = await reconcileL3FirewallRules(
      client(fake),
      NETWORK_ID,
      DESIRED_MATCHING,
    );
    assert.equal(result.changed, false);
    assert.equal(result.ruleCount, 2);
    const puts = fake.requests.filter((r) => r.method === "PUT");
    assert.equal(puts.length, 0, "matching rules must not trigger a write");
    assert.equal(fake.requests.length, 1, "exactly one GET to read current");
    assert.equal(fake.requests[0].path, RULES_PATH);
  } finally {
    await fake.stop();
  }
});

test("reconcile replaces the full ordered list when drift exists", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    const desired: L3Rule[] = [
      ...DESIRED_MATCHING,
      {
        comment: "Sensor VLAN to collector",
        policy: "allow",
        protocol: "udp",
        srcCidr: "10.77.0.0/24",
        destPort: "2055",
        destCidr: "10.10.5.10/32",
        syslogEnabled: true,
      },
    ];
    const normalizedDesired = normalizeRules(desired);
    fake.enqueue("GET", RULES_PATH, 200, {
      rules: [...SERVER_RULES, DEFAULT_RULE],
    });
    fake.enqueue("PUT", RULES_PATH, 200, {
      rules: [...normalizedDesired, DEFAULT_RULE],
    });
    const result = await reconcileL3FirewallRules(
      client(fake),
      NETWORK_ID,
      desired,
      { syslogDefaultRule: true },
    );
    assert.equal(result.changed, true);
    assert.equal(result.ruleCount, 3);
    assert.deepEqual(result.rules, normalizedDesired);
    const puts = fake.requests.filter((r) => r.method === "PUT");
    assert.equal(puts.length, 1, "full-list replacement is a single PUT");
    assert.equal(puts[0].path, RULES_PATH);
    assert.equal(puts[0].contentType, "application/json");
    assert.equal(puts[0].auth, `Bearer ${API_KEY}`);
    const body = puts[0].body as {
      rules: Array<Record<string, unknown>>;
      syslogDefaultRule?: boolean;
    };
    assert.deepEqual(body.rules, normalizedDesired);
    assert.ok(
      body.rules.every((r) => r.comment !== "Default rule"),
      "the special default rule must never be sent in a PUT body",
    );
    assert.equal(body.syslogDefaultRule, true);
  } finally {
    await fake.stop();
  }
});

test("reconcile omits syslogDefaultRule when the caller does not set it", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    const desired: L3Rule[] = [DESIRED_MATCHING[0]];
    fake.enqueue("GET", RULES_PATH, 200, {
      rules: [...SERVER_RULES, DEFAULT_RULE],
    });
    fake.enqueue("PUT", RULES_PATH, 200, {
      rules: [...normalizeRules(desired), DEFAULT_RULE],
    });
    const result = await reconcileL3FirewallRules(
      client(fake),
      NETWORK_ID,
      desired,
    );
    assert.equal(result.changed, true);
    assert.equal(result.ruleCount, 1);
    const body = fake.requests.filter((r) => r.method === "PUT")[0]
      .body as Record<string, unknown>;
    assert.ok(
      !("syslogDefaultRule" in body),
      "syslogDefaultRule must be omitted unless explicitly requested",
    );
  } finally {
    await fake.stop();
  }
});

test("rule order is part of the contract", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    const reordered = [DESIRED_MATCHING[1], DESIRED_MATCHING[0]];
    fake.enqueue("GET", RULES_PATH, 200, {
      rules: [...SERVER_RULES, DEFAULT_RULE],
    });
    fake.enqueue("PUT", RULES_PATH, 200, {
      rules: [...normalizeRules(reordered), DEFAULT_RULE],
    });
    const result = await reconcileL3FirewallRules(
      client(fake),
      NETWORK_ID,
      reordered,
    );
    assert.equal(result.changed, true, "same rules in a new order is drift");
    assert.equal(fake.requests.filter((r) => r.method === "PUT").length, 1);
  } finally {
    await fake.stop();
  }
});

test("invalid desired rules never reach the wire", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    await assert.rejects(
      reconcileL3FirewallRules(client(fake), NETWORK_ID, [
        {
          policy: "permit",
          protocol: "tcp",
          srcCidr: "any",
          destCidr: "any",
        } as L3Rule,
      ]),
      (err: unknown) => {
        assert.ok(err instanceof Error);
        assert.ok(!(err instanceof MerakiApiError));
        assert.ok(err.message.includes("policy"));
        return true;
      },
    );
    assert.equal(fake.requests.length, 0, "no HTTP traffic for invalid input");
  } finally {
    await fake.stop();
  }
});

test("server-side validation errors are surfaced with their messages", async () => {
  const fake = new FakeDashboard();
  await fake.start();
  try {
    fake.enqueue("GET", RULES_PATH, 200, {
      rules: [...SERVER_RULES, DEFAULT_RULE],
    });
    fake.enqueue("PUT", RULES_PATH, 400, {
      errors: ["At least one of your firewall rules is invalid"],
    });
    await assert.rejects(
      reconcileL3FirewallRules(client(fake), NETWORK_ID, [DESIRED_MATCHING[0]]),
      (err: unknown) => {
        assert.ok(err instanceof MerakiApiError);
        assert.equal(err.status, 400);
        assert.deepEqual(err.errors, [
          "At least one of your firewall rules is invalid",
        ]);
        assert.ok(!err.message.includes(API_KEY));
        return true;
      },
    );
  } finally {
    await fake.stop();
  }
});
