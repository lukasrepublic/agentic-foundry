// scaffold.mjs — the closed, schema-valid seven-file scaffold (AC-BCL-6) + settings.json,
// materialized through the confinement join (AC-BCL-9). Template entries are declared once, here.
import fs from 'node:fs';
import path from 'node:path';
import { confinedJoin, RefusalError } from './util.mjs';

/** The closed template-materialization manifest (AC-BCL-6's six template-derived paths;
 * .claude/settings.json is the seventh, generated separately — see buildManagedFiles). */
export const TEMPLATE_ENTRIES = Object.freeze([
  { template: 'CLAUDE.md.tmpl', target: 'CLAUDE.md' },
  { template: 'gitignore.tmpl', target: '.gitignore' },
  { template: 'foundry-project.json.tmpl', target: '.claude/foundry-project.json' },
  { template: 'specs-features-README.md', target: 'specs/features/README.md' },
  { template: 'specs-lifecycle-README.md', target: 'specs/lifecycle/README.md' },
  { template: 'foundry-README.md', target: '.foundry/README.md' },
]);

/** Render a template's raw text with the CLAUDE.md substitutions (the only templated file). */
function renderTemplate(target, raw, { projectName, stageMode }) {
  if (target === 'CLAUDE.md') {
    return raw.replaceAll('{{PROJECT_NAME}}', projectName).replaceAll('{{STAGE_MODE}}', stageMode);
  }
  return raw;
}

/** Build the full managed-file set: the six templated paths + the generated settings.json,
 * joined onto the physically-resolved target root, refusing (RefusalError) any entry whose
 * resolution escapes it (AC-BCL-9). `entries` defaults to TEMPLATE_ENTRIES; tests may inject a
 * mutated list to exercise the traversal refusal without touching the shipped manifest. */
export function buildManagedFiles({
  templatesDir,
  physicalRoot,
  projectName,
  stageMode,
  settingsBytes,
  entries = TEMPLATE_ENTRIES,
}) {
  const files = [];
  for (const entry of entries) {
    const joined = confinedJoin(physicalRoot, entry.target);
    if (joined === null) {
      throw new RefusalError(
        `refusing to materialize ${entry.template}: path escapes the target root (${entry.target})`,
        entry.target,
      );
    }
    const raw = fs.readFileSync(path.join(templatesDir, entry.template), 'utf-8');
    const rendered = renderTemplate(entry.target, raw, { projectName, stageMode });
    files.push({ relPath: entry.target, absPath: joined, bytes: Buffer.from(rendered, 'utf-8') });
  }

  const settingsJoined = confinedJoin(physicalRoot, '.claude/settings.json');
  if (settingsJoined === null) {
    throw new RefusalError(
      'refusing to write .claude/settings.json: path escapes the target root',
      '.claude/settings.json',
    );
  }
  files.push({ relPath: '.claude/settings.json', absPath: settingsJoined, bytes: settingsBytes });
  return files;
}

export const DECLARED_PATH_SET = Object.freeze([
  'CLAUDE.md',
  '.gitignore',
  '.claude/settings.json',
  '.claude/foundry-project.json',
  'specs/features/README.md',
  'specs/lifecycle/README.md',
  '.foundry/README.md',
]);
