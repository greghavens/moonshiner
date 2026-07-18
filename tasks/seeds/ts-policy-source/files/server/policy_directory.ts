export interface WorkspacePolicyRecord {
  id: string;
  lifecycle: 'active' | 'suspended';
  plan: 'starter' | 'pro' | 'enterprise';
  dataRegion: string;
  exportUsers: readonly string[];
}

export interface PolicyDirectory {
  load(workspaceId: string): WorkspacePolicyRecord;
}
