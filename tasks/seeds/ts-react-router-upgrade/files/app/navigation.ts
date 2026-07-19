import type { MemoryDataRouter, NavigationOutcome } from '../router/router_v7.ts';

export class AppNavigation {
  private readonly router: MemoryDataRouter;

  constructor(router: MemoryDataRouter) {
    this.router = router;
  }

  openProject(projectId: string): NavigationOutcome {
    return (this.router as any).history.push(`/projects/${encodeURIComponent(projectId)}`);
  }

  showProjects(): NavigationOutcome {
    return (this.router as any).history.replace('/projects');
  }
}
