import * as React from './react.ts';
import type { ExportPanelModel } from './ExportPanel.ts';

export interface ExportPanelProps {
  model: ExportPanelModel;
  onExport: () => void;
}

// React component kept deliberately thin: policy comes from the controller's
// server/cache decision, while this layer owns accessible button rendering.
export function ExportPanel({ model, onExport }: ExportPanelProps): React.ReactElement {
  return React.createElement(
    'button',
    {
      type: 'button',
      disabled: !model.enabled,
      'aria-disabled': !model.enabled,
      'data-policy-source': model.source,
      'data-policy-reason': model.reason,
      onClick: onExport,
    },
    model.label,
  );
}
