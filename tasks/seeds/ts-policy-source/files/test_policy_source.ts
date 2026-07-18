import test from 'node:test';
import assert from 'node:assert/strict';

import type { ExportPolicyRequest } from './shared/policy_contract.ts';
import type { WorkspacePolicyRecord, PolicyDirectory } from './server/policy_directory.ts';
import { ExportPolicy } from './server/export_policy.ts';
import { PolicyApi } from './server/policy_api.ts';
import { PolicyApiAdapter } from './client/api_adapter.ts';
import { PolicyCache } from './client/policy_cache.ts';
import { ExportPanelController } from './ui/ExportPanel.ts';
import { ExportPanel } from './ui/ExportPanelView.ts';

const NOW = '2026-07-17T16:30:00Z';

class FakeDirectory implements PolicyDirectory {
  private readonly rows: WorkspacePolicyRecord[];

  constructor(rows: WorkspacePolicyRecord[]) {
    this.rows = rows;
  }

  load(id: string): WorkspacePolicyRecord {
    const row = this.rows.find((candidate) => candidate.id === id);
    if (row === undefined) throw new Error(`workspace ${id} not found`);
    return row;
  }
}

function record(overrides: Partial<WorkspacePolicyRecord> = {}): WorkspacePolicyRecord {
  return {
    id: 'ws-7',
    lifecycle: 'active',
    plan: 'pro',
    dataRegion: 'eu-west',
    exportUsers: ['user-allowed'],
    ...overrides,
  };
}

function request(overrides: Partial<ExportPolicyRequest> = {}): ExportPolicyRequest {
  return {
    workspaceId: 'ws-7',
    userId: 'user-allowed',
    requestedRegion: 'eu-west',
    clientAssumedAllowed: true,
    ...overrides,
  };
}

function system(rows: WorkspacePolicyRecord[]) {
  const policy = new ExportPolicy(new FakeDirectory(rows), () => NOW);
  const api = new PolicyApi(policy);
  const adapter = new PolicyApiAdapter((body) => api.postExportDecision(body));
  const cache = new PolicyCache();
  return { policy, cache, panel: new ExportPanelController(adapter, cache) };
}

test('online panel follows the server when a paid user lacks permission', async () => {
  const { panel } = system([record()]);
  const model = await panel.render(request({ userId: 'user-observer' }), true);
  assert.deepEqual(model, {
    enabled: false,
    reason: 'permission_missing',
    source: 'server',
    label: 'Export unavailable: permission_missing',
  });
});

test('React export panel renders the server-owned decision as UI state', async () => {
  const { panel } = system([record()]);
  const model = await panel.render(request({ userId: 'user-observer' }), true);
  let exports = 0;
  const onExport = () => exports++;
  const element = ExportPanel({ model, onExport });
  assert.equal(element.type, 'button');
  assert.equal(element.props.onClick, onExport);
  assert.deepEqual(
    {
      disabled: element.props.disabled,
      ariaDisabled: element.props['aria-disabled'],
      source: element.props['data-policy-source'],
      reason: element.props['data-policy-reason'],
      label: element.props.children,
    },
    {
      disabled: true,
      ariaDisabled: true,
      source: 'server',
      reason: 'permission_missing',
      label: 'Export unavailable: permission_missing',
    },
  );
  assert.equal(exports, 0, 'rendering a denied component must not trigger export');
});

test('server requires plan and permission independently', () => {
  const paid = new ExportPolicy(new FakeDirectory([record()]), () => NOW);
  assert.deepEqual(paid.decide(request()), {
    allowed: true,
    reason: 'allowed',
    evaluatedAt: NOW,
  });
  assert.deepEqual(paid.decide(request({ userId: 'user-observer' })), {
    allowed: false,
    reason: 'permission_missing',
    evaluatedAt: NOW,
  });

  const starter = new ExportPolicy(
    new FakeDirectory([record({ plan: 'starter' })]),
    () => NOW,
  );
  assert.deepEqual(starter.decide(request()), {
    allowed: false,
    reason: 'upgrade_required',
    evaluatedAt: NOW,
  });
});

