// preview.mjs — the full preview (AC-BCL-3): every managed path + action, every machine-scope
// write, and every capability the CLI will declare, before the first byte lands.
import { renderCapabilityLines } from './permissionFloor.mjs';

export function renderPreview({ plan, machineScopeWrites, map }) {
  const lines = [];
  lines.push('The following paths will be written or reconciled under the target root:');
  for (const f of plan) {
    lines.push(`  [${f.action}] ${f.relPath}`);
  }
  lines.push('');
  if (machineScopeWrites.length > 0) {
    lines.push('The following machine-scope writes will be made (outside the target root):');
    for (const w of machineScopeWrites) {
      lines.push(`  [${w.scope}] ${w.description}`);
    }
  } else {
    lines.push('No machine-scope writes (no --gh-account given).');
  }
  lines.push('');
  lines.push('The following capabilities will be declared (never granted — the workspace trust dialog decides):');
  lines.push(...renderCapabilityLines(map));
  return lines.join('\n');
}

export const TRUST_HANDOFF_TEXT = (dir) => `
The workspace has been written. The permission floor above is a declaration, not a grant: the
platform's trust dialog is the consent ceremony, and the \`allow\` rules take effect only after
you accept it — the dialog lists them.

  cd ${dir}
  claude
  > (accept the trust dialog when prompted)
  > /foundry:init

Once the session is open, run \`/foundry:doctor\` — it compares this written floor against the
installed plugin's own copy of the map, the one independent check that did not travel through npm.
`;
