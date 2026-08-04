// questions.mjs — THE single question table (AC-BCL-2). Every accepted long flag, every --help
// line and every interactive prompt are derived from this table and from no second list.
//
// Record shape: { id, flag, type: 'string'|'boolean', prompt, default? , required?, choices?,
//                 interactive } — `default` XOR `required: true` (never both, never neither).

export const QUESTION_TABLE = [
  {
    id: 'dir',
    flag: 'dir',
    type: 'string',
    prompt: 'Workspace directory to create',
    required: true,
    interactive: true,
  },
  {
    id: 'existing',
    flag: 'existing',
    type: 'boolean',
    prompt: 'Target directory already exists and is non-empty (scaffold into it; never clobbers)',
    default: false,
    interactive: true,
  },
  {
    id: 'stageMode',
    flag: 'stage-mode',
    type: 'string',
    prompt: 'Stage mode',
    default: 'lean',
    choices: ['lean', 'scale'],
    interactive: true,
  },
  {
    id: 'ghAccount',
    flag: 'gh-account',
    type: 'string',
    prompt: 'GitHub account slug for commit-identity isolation (blank to skip)',
    default: '',
    interactive: true,
  },
  {
    id: 'gitAuthor',
    flag: 'git-author',
    type: 'string',
    prompt: 'Git author "Name <email>" (blank: try gh, then prompt, then fail closed — only used with --gh-account)',
    default: '',
    interactive: true,
  },
  {
    id: 'yes',
    flag: 'yes',
    type: 'boolean',
    prompt: 'Accept every default and the write-phase confirmation without prompting',
    default: false,
    interactive: false,
  },
  {
    id: 'dryRun',
    flag: 'dry-run',
    type: 'boolean',
    prompt: 'Print the preview only; make no write and no side effect',
    default: false,
    interactive: false,
  },
  {
    id: 'help',
    flag: 'help',
    type: 'boolean',
    prompt: 'Show this help and exit',
    default: false,
    interactive: false,
  },
];

export function tableFlags(table = QUESTION_TABLE) {
  return table.map((r) => r.flag);
}
