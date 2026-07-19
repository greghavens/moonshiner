import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { appRoutes } from './app/routes.ts';
import { renderedLayouts } from './app/app_shell.ts';
import { AppNavigation } from './app/navigation.ts';
import { UnsavedGuard } from './app/unsaved_guard.ts';
import {
  MemoryDataRouter,
  redirect,
  type RouteObject,
} from './router/router_v7.ts';

const currentRoutes: readonly RouteObject[] = [
  {
    id: 'root', path: '/', element: 'RootLayout', children: [
      { id: 'home', index: true, loader: () => redirect('/projects', { replace: true }) },
      {
        id: 'projects', path: 'projects', element: 'ProjectsLayout', children: [
          { id: 'project-list', index: true, element: 'ProjectList' },
          {
            id: 'project', path: ':projectId', element: 'ProjectLayout', children: [
              { id: 'project-overview', index: true, element: 'ProjectOverview' },
              { id: 'project-settings', path: 'settings', element: 'ProjectSettings' },
              { id: 'project-edit', path: 'edit', element: 'ProjectEditor' },
            ],
          },
        ],
      },
      { id: 'legacy-projects', path: 'start', loader: () => redirect('/projects', { replace: true }) },
    ],
  },
];

test('application route objects satisfy the current contract and preserve nesting', () => {
  const router = new MemoryDataRouter(appRoutes, ['/projects/project-7/settings']);
  assert.deepEqual(renderedLayouts(router), [
    'RootLayout', 'ProjectsLayout', 'ProjectLayout', 'ProjectSettings',
  ]);
  const project = router.matches().find((match) => match.id === 'project');
  assert.deepEqual(project?.params, { projectId: 'project-7' });

  const overview = new MemoryDataRouter(appRoutes, ['/projects/project-8']);
  assert.deepEqual(renderedLayouts(overview), [
    'RootLayout', 'ProjectsLayout', 'ProjectLayout', 'ProjectOverview',
  ]);

  const editor = new MemoryDataRouter(appRoutes, ['/projects/editor%209/edit']);
  assert.deepEqual(renderedLayouts(editor), [
    'RootLayout', 'ProjectsLayout', 'ProjectLayout', 'ProjectEditor',
  ]);
  assert.deepEqual(
    editor.matches().find((match) => match.id === 'project')?.params,
    { projectId: 'editor 9' },
  );
});

test('root and legacy redirects retain replace semantics', () => {
  const root = new MemoryDataRouter(appRoutes, ['/projects/project-1']);
  assert.deepEqual(root.navigate('/'), { status: 'committed', location: '/projects' });
  assert.deepEqual(root.entries, ['/projects']);
  assert.deepEqual(renderedLayouts(root), ['RootLayout', 'ProjectsLayout', 'ProjectList']);

  const legacy = new MemoryDataRouter(appRoutes, ['/projects/project-2']);
  assert.deepEqual(legacy.navigate('/start'), { status: 'committed', location: '/projects' });
  assert.deepEqual(legacy.entries, ['/projects']);
});

test('imperative app navigation uses the router and handles dynamic identifiers', () => {
  const router = new MemoryDataRouter(currentRoutes, ['/projects']);
  const navigation = new AppNavigation(router);
  assert.deepEqual(navigation.openProject('ops 7'), {
    status: 'committed', location: '/projects/ops%207',
  });
  assert.equal(router.location, '/projects/ops%207');
  assert.equal(router.matches().find((match) => match.id === 'project')?.params.projectId, 'ops 7');
  assert.deepEqual(navigation.showProjects(), {
    status: 'committed', location: '/projects',
  });
  assert.deepEqual(router.entries, ['/projects', '/projects']);
});

test('unsaved editor blocks, resets, proceeds, and unregisters deterministically', () => {
  const router = new MemoryDataRouter(currentRoutes, ['/projects/project-1/edit']);
  const editor = { dirty: true };
  const navigation = new AppNavigation(router);
  const guard = new UnsavedGuard(router, editor);
  const dispose = guard.attach();

  const blocked = navigation.openProject('project-2');
  assert.equal(blocked.status, 'blocked');
  assert.equal(blocked.location, '/projects/project-1/edit');
  assert.equal(typeof blocked.blocker, 'string');
  assert.ok(blocked.blocker.length > 0, 'the pending transition must identify its blocker');
  assert.equal(guard.pendingLocation(), '/projects/project-2');
  assert.deepEqual(guard.resolve('stay'), {
    status: 'reset', location: '/projects/project-1/edit',
  });
  assert.equal(router.location, '/projects/project-1/edit');
  assert.equal(guard.pendingLocation(), null);

  navigation.openProject('project-2');
  assert.deepEqual(guard.resolve('leave'), {
    status: 'committed', location: '/projects/project-2',
  });
  assert.equal(router.location, '/projects/project-2');

  editor.dirty = false;
  assert.equal(navigation.openProject('project-3').status, 'committed');
  editor.dirty = true;
  dispose();
  assert.equal(navigation.openProject('project-4').status, 'committed');
  assert.equal(router.location, '/projects/project-4');
});

test('leaving a blocked replace preserves the original transition semantics', () => {
  const router = new MemoryDataRouter(currentRoutes, [
    '/projects',
    '/projects/project-6/edit',
  ]);
  const guard = new UnsavedGuard(router, { dirty: true });
  guard.attach();

  const blocked = new AppNavigation(router).showProjects();
  assert.equal(blocked.status, 'blocked');
  assert.equal(blocked.location, '/projects/project-6/edit');
  assert.equal(typeof blocked.blocker, 'string');
  assert.ok(blocked.blocker.length > 0);
  assert.equal(guard.pendingLocation(), '/projects');
  assert.deepEqual(router.entries, [
    '/projects',
    '/projects/project-6/edit',
  ]);

  assert.deepEqual(guard.resolve('leave'), {
    status: 'committed',
    location: '/projects',
  });
  assert.deepEqual(router.entries, ['/projects', '/projects'],
    'proceed must retain replace instead of resetting and pushing the destination');
});

test('a same-location transition is not treated as data loss', () => {
  const router = new MemoryDataRouter(currentRoutes, ['/projects/project-5']);
  const guard = new UnsavedGuard(router, { dirty: true });
  guard.attach();
  assert.equal(new AppNavigation(router).openProject('project-5').status, 'committed');
  assert.equal(guard.pendingLocation(), null);
});

test('protected notes record the removed and current APIs', () => {
  const notes = readFileSync(new URL('./contracts/react_router_v7_notes.md', import.meta.url), 'utf8');
  for (const phrase of [
    'Route-object `redirect`, `component`, and `render` properties are removed',
    '`router.navigate(location, { replace })`',
    '`proceed()` or',
    'Nested route objects',
  ]) assert.ok(notes.includes(phrase), phrase);
});