test('lifecycle and region denials retain precedence over client assumptions', () => {
  const suspended = new ExportPolicy(
    new FakeDirectory([record({ lifecycle: 'suspended' })]),
    () => NOW,
  );
  assert.deepEqual(suspended.decide(request()), {
    allowed: false,
    reason: 'workspace_inactive',
    evaluatedAt: NOW,
  });
  const region = new ExportPolicy(new FakeDirectory([record()]), () => NOW);
  assert.deepEqual(region.decide(request({ requestedRegion: 'us-east' })), {
    allowed: false,
    reason: 'region_mismatch',
    evaluatedAt: NOW,
  });
});

test('online decisions refresh the required offline cache', async () => {
  const { panel } = system([record()]);
  const req = request();
  assert.equal((await panel.render(req, true)).source, 'server');
  const offline = await panel.render(req, false);
  assert.deepEqual(offline, {
    enabled: true,
    reason: 'allowed',
    source: 'cache',
    label: 'Export data',
  });
});

test('online denial replaces a stale cached allow and remains available offline', async () => {
  const { cache, panel } = system([record()]);
  const req = request({ userId: 'user-observer' });
  cache.put(req, {
    allowed: true,
    reason: 'allowed',
    evaluatedAt: '2026-07-10T09:00:00Z',
  });

  assert.deepEqual(await panel.render(req, true), {
    enabled: false,
    reason: 'permission_missing',
    source: 'server',
    label: 'Export unavailable: permission_missing',
  });
  assert.deepEqual(cache.get(req), {
    allowed: false,
    reason: 'permission_missing',
    evaluatedAt: NOW,
  });
  assert.deepEqual(await panel.render(req, false), {
    enabled: false,
    reason: 'permission_missing',
    source: 'cache',
    label: 'Export unavailable: permission_missing',
  });
});

test('offline mode preserves a cached denial and never calls transport', async () => {
  const cache = new PolicyCache();
  const req = request({ userId: 'user-observer' });
  cache.put(req, { allowed: false, reason: 'permission_missing', evaluatedAt: NOW });
  const adapter = new PolicyApiAdapter(async () => {
    throw new Error('offline transport must not be called');
  });
  const panel = new ExportPanelController(adapter, cache);
  assert.deepEqual(await panel.render(req, false), {
    enabled: false,
    reason: 'permission_missing',
    source: 'cache',
    label: 'Export unavailable: permission_missing',
  });
});

test('offline cache misses disable safely without deleting offline behavior', async () => {
  const adapter = new PolicyApiAdapter(async () => {
    throw new Error('offline transport must not be called');
  });
  const panel = new ExportPanelController(adapter, new PolicyCache());
  assert.deepEqual(await panel.render(request(), false), {
    enabled: false,
    reason: 'offline_unknown',
    source: 'offline',
    label: 'Export policy unavailable offline',
  });
});

test('cache is isolated by workspace user and requested region', () => {
  const cache = new PolicyCache();
  cache.put(request(), { allowed: true, reason: 'allowed', evaluatedAt: NOW });
  assert.equal(cache.get(request())?.allowed, true);
  assert.equal(cache.get(request({ userId: 'another-user' })), undefined);
  assert.equal(cache.get(request({ workspaceId: 'ws-other' })), undefined);
  assert.equal(cache.get(request({ requestedRegion: 'us-east' })), undefined);
});

test('API adapter preserves the generated decision shape and rejects malformed bodies', async () => {
  const good = new PolicyApiAdapter(async () => ({
    allowed: false,
    reason: 'upgrade_required',
    evaluatedAt: NOW,
  }));
  assert.deepEqual(await good.fetch(request()), {
    allowed: false,
    reason: 'upgrade_required',
    evaluatedAt: NOW,
  });
  const malformed = new PolicyApiAdapter(async () => ({ allowed: 'yes', reason: 'allowed' }));
  await assert.rejects(malformed.fetch(request()), /bad policy response/);
});
