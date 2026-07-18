import type {
  ExportPolicyDecision,
  ExportPolicyRequest,
} from '../shared/policy_contract.ts';

function keyOf(request: ExportPolicyRequest): string {
  return `${request.workspaceId}\u0000${request.userId}\u0000${request.requestedRegion}`;
}

export class PolicyCache {
  private readonly values = new Map<string, ExportPolicyDecision>();

  put(request: ExportPolicyRequest, decision: ExportPolicyDecision): void {
    this.values.set(keyOf(request), { ...decision });
  }

  get(request: ExportPolicyRequest): ExportPolicyDecision | undefined {
    const found = this.values.get(keyOf(request));
    return found === undefined ? undefined : { ...found };
  }
}
