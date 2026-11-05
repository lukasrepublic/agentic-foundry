#!/usr/bin/env python3
"""foundry-secret-read-guard — PreToolUse(Bash) refusal of commands that READ A SECRET VALUE (gv-11, rule D1).

D1: verify a secret by METADATA only (does it exist, its version, its ARN, who may read it); never read,
echo, or round-trip the value. This hook refuses the commands whose job is to print one, and says which
verb tripped it. `docs/how-to/secrets-never-read.md` is the operator-facing page.

*** A COMMAND-TEXT HOOK IS NOT A BOUNDARY. *** Claude Code's own documentation says Bash deny rules are not a
security boundary (they match the usual invocation text, not `/usr/bin/x`, `sh -c`, or a script that does the
read for you); and the sandbox has no built-in credential deny list, only the files and variables you list.
This hook catches the mistaken and the lazy read, loudly, with the verb named. The control that actually
prevents a read is an IAM-level one: give the agent's identity no `secretsmanager:GetSecretValue`, no
`ssm:GetParameter` with decryption, no `kms:Decrypt` on the key, a Kubernetes role without `get` on
`secrets`. If the identity cannot read the value, no command text can.

DEFAULT FORBIDDEN VERBS (the built-in floor; the names below are what a refusal prints). Each is a verb whose
documented output is the secret value itself:
  aws secretsmanager get-secret-value | batch-get-secret-value      (AWS CLI: returns SecretString/SecretBinary)
  aws ssm get-parameter(s)[-by-path] --with-decryption               (AWS CLI: decrypts SecureString values)
  aws kms decrypt                                                    (returns the plaintext)
  aws configure export-credentials | configure get <secret key>      (prints the key pair / session token)
  aws sts get-session-token | assume-role* | get-federation-token    (print temporary credentials)
  aws iam create-access-key | create-service-specific-credential     (print the secret half once)
  aws ecr get-login-password                                         (prints a registry token)
  gcloud secrets versions access                                     (gcloud: prints the payload)
  gcloud auth print-access-token | print-identity-token              (also: auth application-default ...)
  gcloud iam service-accounts keys <anything but list>               (create prints the private key)
  az keyvault secret show|download, certificate download             (az: value / private key)
  az account get-access-token, az storage account keys list, az acr credential show
  vault kv get | vault read                                          (HashiCorp Vault: the secret's data)
  kubectl|oc get secret(s) -o <anything but name|wide>               (yaml/json/jsonpath/go-template print base64 data)
  kubectl config view --raw, kubectl create token, kubectl view-secret
  op read | item get | inject | document get                         (1Password CLI)
  gh auth token, gh auth status --show-token                         (the GitHub token)
  terraform|tofu show -json, state pull, state show, output -json|-raw   (state resolves secrets in clear)
  pass show, gopass show, secret-tool lookup, security find-*-password, gpg --export-secret-keys
  printenv / env that dumps the environment, echo/printf of a $SECRET-like variable
  a READER (cat, head, grep, cp, scp, tar, curl -d @file, an interpreter's inline open(), …) pointed at a
  NO-PRINT ROOT: ~/.ssh, ~/.aws, ~/.config/gcloud, ~/.kube, .env* (not .env.example/.sample/.template/.dist/.defaults),
  ~/.git-credentials, ~/.netrc, ~/.docker/config.json, ~/.npmrc, ~/.pypirc, ~/.config/gh, ~/.claude/.credentials.json,
  ~/.gnupg, ~/.vault-token, ~/.azure, ~/.terraform.d, *.tfstate*, /proc/*/environ, /proc/*/task/*/environ (`~/x` also means x under any
  user's home; cp/mv/install are judged by their SOURCE operands; here-strings into a reader and brace-expanded
  reader paths are refused; a reader whose target is an unknowable `$VAR` is ADMITTED - a practice, not a control;
  gv-20: an option VALUE may sit between two sub-words of a verb, `dd if=<root>` is judged by its `if=`, a recursive read
  of a whole home (`grep -r X ~`, `tar czf x ~`, `rg X $HOME`) is refused, a leading `<root` redirect is read, and
  `/proc/$$/environ` counts)
NOT BANNED, on purpose: `kubectl describe secret` (prints key names and byte sizes only), `aws secretsmanager
describe-secret|list-secrets`, `gcloud secrets versions describe|list`, `kubectl get secret -o name|wide`,
`gcloud iam service-accounts keys list` — metadata verification is what D1 asks for.

POLICY MAY ONLY ADD. `.foundry/operating-model.yaml` `secrets.forbidden_verbs[]` (a verb phrase such as
`aws iam create-login-profile`: the command word then the sub-words in order) and `secrets.no_print_roots[]`
extend the defaults above. A default can never be removed: the defaults live in this file, the policy lists
are merged over them, the schema marks both lists add-only and refuses a removal marker
(`floor-rule-disabled:secrets.forbidden_verbs.removal`), and a broken policy file leaves the defaults in force.
`secrets.value_reads_forbidden` is `const: true`.

Reuses `the foundry_shell_scan module` (heredoc data/code classification, wrapper-aware command position).
A `data` heredoc body is inert; the lines of a CODE heredoc are each scanned as commands. Failure posture:
no recoverable command => admit; an internal error with a command in hand => exit 2 (fail closed). Exit 2
carries the verb name on stderr and as a `permissionDecision: deny` JSON.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scan = None            # imported in main() once a command is in hand: a broken install must fail CLOSED (exit 2)

GUARD = "secret-read-guard"
BOUNDARY_NOTE = ("A command-text hook is not a boundary: only an IAM-level boundary (no read permission on the "
                 "identity) prevents the read. Verify by metadata only (describe/list/exists).")

# ------------------------------------------------------------------------------ default verbs --


def _opts(args):
    return [a for a in args if a.startswith("-")]


class _Words(list):
    """The non-option words of an argument list, each tagged with whether it directly FOLLOWS an option (`--region x`: `x`)."""
    after_opt = ()


def _words(args):
    out, tags, prev_opt = _Words(), [], False
    for a in args:
        if a.startswith("-"):
            prev_opt = "=" not in a and len(a) > 1 and a != "--"
            continue
        out.append(a)
        tags.append(prev_opt)
        prev_opt = False
    out.after_opt = tags
    return out


def _subseq(words, seq):
    """`seq` as a run of `words` that is CONTIGUOUS except for option values (gv-20). Contiguous, not merely ordered:
    `vault kv metadata get` is a metadata verb and must not read as `vault kv get`. A word that directly follows an option
    (`--region x`, `--profile p`) may sit BETWEEN two sub-words (`aws secretsmanager --region x get-secret-value`); a word
    that does not follow an option may not. The run may start anywhere."""
    n = len(seq)
    if n == 0:
        return True
    words = list(words) if not isinstance(words, _Words) else words
    tags = list(getattr(words, "after_opt", ())) or [False] * len(words)
    for i in range(len(words)):
        if words[i] != seq[0]:
            continue
        j, k = 1, i + 1
        while j < n and k < len(words):
            if words[k] == seq[j]:
                j, k = j + 1, k + 1
            elif tags[k]:
                k += 1                          # an option value between two sub-words
            else:
                break
        if j == n:
            return True
    return False


def _flag(args, *names):
    for a in args:
        head = a.split("=", 1)[0]
        if head in names:
            return True
    return False


def _flag_value(args, *names):
    for i, a in enumerate(args):
        head, eq, val = a.partition("=")
        if head in names:
            if eq:
                return val
            return args[i + 1] if i + 1 < len(args) else ""
        if len(a) > 2 and a[:2] in names and not a.startswith("--"):
            return a[2:].lstrip("=")          # -oyaml
    return None


def _verb(cmds, *seq, when=None):
    """Matcher: command word in `cmds`, the sub-words `seq` as an ordered subsequence of its non-option words."""
    cmds = set(cmds)

    def m(cmd, args):
        return cmd in cmds and _subseq(_words(args), seq) and (when is None or when(args))
    return m


def _kube_get_secret(cmd, args):
    if cmd not in ("kubectl", "oc") or "get" not in _words(args):
        return False
    pat = re.compile(r"(^|,)secrets?(\.[a-z0-9.]+)?(/|,|$)", re.I)
    if not any(pat.search(w) for w in _words(args)):
        return False
    out = _flag_value(args, "-o", "--output")
    return out is not None and out.split("=", 1)[0] not in ("name", "wide")


def _aws_configure_get(cmd, args):
    w = _words(args)
    return (cmd == "aws" and _subseq(w, ["configure", "get"]) and
            any(re.search(r"secret|token|key", x, re.I) for x in w[w.index("get") + 1:]))


def _gcloud_sa_keys(cmd, args):
    w = _words(args)
    if cmd != "gcloud" or not _subseq(w, ["iam", "service-accounts", "keys"]):
        return False
    rest = w[w.index("keys") + 1:]
    return not rest or rest[0] not in ("list", "describe", "get-public-key")


def _env_listing(cmd, args):
    if cmd == "export":
        return _flag(args, "-p")
    if cmd == "set":
        return not args
    if cmd in ("declare", "typeset"):
        return any(re.fullmatch(r"-[A-Za-z]*[xp][A-Za-z]*", a) for a in args)
    return False


def _env_dump(cmd, args):
    if cmd == "env":
        return not args or all(a.startswith("-") for a in args)
    if cmd == "printenv":
        w = _words(args)
        return not w or any(re.search(r"SECRET|TOKEN|PASSW|API_?KEY|PRIVATE|CREDENTIAL", x, re.I) for x in w)
    return False


_SECRET_VAR = re.compile(r"\$\{?[A-Za-z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|CREDENTIAL)"
                         r"[A-Za-z0-9_]*", re.I)


def _echo_secret_var(cmd, args):
    return cmd in ("echo", "printf") and any(_SECRET_VAR.search(a) for a in args)


DEFAULT_VERBS = [
    ("aws secretsmanager get-secret-value", _verb(["aws"], "secretsmanager", "get-secret-value")),
    ("aws secretsmanager batch-get-secret-value", _verb(["aws"], "secretsmanager", "batch-get-secret-value")),
    ("aws ssm get-parameter --with-decryption",
     lambda c, a: c == "aws" and _subseq(_words(a), ["ssm"]) and any(
         w in ("get-parameter", "get-parameters", "get-parameters-by-path", "get-parameter-history")
         for w in _words(a)) and _flag(a, "--with-decryption")),
    ("aws eks get-token", _verb(["aws"], "eks", "get-token")),
    ("aws ecr get-authorization-token", _verb(["aws"], "ecr", "get-authorization-token")),
    ("aws sso get-role-credentials", _verb(["aws"], "sso", "get-role-credentials")),
    ("aws codeartifact get-authorization-token", _verb(["aws"], "codeartifact", "get-authorization-token")),
    ("aws rds generate-db-auth-token", _verb(["aws"], "rds", "generate-db-auth-token")),
    ("aws kms decrypt", _verb(["aws"], "kms", "decrypt")),
    ("aws configure export-credentials", _verb(["aws"], "configure", "export-credentials")),
    ("aws configure get <secret key>", _aws_configure_get),
    ("aws sts get-session-token", _verb(["aws"], "sts", "get-session-token")),
    ("aws sts assume-role", lambda c, a: c == "aws" and _subseq(_words(a), ["sts"]) and any(
        w.startswith("assume-role") or w == "get-federation-token" for w in _words(a))),
    ("aws iam create-access-key", _verb(["aws"], "iam", "create-access-key")),
    ("aws iam create-service-specific-credential", _verb(["aws"], "iam", "create-service-specific-credential")),
    ("aws ecr get-login-password", _verb(["aws"], "ecr", "get-login-password")),
    ("gcloud secrets versions access", _verb(["gcloud"], "secrets", "versions", "access")),
    ("gcloud auth print-access-token", lambda c, a: c == "gcloud" and "auth" in _words(a) and any(
        w in ("print-access-token", "print-identity-token") for w in _words(a))),
    ("gcloud iam service-accounts keys", _gcloud_sa_keys),
    ("az keyvault secret show", _verb(["az"], "keyvault", "secret", "show")),
    ("az keyvault secret download", _verb(["az"], "keyvault", "secret", "download")),
    ("az keyvault certificate download", _verb(["az"], "keyvault", "certificate", "download")),
    ("az account get-access-token", _verb(["az"], "account", "get-access-token")),
    ("az storage account keys list", _verb(["az"], "storage", "account", "keys", "list")),
    ("az acr credential show", _verb(["az"], "acr", "credential", "show")),
    ("vault kv get", _verb(["vault"], "kv", "get")),
    ("vault read", _verb(["vault"], "read")),
    ("kubectl get secret -o yaml|json|jsonpath", _kube_get_secret),
    ("kubectl get secret --template", lambda c, a: c in ("kubectl", "oc") and "get" in _words(a) and any(
        re.search(r"(^|,)secrets?(\.[a-z0-9.]+)?(/|,|$)", w, re.I) for w in _words(a)) and _flag(a, "--template")),
    ("kubectl config view --raw", _verb(["kubectl", "oc"], "config", "view", when=lambda a: _flag(a, "--raw"))),
    ("kubectl create token", _verb(["kubectl", "oc"], "create", "token")),
    ("kubectl view-secret", _verb(["kubectl", "oc"], "view-secret")),
    ("op read", _verb(["op"], "read")),
    ("op item get", _verb(["op"], "item", "get")),
    ("op inject", _verb(["op"], "inject")),
    ("op document get", _verb(["op"], "document", "get")),
    ("gh auth token", _verb(["gh"], "auth", "token")),
    ("gh auth status --show-token", _verb(["gh"], "auth", "status", when=lambda a: _flag(a, "--show-token", "-t"))),
    ("terraform show -json", _verb(["terraform", "tofu"], "show", when=lambda a: _flag(a, "-json", "--json"))),
    ("terraform state pull", _verb(["terraform", "tofu"], "state", "pull")),
    ("terraform state show", _verb(["terraform", "tofu"], "state", "show")),
    ("terraform output -json|-raw", _verb(["terraform", "tofu"], "output",
                                          when=lambda a: _flag(a, "-json", "--json", "-raw", "--raw"))),
    ("pass show", _verb(["pass", "gopass"], "show")),
    ("secret-tool lookup", _verb(["secret-tool"], "lookup")),
    ("security find-generic-password", lambda c, a: c == "security" and any(
        w in ("find-generic-password", "find-internet-password") for w in _words(a)) and _flag(a, "-w", "-g")),
    ("gpg --export-secret-keys", lambda c, a: c in ("gpg", "gpg2") and _flag(a, "--export-secret-keys",
                                                                              "--export-secret-subkeys")),
    ("git credential fill", _verb(["git"], "credential", "fill")),
    ("sops -d", lambda c, a: c == "sops" and (_flag(a, "-d", "--decrypt") or "decrypt" in _words(a)
                                                or "exec-env" in _words(a) or "exec-file" in _words(a))),
    ("ansible-vault view|decrypt", lambda c, a: c == "ansible-vault" and any(
        w in ("view", "decrypt") for w in _words(a))),
    ("gpg --decrypt", lambda c, a: c in ("gpg", "gpg2") and (
        _flag(a, "-d", "--decrypt", "--decrypt-files") or any(re.fullmatch(r"-[A-Za-z]*d[A-Za-z]*", x) for x in a))),
    ("age --decrypt", lambda c, a: c in ("age", "rage") and (_flag(a, "--decrypt") or any(
        re.fullmatch(r"-[A-Za-z]*d[A-Za-z]*", x) for x in a))),
    ("vault print token", _verb(["vault"], "print", "token")),
    ("doppler secrets get", lambda c, a: c == "doppler" and _subseq(_words(a), ["secrets"]) and any(
        w in ("get", "download") for w in _words(a))),
    ("export -p / set / declare -x (dumps the environment)", _env_listing),
    ("printenv / env (dumps the environment)", _env_dump),
    ("echo of a secret-named variable", _echo_secret_var),
]
DEFAULT_VERB_NAMES = [n for n, _ in DEFAULT_VERBS]

DEFAULT_ROOTS = ["~/.ssh", "~/.aws", "~/.config/gcloud", "~/.kube", ".env*", "~/.git-credentials", "~/.netrc",
                 "~/.docker/config.json", "~/.npmrc", "~/.pypirc", "~/.config/gh", "~/.claude/.credentials.json",
                 "~/.gnupg", "~/.vault-token", "~/.azure", "~/.terraform.d", "*.tfstate*", "/proc/*/environ", "/proc/*/task/*/environ"]
ROOT_EXEMPT_SUFFIX = (".example", ".sample", ".template", ".dist", ".defaults")
READERS = {
    "cat", "tac", "less", "more", "head", "tail", "bat", "batcat", "nl", "od", "xxd", "hexdump", "strings", "base64",
    "cut", "sort", "uniq", "grep", "egrep", "fgrep", "rg", "ag", "awk", "gawk", "sed", "paste", "fold", "rev", "jq",
    "yq", "cp", "mv", "install", "scp", "rsync", "sftp", "tar", "zip", "gzip", "bzip2", "xz", "7z", "zstd", "diff", "cmp", "comm",
    "openssl", "tee", "dd", "curl", "wget", "xclip", "pbcopy", "wl-copy", "source", ".", "vi", "vim", "nano", "emacs",
}
INTERPRETERS = re.compile(r"^(python[0-9.]*|node|nodejs|ruby|perl|php|lua|deno|bun)$")
# curl/wget read a file only when told to upload it
_UPLOAD_FLAGS = ("-d", "--data", "--data-binary", "--data-raw", "--data-urlencode", "-F", "--form", "-T",
                 "--upload-file", "--post-file", "--body-file")


# ---------------------------------------------------------------------------------- root matching --
def _expand(word, cwd):
    w = word.replace("${HOME}", "~").replace("$HOME", "~")
    if w.startswith("~"):
        w = os.path.expanduser(w)                       # `~` and `~user` (an unknown user stays `~user/…`)
        if w.startswith("~"):
            w = "/home/" + w[1:]
    if not os.path.isabs(w):
        w = os.path.join(cwd, w)
    return os.path.normpath(w)


def _components(path):
    return [c for c in path.split("/") if c]


def _root_matcher(root, cwd):
    """-> fn(abs_path_pattern) -> bool. A root with no slash is a basename glob matched against every path
    component; `~/…` or `/…` is a directory prefix (component-wise fnmatch, so a glob in the word still hits);
    a relative `a/b` root matches that component run anywhere."""
    exempt = ROOT_EXEMPT_SUFFIX

    if root.startswith("~") or root.startswith("/"):
        rcomp = _components(os.path.normpath(os.path.expanduser(root)))

        tail = _components(root[2:]) if root.startswith("~/") else None

        def _run(pcomp, rc_list):
            return len(pcomp) >= len(rc_list) and all(fnmatch.fnmatchcase(rc, pc) or fnmatch.fnmatchcase(pc, rc)
                                                      for rc, pc in zip(rc_list, pcomp))

        def m(path, raw):
            pcomp = _components(path)
            if _run(pcomp, rcomp):
                return True
            if tail:                      # `~/x` is x under ANY user's home, not only the current user's
                for skip in (1, 2):       # /root/x, /home/<u>/x, /Users/<u>/x
                    if (len(pcomp) > skip and ((skip == 1 and pcomp[0] == "root") or (
                            skip == 2 and pcomp[0] in ("home", "Users"))) and _run(pcomp[skip:], tail)):
                        return True
            return False
        return m
    if "/" not in root:
        def m(path, raw):         # the WORD's own components: the session cwd's names are not the target's
            return any(fnmatch.fnmatchcase(c, root) and not c.endswith(exempt) for c in _components(raw))
        return m
    rcomp = _components(os.path.normpath(root))

    def m(path, raw):
        pcomp = _components(raw)
        return any(all(fnmatch.fnmatchcase(pcomp[i + j], rcomp[j]) for j in range(len(rcomp)))
                   for i in range(len(pcomp) - len(rcomp) + 1))
    return m


def _touches_root(word, cwd, matchers):
    if not word or word.startswith("-") and "/" not in word and "." not in word:
        return None
    w = word
    if "=" in w and w.startswith("-"):
        w = w.split("=", 1)[1]
    if w.startswith("@"):
        w = w[1:]
    if re.fullmatch(r"/proc/[^/]*/(?:task/[^/]*/)?environ", w):
        return "/proc/*/environ"                    # `/proc/$$/environ`, `/proc/$PID/task/1/environ`: any pid, expanded or not
    if "$" in w and not (w.startswith("$HOME") or w.startswith("${HOME}")):
        return None
    path = _expand(w, cwd)
    for root, m in matchers:
        if m(path, w):
            return root
        # symlink: the real target of an existing path
        try:
            if os.path.lexists(path) and m(os.path.realpath(path), os.path.realpath(path)):
                return root
        except OSError:
            pass
    return None


# ------------------------------------------------------------------------------------ the checks --
class Verdict:
    def __init__(self, verb, detail):
        self.verb, self.detail = verb, detail


def _phrase_matcher(phrase):
    toks = phrase.split()
    if not toks:
        return None
    first, rest = scan._cmd_basename(toks[0]), toks[1:]

    def m(cmd, args):
        return cmd == first and _subseq(_words(args), rest)
    return m


def check_invocation(inv, cwd, verbs, matchers, depth=0):
    cmd, args = inv.cmd, inv.args
    for name, fn in verbs:
        if fn(cmd, args):
            return Verdict(name, "forbidden verb `%s`" % name)
    # an inline shell payload (`bash -c '…'`, `eval …`) is scanned as a command string of its own
    if depth < 3 and cmd in ("bash", "sh", "zsh", "dash", "ksh", "eval"):
        payload = None
        if cmd == "eval":
            payload = " ".join(args)
        else:
            for i, a in enumerate(args):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a) and i + 1 < len(args):
                    payload = args[i + 1]
                    break
        if payload:
            v = check_text(payload, cwd, verbs, matchers, depth + 1)
            if v:
                return v
    # roots: a reader pointed at one, a `<` redirect from one, an interpreter's inline text naming one
    reads = cmd in READERS
    if cmd == "tar":                              # gv-20: operands are relative to `-C DIR` / `--directory DIR`
        for k, a in enumerate(args):
            d = args[k + 1] if a in ("-C", "--directory") and k + 1 < len(args) else (
                a.split("=", 1)[1] if a.startswith("--directory=") else None)
            if d:
                cwd = _expand(d, cwd)
    if cmd in ("curl", "wget"):
        reads = any(a.split("=", 1)[0] in _UPLOAD_FLAGS or a.startswith("-d@") for a in args) or \
            any("@" in a for a in args)
    for tgt in getattr(inv, "redirs", ()):          # `</root/.ssh/id_rsa cat`: a redirect that led the clause
        root = _touches_root(tgt, cwd, matchers)
        if root:
            return Verdict("read of no-print root %s" % root, "`<` redirect from %s" % tgt)
    words = [cmd] + list(args)
    for i, w in enumerate(words):
        if w.startswith("<") and not w.startswith(("<<", "<(")):
            tgt = w[1:] or (words[i + 1] if i + 1 < len(words) else "")
            root = _touches_root(tgt, cwd, matchers)
            if root:
                return Verdict("read of no-print root %s" % root, "`<` redirect from %s" % tgt)
    if reads and any(w.startswith("<<<") for w in words):
        return Verdict("here-string into %s" % cmd, "`%s <<<` decodes or prints a value this scan cannot see" % cmd)
    if reads:
        operands = args
        if cmd in ("cp", "mv", "install"):       # judged by SOURCE operands only: `cp .env.example .env` is fine
            operands = _copy_sources(args)
        elif cmd == "dd":                        # `dd if=~/.ssh/id_rsa`: the SOURCE is the `if=` value
            operands = [a.split("=", 1)[1] for a in args if a.startswith("if=")]
        if _recursive_read(cmd, args):
            for w in operands:
                if _whole_home(w, cwd):
                    return Verdict("whole-home recursive read by %s" % cmd,
                                   "`%s` reads %s recursively, which holds the no-print roots" % (cmd, w))
        for w in operands:
            if "{" in w and "}" in w and ("," in w or ".." in w):
                return Verdict("brace expansion in %s path" % cmd, "`%s` path %s uses brace expansion" % (cmd, w))
            root = _touches_root(w, cwd, matchers)
            if root:
                return Verdict("%s of no-print root %s" % (cmd, root), "`%s` reads %s" % (cmd, w))
    if INTERPRETERS.match(cmd):
        # inline text (`python3 -c "open('~/.aws/credentials')"`): every path-shaped token is judged like an argument
        for tok in re.findall(r"[A-Za-z0-9_.~/$\-]+", " ".join(args)):
            root = _touches_root(tok, cwd, matchers)
            if root:
                return Verdict("%s naming no-print root %s" % (cmd, root), "inline `%s` text names %s" % (cmd, tok))
    return None


_ALWAYS_RECURSIVE = {"rg", "ag", "tar", "zip", "7z"}
_NEEDS_RECURSIVE_FLAG = {"grep", "egrep", "fgrep", "cp", "scp", "rsync"}


def _recursive_read(cmd, args):
    if cmd in _ALWAYS_RECURSIVE:
        return True
    if cmd in _NEEDS_RECURSIVE_FLAG:
        return any(re.fullmatch(r"-[A-Za-z]*[rRa][A-Za-z]*", a) or a in ("--recursive", "--dereference-recursive", "--archive") for a in args) \
            or any(a.startswith("--directories=recurse") for a in args)
    return False


def _whole_home(word, cwd):
    """True when `word` names a home directory or one of its ancestors (`~`, `$HOME`, `/home/u`, `/home`, `/`): a recursive read
    of it reaches every no-print root below."""
    if not word or word.startswith("-"):
        return False
    w = word.replace("${HOME}", "~").replace("$HOME", "~")
    if "$" in w or "`" in w:
        return False
    path = _expand(w.rstrip("/") or "/", cwd)
    home = os.path.normpath(os.path.expanduser("~"))
    return path in ("/", "/home", "/Users", "/root", home, os.path.dirname(home)) or bool(
        re.fullmatch(r"/(?:home|Users)/[^/]+", path))


def _copy_sources(args):
    """The SOURCE operands of cp/mv/install: every operand but the last, or every operand but the `-t DIR` value."""
    ops, target_dir, skip = [], False, False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a in ("-t", "--target-directory"):
            target_dir, skip = True, True
            continue
        if a.startswith("--target-directory="):
            target_dir = True
            continue
        if a.startswith("-") and len(a) > 1:
            continue
        ops.append(a)
    return ops if target_dir else ops[:-1]


def check_text(text, cwd, verbs, matchers, depth=0):
    seen = set()
    nt = scan.neutralize(text)                      # a data heredoc body is inert
    # A CODE heredoc body is a script: scan each of its lines as a command too. (Only then: a prose line
    # inside a multi-line quoted string, e.g. a commit message, is not a command.)
    chunks = [nt] + (nt.splitlines() if scan.has_code_heredoc(nt) else [])
    for chunk in chunks:
        if not chunk.strip():
            continue
        for inv in scan.invocations(chunk):
            key = (inv.cmd, tuple(inv.args), inv.clause, inv.sub)
            if key in seen:
                continue
            seen.add(key)
            v = check_invocation(inv, cwd, verbs, matchers, depth)
            if v:
                return v
    return None


def load_policy_additions(project_dir):
    """(extra verb phrases, extra roots) from the policy file, or ([], []) when absent/broken: a policy file can
    only ADD, so failing to read it can never loosen the defaults."""
    try:
        sys.path.insert(0, os.path.join(_PLUGIN_ROOT, "scripts"))
        import foundry_operating_model as om
        pol = om.load(project_dir)
        verbs = pol.get("secrets.forbidden_verbs") or []
        roots = pol.get("secrets.no_print_roots") or []
        return ([v for v in verbs if isinstance(v, str)], [r for r in roots if isinstance(r, str)])
    except Exception as e:
        reason = (getattr(e, "errors", None) or [str(e) or type(e).__name__])[0]
        sys.stderr.write("policy-additions-dropped:%s\n" % re.sub(r"\s+", " ", str(reason))[:120])
        return [], []


def build_rules(project_dir, cwd):
    verbs = list(DEFAULT_VERBS)
    extra_verbs, extra_roots = load_policy_additions(project_dir)
    for phrase in extra_verbs:
        if phrase.strip().startswith(("!", "-")):
            continue                    # a removal marker is never an addition (and validate refuses the file)
        m = _phrase_matcher(phrase)
        if m:
            verbs.append((phrase.strip(), m))
    roots = list(DEFAULT_ROOTS) + [r for r in extra_roots if r.strip() and not r.strip().startswith(("!", "-"))]
    return verbs, [(r, _root_matcher(r, cwd)) for r in roots]


def _ensure_scan():
    global scan
    if scan is None:
        sys.path.insert(0, os.path.join(_PLUGIN_ROOT, "scripts"))
        import foundry_shell_scan
        scan = foundry_shell_scan


def decide(command, cwd, project_dir):
    _ensure_scan()
    verbs, matchers = build_rules(project_dir, cwd)
    return check_text(command, cwd, verbs, matchers)


def deny(v):
    reason = ("secret-read guard refused: %s. %s" % (v.verb, BOUNDARY_NOTE))
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                               "permissionDecisionReason": reason},
        "observation": {"status": "blocked", "guard": GUARD, "reason": v.detail, "verb": v.verb,
                        "remediation": BOUNDARY_NOTE},
    }) + "\n")
    sys.stderr.write(reason + "\n")
    return 2


def main(argv):
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    ti = payload.get("tool_input") if isinstance(payload, dict) else None
    command = ti.get("command") if isinstance(ti, dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0
    try:
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else os.getcwd()
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
        v = decide(command, cwd, project_dir)
    except Exception as e:      # fail closed once a command is in hand
        sys.stderr.write("secret-read guard refused: internal error (%s: %s); failing closed. %s\n"
                         % (type(e).__name__, e, BOUNDARY_NOTE))
        return 2
    return deny(v) if v else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
