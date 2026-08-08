// questions.mjs — THE single question table (AC-BCL-2). Every accepted long flag, every --help
// line and every interactive prompt are derived from this table and from no second list.
//
// Record shape: { id, flag, type: 'string'|'boolean', prompt, description, default? , required?,
//                 choices?, choiceDescriptions?, writesOutsideProject?, interactive }
//               `default` XOR `required: true` (never both, never neither).
//
// `description` (AC-WPD-1) is prose stating what the answer DOES — rendered above the interactive
// prompt (AC-WPD-2) and as an indented continuation in --help (AC-WPD-10). `choiceDescriptions`
// carries one line per enumerated value (AC-WPD-3/-11); `choices` stays a plain string[] so
// argv.mjs's membership check and the frozen AC-BCL-2 refusal test are untouched.
//
// `writesOutsideProject` (AC-WPD-9) marks a record whose answer writes outside the target root.
// Its description MUST state the scope of the effect AND name each such artifact (AC-WPD-8/-14).
// Naming the file without naming the scope is the failure this annotation exists to prevent: the
// draft copy ("writes global git config, machine-wide") was read by the framework's own operator
// as changing their global identity, which it does not do.

export const QUESTION_TABLE = [
  {
    id: 'dir',
    flag: 'dir',
    type: 'string',
    prompt: 'Workspace directory to create',
    description:
      'The folder your specs, config, and factory wiring will live in — one\n' +
      'per project (e.g. ./my-project-handbook). Relative to where you are\n' +
      'now, or an absolute path.\n' +
      'Required — there is no default, so Enter alone will not do.',
    required: true,
    interactive: true,
  },
  {
    id: 'existing',
    flag: 'existing',
    type: 'boolean',
    prompt: 'Scaffold into an existing folder?',
    description:
      'Nothing already there is overwritten — existing files are left alone and\n' +
      'only the missing pieces are added.\n' +
      'Enter alone means no. y, yes, true or 1 mean yes.',
    default: false,
    interactive: true,
  },
  {
    id: 'stageMode',
    flag: 'stage-mode',
    type: 'string',
    prompt: 'Stage mode',
    description:
      'How much process ceremony this workspace enforces.\n' +
      "Recorded in your workspace's CLAUDE.md; agents read it each session,\n" +
      'and you can change it later by editing that line.',
    default: 'lean',
    choices: ['lean', 'scale'],
    choiceDescriptions: {
      lean: 'development loop with lighter ceremony — the solo/small-team norm',
      scale: 'higher ceremony loop enforced end to end',
    },
    interactive: true,
  },
  {
    id: 'ghAccount',
    flag: 'gh-account',
    type: 'string',
    prompt: 'GitHub account slug (blank to skip)',
    description:
      'GitHub account for commit identity.\n' +
      'On a machine with more than one GitHub account, this makes sure commits\n' +
      'in THIS workspace are always authored by the right one.\n' +
      '\n' +
      '  Leave blank to skip. Nothing outside this folder is written.\n' +
      '\n' +
      '  If you enter a slug, your global git identity is NOT changed. Git is\n' +
      '  taught a rule that applies ONLY inside this folder:\n' +
      '    - a small identity file is created at ~/.config/git/identity-<slug>\n' +
      '    - two "only inside this folder" rules are added to your ~/.gitconfig,\n' +
      '      pointing at that file\n' +
      '    - inside this repo, git is told never to guess an identity, so a\n' +
      '      mistake fails loudly instead of committing as the wrong you',
    default: '',
    writesOutsideProject: true,
    interactive: true,
  },
  {
    id: 'gitAuthor',
    flag: 'git-author',
    type: 'string',
    prompt: 'Git author name and email (blank to look up)',
    description:
      'Name and email to record in that identity file.\n' +
      'Only used if you entered an account slug above — otherwise ignored.\n' +
      'Format: Name <email@example.com>\n' +
      'Leave blank to look it up from your GitHub account. If that cannot be\n' +
      "determined you'll be asked; if it still cannot be resolved the run stops\n" +
      'rather than committing under a guessed identity.',
    default: '',
    interactive: true,
  },
  {
    id: 'yes',
    flag: 'yes',
    type: 'boolean',
    prompt: 'Accept every default and skip the confirmation',
    description:
      'Non-interactive mode: every question resolves to its\n' +
      'default and the final write confirmation is skipped.\n' +
      'Required flags must be given on the command line or the\n' +
      'run refuses. Also implied when stdin is not a terminal.',
    default: false,
    interactive: false,
  },
  {
    id: 'dryRun',
    flag: 'dry-run',
    type: 'boolean',
    prompt: 'Print the preview only; make no write',
    description:
      'Shows exactly what would be written — files inside the\n' +
      'workspace and any machine-scope writes outside it — then\n' +
      'exits. Nothing is created; no child process is spawned.',
    default: false,
    interactive: false,
  },
  {
    id: 'help',
    flag: 'help',
    type: 'boolean',
    prompt: 'Show this help and exit',
    description:
      'Prints every option with the same explanation the\n' +
      'interactive prompt shows.',
    default: false,
    interactive: false,
  },
];

export function tableFlags(table = QUESTION_TABLE) {
  return table.map((r) => r.flag);
}
