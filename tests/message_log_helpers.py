"""Test-only scaffolding for the shared message log's ruleset check (gv-09).

Lives under tests/ on purpose: it resets and force-pushes, which production code must never do.
"""
import foundry_message_log as ml

_REJECTION_WORDS = ("rejected", "denied", "declined", "protected")


def git_equivalent_of(template):
    """The bare-remote settings that enforce the same rule types locally."""
    types = {r["type"] for r in template["rules"]}
    cfg = {}
    if "non_fast_forward" in types:
        cfg["receive.denyNonFastForwards"] = "true"
    if "deletion" in types:
        cfg["receive.denyDeletes"] = "true"
    return cfg


def observe_ruleset_enforcement(ad, workdir, remote, branch):
    """Normal push (control), then a force-push to `branch`, and report what was OBSERVED:
    `enforced` only when the control is accepted AND the force-push fails WITH a rejection message;
    `unverifiable:tier-b` when the force-push is accepted (rules created, not enforced);
    `unverifiable:control-failed` when the normal push was refused; `unverifiable:no-rejection-message` when
    the force-push failed for a reason that is not a rejection (transport, auth). Never reports an unobserved pass.
    `workdir` must be a scratch clone: this resets its HEAD."""
    env = ml._identity_env(ad, workdir)

    def commit(msg):
        ml._git_ok(ad, workdir, ["-c", "commit.gpgsign=false", "commit", "--allow-empty", "-q", "-m", msg], "commit", env=env)

    commit("observe: base")
    commit("observe: control")
    if ad.git(["push", remote, f"HEAD:refs/heads/{branch}"], workdir).returncode != 0:
        return "unverifiable:control-failed"
    ml._git_ok(ad, workdir, ["reset", "--hard", "-q", "HEAD~1"], "reset")
    commit("observe: diverging")
    forced = ad.git(["push", "--force", remote, f"HEAD:refs/heads/{branch}"], workdir)
    if forced.returncode == 0:
        return "unverifiable:tier-b"
    text = ((forced.stdout or "") + (forced.stderr or "")).lower()
    return "enforced" if any(w in text for w in _REJECTION_WORDS) else "unverifiable:no-rejection-message"
