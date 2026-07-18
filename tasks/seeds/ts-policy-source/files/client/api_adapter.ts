import type {
  ExportPolicyDecision,
  ExportPolicyReason,
  ExportPolicyRequest,
} from '../shared/policy_contract.ts';

export type PolicyTransport = (request: ExportPolicyRequest) => Promise<unknown>;

const reasons = new Set<ExportPolicyReason>([
  'allowed',
  'workspace_inactive',
  'upgrade_required',
  'permission_missing',
  'region_mismatch',
]);

export class PolicyApiAdapter {
  private readonly transport: PolicyTransport;

  constructor(transport: PolicyTransport) {
    this.transport = transport;
  }

  async fetch(request: ExportPolicyRequest): Promise<ExportPolicyDecision> {
    const body = await this.transport({ ...request });
    if (typeof body !== 'object' || body === null) throw new Error('bad policy response');
    const value = body as Record<string, unknown>;
    if (
      typeof value.allowed !== 'boolean' ||
      typeof value.reason !== 'string' ||
      !reasons.has(value.reason as ExportPolicyReason) ||
      typeof value.evaluatedAt !== 'string'
    ) {
      throw new Error('bad policy response');
    }
    return {
      allowed: value.allowed,
      reason: value.reason as ExportPolicyReason,
      evaluatedAt: value.evaluatedAt,
    };
  }
}
