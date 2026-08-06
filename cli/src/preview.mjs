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

// `isGitRepo` is computed by the CALLER and passed in, so this module stays pure (it renders text;
// it does not probe the filesystem).
//
// WHY THE GIT STEP IS PRINTED AND NOT PERFORMED. A workspace that is not a git repository is a
// dead end: the factory's whole discipline is branch-per-atom → PR → merge floor, and the setup
// path tells the reader to apply branch protection a few steps later — which needs a repo AND a
// remote. This CLI ALREADY writes a `.gitignore`, which means nothing without one.
//
// It is still printed rather than run. This CLI's contract is that it writes exactly the files it
// previewed and nothing else; `git init` would be an un-previewed side effect, and the choice of
// remote is genuinely the reader's. `create-vite` prints its next steps the same way, for the same
// reason. Telling keeps the promise; doing would quietly break it.
export const TRUST_HANDOFF_TEXT = (dir, { isGitRepo = true } = {}) => `
The workspace has been written. The permission floor above is a declaration, not a grant: the
platform's trust dialog is the consent ceremony, and the \`allow\` rules take effect only after
you accept it — the dialog lists them.

  cd ${dir}${isGitRepo ? '' : `
  git init && git add -A && git commit -m 'workspace seed'
  # ^ this directory is NOT a git repository yet. The factory works through
  #   branch-per-atom → PR → the merge floor, so it needs a repo and a remote.
  #   Create the remote too (e.g. \`gh repo create <you>/<project>-handbook --private --source=. --push\`)
  #   before you reach the branch-protection step.`}
  claude
  > (accept the trust dialog when prompted)
  > /foundry:init

Once the session is open, run \`/foundry:doctor\` — it compares this written floor against the
installed plugin's own copy of the map, the one independent check that did not travel through npm.
`;
