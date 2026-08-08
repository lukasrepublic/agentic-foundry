// argv.mjs — the argv parser. Its accepted long-flag set is derived from the question table AND
// FROM NO SECOND LIST (AC-BCL-2). --help is rendered one line per table record.
import { RefusalError } from './util.mjs';

/** Parse argv against `table` (defaults to QUESTION_TABLE at call sites). Returns
 * { values: {id: value}, provided: Set<id> }. Throws RefusalError on an unknown flag, a missing
 * value, or an out-of-`choices` value — writing nothing is the caller's responsibility (this
 * function performs no I/O). */
export function parseArgv(argv, table) {
  const byFlag = new Map(table.map((r) => [r.flag, r]));
  const values = {};
  const provided = new Set();

  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith('--')) {
      throw new RefusalError(`unexpected positional argument: ${tok}`);
    }
    let name = tok.slice(2);
    let inline = null;
    const eq = name.indexOf('=');
    if (eq !== -1) {
      inline = name.slice(eq + 1);
      name = name.slice(0, eq);
    }
    const rec = byFlag.get(name);
    if (!rec) {
      throw new RefusalError(`unknown flag: --${name}`, name);
    }
    if (rec.type === 'boolean') {
      values[rec.id] = inline === null ? true : inline === 'true';
    } else {
      let v = inline;
      if (v === null) {
        const next = argv[i + 1];
        if (next === undefined || (next.startsWith('--') && next.length > 2)) {
          throw new RefusalError(`--${rec.flag} needs a value`, rec.flag);
        }
        v = next;
        i++;
      }
      if (rec.choices && !rec.choices.includes(v)) {
        throw new RefusalError(
          `--${rec.flag} must be one of: ${rec.choices.join(', ')} (got ${JSON.stringify(v)})`,
          rec.flag,
        );
      }
      values[rec.id] = v;
    }
    provided.add(rec.id);
  }
  return { values, provided };
}

/** Render one `--help` line per table record — the third leg of the flag/prompt/help bijection. */
export function renderHelp(table, { programName = 'create-agentic-workspace' } = {}) {
  const lines = [`Usage: ${programName} [options]`, ''];
  for (const rec of table) {
    const flagCol = rec.type === 'boolean' ? `--${rec.flag}` : `--${rec.flag} <value>`;
    const req = rec.required ? ' (required)' : rec.default !== undefined && rec.default !== '' ? ` (default: ${rec.default})` : '';
    const choices = rec.choices ? ` [choices: ${rec.choices.join('|')}]` : '';
    lines.push(`  ${flagCol.padEnd(24)} ${rec.prompt}${choices}${req}`);
    // The description renders as an indented continuation beneath its flag line (AC-WPD-10),
    // across as many physical lines as the description itself contains, then one further
    // indented line per declared choice (AC-WPD-11). Same table, no second list (AC-WPD-12).
    for (const dline of String(rec.description).split('\n')) {
      lines.push(dline ? `${' '.repeat(27)}${dline}` : '');
    }
    if (rec.choices) {
      const width = Math.max(...rec.choices.map((c) => c.length));
      for (const c of rec.choices) {
        const note = (rec.choiceDescriptions || {})[c] || '';
        lines.push(`${' '.repeat(27)}  ${c.padEnd(width)}  ${note}`.trimEnd());
      }
    }
  }
  lines.push('');
  lines.push('This CLI collects and transmits nothing: no telemetry.');
  return lines.join('\n');
}
