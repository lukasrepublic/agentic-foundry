// cli/test/handoff.test.mjs — the trust-handoff text's git-repo branch.
//
// The GP-3 stranger walk (2026-08-06) ran step 0 exactly as the published README says and landed
// in a NON-GIT directory holding a .gitignore, while the setup path told the reader to apply
// branch protection a few steps later. The factory works through branch-per-atom -> PR -> merge
// floor; without a repo and a remote that is a dead end, and nothing in the output said so.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TRUST_HANDOFF_TEXT } from '../src/preview.mjs';

test('a non-git target is told to init a repo, and why', () => {
  const out = TRUST_HANDOFF_TEXT('/tmp/ws', { isGitRepo: false });
  assert.match(out, /git init/, 'a non-git workspace must be told to initialise one');
  assert.match(out, /NOT a git repository/);
  assert.match(out, /remote/, 'branch protection needs a remote, not just a local repo');
  // Ordering matters: the reader follows this top-to-bottom, and `claude` before `git init` would
  // put them in a session in a non-repo — exactly the state the walk found.
  assert.ok(out.indexOf('git init') < out.indexOf('claude'),
    'the git step must precede opening the session');
});

test('an existing git repo is not told to re-init', () => {
  const out = TRUST_HANDOFF_TEXT('/tmp/ws', { isGitRepo: true });
  assert.doesNotMatch(out, /git init/,
    'telling a reader to re-init an existing repo is wrong and alarming');
  assert.match(out, /claude/);
});

test('the default is the quiet branch — callers that pass nothing must not emit the git step', () => {
  // Defaulting to isGitRepo:false would print the init advice to every --existing adopter who
  // already has a repo, which is the noisier and more wrong direction.
  assert.doesNotMatch(TRUST_HANDOFF_TEXT('/tmp/ws'), /git init/);
});
