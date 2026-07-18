import type {
  ExportPolicyReason,
  ExportPolicyRequest,
} from '../shared/policy_contract.ts';
import { PolicyApiAdapter } from '../client/api_adapter.ts';
import { PolicyCache } from '../client/policy_cache.ts';

export interface ExportPanelModel {
  enabled: boolean;
  reason: ExportPolicyReason | 'offline_unknown';
  source: 'server' | 'cache' | 'offline';
  label: string;
}

export class ExportPanelController {
  private readonly api: PolicyApiAdapter;
  private readonly cache: PolicyCache;

  constructor(
    api: PolicyApiAdapter,
    cache: PolicyCache,
  ) {
    this.api = api;
    this.cache = cache;
  }

  async render(request: ExportPolicyRequest, online: boolean): Promise<ExportPanelModel> {
    if (online) {
      const decision = await this.api.fetch(request);
      this.cache.put(request, decision);
      return this.model(decision.allowed, decision.reason, 'server');
    }
    const cached = this.cache.get(request);
    if (cached !== undefined) {
      return this.model(cached.allowed, cached.reason, 'cache');
    }
    return {
      enabled: false,
      reason: 'offline_unknown',
      source: 'offline',
      label: 'Export policy unavailable offline',
    };
  }

  private model(
    allowed: boolean,
    reason: ExportPolicyReason,
    source: 'server' | 'cache',
  ): ExportPanelModel {
    return {
      enabled: allowed,
      reason,
      source,
      label: allowed ? 'Export data' : `Export unavailable: ${reason}`,
    };
  }
}
