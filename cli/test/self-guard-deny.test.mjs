import test from 'node:test';
import assert from 'node:assert/strict';
import { SELF_GUARD_DENY, missingSelfGuardDeny, applySelfGuardDeny, selfGuardShapeOk } from '../src/selfGuardDeny.mjs';

test('self-guard: add-if-absent onto permissions.deny, nothing else touched', () => {
  const s = { enabledPlugins: { x: true }, permissions: { allow: ['A'], deny: ['Edit(.foundry/permissions.yaml)', 'Z'] } };
  assert.deepEqual(missingSelfGuardDeny(s), ['Write(.foundry/permissions.yaml)']);
  const n = applySelfGuardDeny(s);
  assert.deepEqual(n.permissions.deny, ['Edit(.foundry/permissions.yaml)', 'Z', 'Write(.foundry/permissions.yaml)']);
  assert.deepEqual(n.permissions.allow, ['A']);
  assert.deepEqual(n.enabledPlugins, { x: true });
  assert.deepEqual(s.permissions.deny, ['Edit(.foundry/permissions.yaml)', 'Z']); // input untouched
  assert.deepEqual(missingSelfGuardDeny({}), [...SELF_GUARD_DENY]);
  assert.deepEqual(applySelfGuardDeny({}).permissions.deny, [...SELF_GUARD_DENY]);
});

test('self-guard: a malformed deny or permissions is refused, never mangled (review Risk 3)', () => {
  for (const bad of [{ permissions: { deny: 'Edit(x)' } }, { permissions: 'nope' }, { permissions: ['a'] }, { permissions: { deny: { a: 1 } } }]) {
    assert.equal(selfGuardShapeOk(bad), false);
    assert.deepEqual(missingSelfGuardDeny(bad), []);
    assert.equal(applySelfGuardDeny(bad), bad);
  }
  assert.equal(selfGuardShapeOk({ permissions: {} }), true);
});
