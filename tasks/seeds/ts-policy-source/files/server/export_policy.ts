import type {
  ExportPolicyDecision,
  ExportPolicyRequest,
} from '../shared/policy_contract.ts';
import type { PolicyDirectory } from './policy_directory.ts';

export class ExportPolicy {
  private readonly directory: PolicyDirectory;
  private readonly now: () => string;

  constructor(
    directory: PolicyDirectory,
    now: () => string,
  ) {
    this.directory = directory;
    this.now = now;
  }

  decide(request: ExportPolicyRequest): ExportPolicyDecision {
    const workspace = this.directory.load(request.workspaceId);
    const at = this.now();
    if (workspace.lifecycle !== 'active') {
      return { allowed: false, reason: 'workspace_inactive', evaluatedAt: at };
    }
    if (workspace.dataRegion !== request.requestedRegion) {
      return { allowed: false, reason: 'region_mismatch', evaluatedAt: at };
    }

    const eligiblePlan = workspace.plan === 'pro' || workspace.plan === 'enterprise';
    const hasPermission = workspace.exportUsers.includes(request.userId);
    if (!eligiblePlan && !hasPermission) {
      return { allowed: false, reason: 'upgrade_required', evaluatedAt: at };
    }
    return { allowed: true, reason: 'allowed', evaluatedAt: at };
  }
}
