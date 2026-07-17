import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderManifest } from './render.ts';

// Every test builds fresh manifest objects so runs are independent.
function baseManifest() {
  return {
    defaults: { replicas: 1, env: { LOG_LEVEL: 'info' } },
    services: {
      api: {
        image: 'registry.local/api:1.4.0',
        replicas: 2,
        env: { API_TOKEN: 'from-base' },
        ports: [8080, 8081],
      },
      web: { image: 'registry.local/web:2.0.1' },
      worker: { image: 'registry.local/worker:1.4.0' },
    },
  };
}

test('a single manifest renders every service alphabetically with defaults applied', () => {
  assert.deepEqual(renderManifest([baseManifest()]), [
    {
      name: 'api',
      image: 'registry.local/api:1.4.0',
      replicas: 2,
      env: { LOG_LEVEL: 'info', API_TOKEN: 'from-base' },
      ports: [8080, 8081],
    },
    {
      name: 'web',
      image: 'registry.local/web:2.0.1',
      replicas: 1,
      env: { LOG_LEVEL: 'info' },
      ports: [],
    },
    {
      name: 'worker',
      image: 'registry.local/worker:1.4.0',
      replicas: 1,
      env: { LOG_LEVEL: 'info' },
      ports: [],
    },
  ]);
});

test('defaults are applied per service, not accumulated across services', () => {
  const out = renderManifest([baseManifest()]);
  const web = out.find((s) => s.name === 'web');
  assert.ok(web, 'web must render');
  // api's token must not ride along into web via the shared defaults.
  assert.deepEqual(web.env, { LOG_LEVEL: 'info' });
});

test('an empty manifest renders no services', () => {
  assert.deepEqual(renderManifest([{}]), []);
});

test('deployOrder picks exactly the listed services in the listed order', () => {
  const out = renderManifest([baseManifest(), { deployOrder: ['worker', 'api'] }]);
  assert.deepEqual(
    out.map((s) => s.name),
    ['worker', 'api'],
  );
});

test('services listed in deployOrder but disabled are left out', () => {
  const out = renderManifest([
    baseManifest(),
    { deployOrder: ['api', 'web'], services: { web: { disabled: true } } },
  ]);
  assert.deepEqual(
    out.map((s) => s.name),
    ['api'],
  );
});

test('duplicate deployOrder entries render once', () => {
  const out = renderManifest([baseManifest(), { deployOrder: ['api', 'web', 'api'] }]);
  assert.deepEqual(
    out.map((s) => s.name),
    ['api', 'web'],
  );
});

test('an unknown name in deployOrder is reported as such', () => {
  assert.throws(
    () => renderManifest([baseManifest(), { deployOrder: ['api', 'ghost'] }]),
    (err: Error) => err.message.includes('"ghost"'),
  );
});

test('overlay values win and env maps merge per key', () => {
  const out = renderManifest([
    baseManifest(),
    { services: { api: { replicas: 6, env: { API_TOKEN: 'from-prod', EXTRA: 'on' } } } },
  ]);
  const api = out.find((s) => s.name === 'api');
  assert.ok(api);
  assert.equal(api.replicas, 6);
  assert.equal(api.image, 'registry.local/api:1.4.0');
  assert.deepEqual(api.env, { LOG_LEVEL: 'info', API_TOKEN: 'from-prod', EXTRA: 'on' });
});

test('an overlay list replaces the base list instead of splicing into it', () => {
  const out = renderManifest([baseManifest(), { services: { api: { ports: [9090] } } }]);
  const api = out.find((s) => s.name === 'api');
  assert.ok(api);
  assert.deepEqual(api.ports, [9090]);
});

test('services added only by an overlay render, whatever they are called', () => {
  const sidecar = { image: 'registry.local/search-sidecar:2.1.0', env: { MODE: 'live' } };

  // Vendor names the sidecar binary "constructor"; a name is just a name.
  const out = renderManifest([baseManifest(), { services: { constructor: { ...sidecar } } }]);
  assert.deepEqual(
    out.map((s) => s.name),
    ['api', 'constructor', 'web', 'worker'],
  );
  const ctor = out.find((s) => s.name === 'constructor');
  assert.ok(ctor);
  assert.equal(ctor.image, 'registry.local/search-sidecar:2.1.0');
  assert.deepEqual(ctor.env, { LOG_LEVEL: 'info', MODE: 'live' });

  // Under an ordinary name the very same overlay must behave identically.
  const control = renderManifest([baseManifest(), { services: { search: { ...sidecar } } }]);
  assert.deepEqual(
    control.map((s) => s.name),
    ['api', 'search', 'web', 'worker'],
  );
});

test('rendering must not mutate the input manifests', () => {
  const base = baseManifest();
  const overlay = { services: { api: { env: { REGION: 'eu-1' } } } };
  const snapshot = JSON.parse(JSON.stringify([base, overlay]));
  renderManifest([base, overlay]);
  assert.deepEqual([base, overlay], snapshot);
});

test('a base manifest reused across renders must not leak settings between them', () => {
  const base = baseManifest();
  const staging = { services: { api: { env: { FEATURE_FLAGS: 'staging-set' } } } };
  const prod = { services: { api: { env: { REGION: 'eu-1' } } } };

  renderManifest([base, staging]);
  const out = renderManifest([base, prod]);
  const api = out.find((s) => s.name === 'api');
  assert.ok(api);
  assert.equal(api.env.FEATURE_FLAGS, undefined, 'staging-only setting leaked into the prod render');
  assert.equal(api.env.REGION, 'eu-1');
});
