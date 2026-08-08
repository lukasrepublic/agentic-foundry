// answers.mjs — resolves the final answer set from parsed argv + (optionally) interactive
// prompts, honoring AC-BCL-2's "yes-mode: zero prompts" invariant.
import { createInterface } from 'node:readline/promises';
import { RefusalError } from './util.mjs';

function coerceBoolean(raw) {
  const v = String(raw).trim().toLowerCase();
  if (v === '' ) return undefined;
  return v === 'y' || v === 'yes' || v === 'true' || v === '1';
}

/** The bracketed default (AC-WPD-5/-6/-7). A boolean renders `[y]`/`[n]` rather than its literal
 * value — `[false]` reads as a type, not an affordance, on a yes/no question. An empty-string
 * default renders `[blank]`: it states positively that a default exists, that it is blank, and
 * therefore that Enter is safe. Rendering `[]` would read as noise instead. */
export function defaultToken(rec) {
  if (rec.default === undefined) return '';
  if (rec.type === 'boolean') return rec.default ? '[y]' : '[n]';
  return rec.default === '' ? '[blank]' : `[${rec.default}]`;
}

/** `(choices) [default]` — both when both are declared (AC-WPD-4). A boolean carries the implicit
 * `(y/n)` vocabulary (AC-WPD-7). */
export function promptSuffix(rec) {
  const parts = [];
  if (rec.choices) parts.push(`(${rec.choices.join('/')})`);
  else if (rec.type === 'boolean') parts.push('(y/n)');
  const dflt = defaultToken(rec);
  if (dflt) parts.push(dflt);
  return parts.length ? ` ${parts.join(' ')}` : '';
}

/** The description block printed ABOVE the prompt line (AC-WPD-2), with one indented line per
 * enumerated choice BETWEEN the description and the prompt (AC-WPD-3). Derived from the question
 * table and from no second list (AC-WPD-12). */
export function renderPromptBlock(rec) {
  // Leading blank line: readline echoes no newline after a piped answer, so without this the next
  // question's description starts on the same line as the previous prompt. Separating the blocks
  // is also simply easier to read in a real terminal.
  const lines = ['', ...String(rec.description).split('\n')];
  if (rec.choices) {
    const width = Math.max(...rec.choices.map((c) => c.length));
    for (const c of rec.choices) {
      const note = (rec.choiceDescriptions || {})[c] || '';
      lines.push(`  ${c.padEnd(width)}  ${note}`.trimEnd());
    }
  }
  return lines.join('\n') + '\n';
}

/** yesMode is true when --yes was given OR stdin is not a TTY (AC-BCL-2): both suppress every
 * prompt, including the write-phase confirmation (AC-BCL-3). */
export function isYesMode(values, isTTY) {
  return values.yes === true || !isTTY;
}

/** Resolve every table record to a final value. In yes-mode: apply declared defaults, refusing
 * (naming the flag) on an unanswered required record. Interactively: prompt only records marked
 * `interactive: true` and not already provided by a flag; a non-interactive record always takes
 * its provided value or its default. */
export async function resolveAnswers(table, parsed, { yesMode, input, output }) {
  const { values, provided } = parsed;
  const resolved = { ...values };

  const needsPrompt = yesMode
    ? []
    : table.filter((r) => r.interactive && !provided.has(r.id));

  let rl = null;
  if (needsPrompt.length > 0) {
    rl = createInterface({ input, output });
  }
  try {
    for (const rec of table) {
      if (provided.has(rec.id)) continue;
      if (yesMode || !rec.interactive) {
        if (rec.required) {
          throw new RefusalError(`missing required flag: --${rec.flag}`, rec.flag);
        }
        resolved[rec.id] = rec.default;
        continue;
      }
      // The suffix is ADDITIVE (AC-WPD-4): choices and default render TOGETHER. The prior
      // mutually-exclusive ternary suppressed the default on every record declaring choices —
      // structurally, so the one record where a default matters most could never show it.
      output.write(renderPromptBlock(rec));
      const answer = (await rl.question(`${rec.prompt}${promptSuffix(rec)}: `)).trim();
      if (rec.type === 'boolean') {
        const b = coerceBoolean(answer);
        resolved[rec.id] = b === undefined ? rec.default : b;
      } else if (answer === '') {
        if (rec.required) {
          throw new RefusalError(`missing required flag: --${rec.flag}`, rec.flag);
        }
        resolved[rec.id] = rec.default;
      } else {
        if (rec.choices && !rec.choices.includes(answer)) {
          throw new RefusalError(
            `--${rec.flag} must be one of: ${rec.choices.join(', ')} (got ${JSON.stringify(answer)})`,
            rec.flag,
          );
        }
        resolved[rec.id] = answer;
      }
    }
  } finally {
    if (rl) rl.close();
  }
  return resolved;
}
