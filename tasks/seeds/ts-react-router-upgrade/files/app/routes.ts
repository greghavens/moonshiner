import type { RouteObject } from '../router/router_v7.ts';

export const appRoutes = [
  {
    id: 'root',
    path: '/',
    element: 'RootLayout',
    children: [
      { id: 'home', index: true, redirect: '/projects', replace: true },
      {
        id: 'projects',
        path: 'projects',
        element: 'ProjectsLayout',
        children: [
          { id: 'project-list', index: true, element: 'ProjectList' },
          {
            id: 'project',
            path: ':projectId',
            element: 'ProjectLayout',
            children: [
              { id: 'project-overview', index: true, element: 'ProjectOverview' },
              { id: 'project-settings', path: 'settings', element: 'ProjectSettings' },
              { id: 'project-edit', path: 'edit', element: 'ProjectEditor' },
            ],
          },
        ],
      },
      { id: 'legacy-projects', path: 'start', redirect: '/projects', replace: true },
    ],
  },
] as unknown as readonly RouteObject[];

