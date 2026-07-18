import type { ExportPolicyRequest } from '../shared/policy_contract.ts';
import { ExportPolicy } from './export_policy.ts';

// HTTP-shaped adapter; tests call it in process to stay hermetic.
export class PolicyApi {
  private readonly policy: ExportPolicy;

  constructor(policy: ExportPolicy) {
    this.policy = policy;
  }

  async postExportDecision(body: ExportPolicyRequest): Promise<unknown> {
    const decision = this.policy.decide(body);
    return {
      allowed: decision.allowed,
      reason: decision.reason,
      evaluatedAt: decision.evaluatedAt,
    };
  }
}
