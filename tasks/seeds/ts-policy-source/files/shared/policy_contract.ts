// Generated from the internal policy schema. Stable across server and client.
export interface ExportPolicyRequest {
  workspaceId: string;
  userId: string;
  requestedRegion: string;
  clientAssumedAllowed?: boolean;
}

export type ExportPolicyReason =
  | 'allowed'
  | 'workspace_inactive'
  | 'upgrade_required'
  | 'permission_missing'
  | 'region_mismatch';

export interface ExportPolicyDecision {
  allowed: boolean;
  reason: ExportPolicyReason;
  evaluatedAt: string;
}
