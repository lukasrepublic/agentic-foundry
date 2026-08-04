// answers.mjs — resolves the final answer set from parsed argv + (optionally) interactive
// prompts, honoring AC-BCL-2's "yes-mode: zero prompts" invariant.
import { createInterface } from 'node:readline/promises';
import { RefusalError } from './util.mjs';

function coerceBoolean(raw) {
  const v = String(raw).trim().toLowerCase();
  if (v === '' ) return undefined;
  return v === 'y' || v === 'yes' || v === 'true' || v === '1';
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
      const suffix = rec.choices ? ` (${rec.choices.join('/')})` : rec.default !== undefined && rec.default !== '' ? ` [${rec.default}]` : '';
      const answer = (await rl.question(`${rec.prompt}${suffix}: `)).trim();
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
