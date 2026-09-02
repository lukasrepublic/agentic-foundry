// cleanup.mjs — Phase 3 (opt-in, `--cleanup`) of `npx update-agentic-workspace`: prune superseded
// plugin-cache versions and remove a stale/duplicate marketplace registration. THE FIRST recursive
// delete of an adopter path in cli/src/ — every other `rmSync` in this package removes a temp file
// the process itself just wrote. Fail-closed throughout: AC-UWC-4 skips (removes nothing) on any
// indeterminate input, and AC-UWC-9 refuses (removes nothing AT ALL, superseded entries included)
// on any cache entry that is not a plain immediate-child directory of the pinned root.
import fs from 'node:fs';
import path from 'node:path';
import { RefusalError } from './util.mjs';
import {
  runClaude, readInstalledPluginsRegistry, scopeRecordsFor,
  readMarketplaceManifest, pluginEntryOf,
} from './pluginRefresh.mjs';

// ── AC-UWC-1 — the live set is READ from the platform's own state, never computed by sorting ────

/** The union of every scope record's `version` for `<plugin>@<marketplace>` in
 * installed_plugins.json, plus the refreshed marketplace manifest's `version` — NEVER the highest
 * semver on disk. `scripts/foundry-fleet-doctor.py:70-83`'s `installed_index()` is correct for its
 * own job (report ONE adopter's resolved plugin) and unsafe here: it `break`s after the first
 * record and would silently drop a scope pinned to an older version that is still live. */
export function deriveLiveSet({ registry, manifestVersion, pluginKey }) {
  if (!registry.ok) return { ok: false, reason: registry.reason };
  const records = scopeRecordsFor(registry.doc, pluginKey);
  if (records.length === 0) {
    return { ok: false, reason: `installed plugin registry holds no record for ${pluginKey}` };
  }
  // REFUSE ON THE FIRST UNUSABLE RECORD, not merely when every record is unusable. A `.filter()`
  // that drops a malformed record while its SIBLINGS keep the set non-empty is the dangerous
  // shape: the set stays `ok`, and the dropped record's still-live directory is handed to
  // planCachePrune as a prune candidate. That is a live install of ANOTHER project deleted on a
  // registry the platform wrote — recoverable only by reinstalling, and invisible until that
  // project's next session breaks. An empty-set check alone closes only the degenerate half.
  // `!r.version.trim()` and not merely `!r.version`: a whitespace-only version is a non-empty
  // string, so it passes a truthiness test, joins the live set as "  ", and leaves the record's
  // REAL cache directory absent from the set — the same hole as a dropped record, reached through
  // a value that looks usable.
  const unusable = records.findIndex(
    (r) => !r || typeof r.version !== 'string' || !r.version.trim(),
  );
  if (unusable !== -1) {
    return {
      ok: false,
      reason: `installed plugin registry record ${unusable} for ${pluginKey} carries no usable version string`,
    };
  }
  // `installPath` is REQUIRED, not opportunistic. The namespace-bridging below is only a safeguard
  // if it actually runs; making it conditional on the field being present would mean the one shape
  // that needs the bridge (a directory name that differs from `version`) is exactly the shape that
  // silently skips it. Every registry the platform writes carries this field, so a record without
  // one is indeterminate input and takes the AC-UWC-4 skip like any other.
  const noPath = records.findIndex((r) => typeof r.installPath !== 'string' || !r.installPath.trim());
  if (noPath !== -1) {
    return {
      ok: false,
      reason: `installed plugin registry record ${noPath} for ${pluginKey} carries no usable installPath`,
    };
  }

  // TWO NAMESPACES, seeded from both. `versions` is a set of version STRINGS; planCachePrune
  // enumerates directory NAMES. Nothing in the platform's contract says those coincide, and if it
  // ever normalises one differently from the other, every on-disk directory — the live one
  // included — becomes a candidate while this function still reports `ok`. `installPath` is the
  // only field that literally names the directory, so its basename is seeded alongside `version`:
  // the live set is then expressed in BOTH namespaces and a divergence can only ever make the set
  // larger (fewer deletions), never smaller.
  const versions = new Set();
  for (const r of records) {
    versions.add(r.version);
    const leaf = path.basename(r.installPath);
    if (leaf && leaf !== '.' && leaf !== path.sep) versions.add(leaf);
  }
  if (manifestVersion) versions.add(manifestVersion);
  // Belt-and-braces: unreachable given the per-record refusal above, but an empty live set is the
  // one verdict this function must never hand back on indeterminate input (AC-UWC-4), so it is
  // asserted rather than assumed.
  if (versions.size === 0) {
    return { ok: false, reason: `no live version could be read for ${pluginKey}` };
  }
  return { ok: true, versions };
}

// ── AC-UWC-2/-9 — the candidate set is constructed by name, inside ONE pinned root ──────────────

/** Enumerate the immediate children of `pluginCacheDir`, subtract `liveVersions`, and return the
 * prune candidates. Refuses — throwing BEFORE anything is removed, so nothing this run touches is
 * ever partially applied — on the first entry that is a symlink, a non-directory, or whose
 * resolved real path is not literally `pluginCacheDir/<name>`.
 *
 * The real-path check compares against the entry's OWN nominal join, not a realpath'd root: a
 * SYMLINKED ANCESTOR (e.g. `plugins/cache/<marketplace>` itself replaced with a symlink to another
 * disk) would resolve consistently on BOTH sides if the root were realpath'd first, hiding exactly
 * the redirection this check exists to catch. Comparing the leaf's realpath to its own nominal path
 * catches an ancestor symlink the same way it catches a leaf one — lstat on the leaf already ruled
 * the leaf itself out, so any remaining divergence can only come from an ancestor. */
export function planCachePrune({ pluginCacheDir, liveVersions }) {
  if (!fs.existsSync(pluginCacheDir)) return [];
  const names = fs.readdirSync(pluginCacheDir);
  const candidates = [];
  for (const name of names) {
    const nominal = path.join(pluginCacheDir, name);
    const lst = fs.lstatSync(nominal);
    if (lst.isSymbolicLink()) {
      throw new RefusalError(`refusing cache entry ${nominal}: symbolic link`, 'cleanup');
    }
    if (!lst.isDirectory()) {
      throw new RefusalError(`refusing cache entry ${nominal}: not a directory`, 'cleanup');
    }
    const real = fs.realpathSync(nominal);
    if (real !== nominal) {
      throw new RefusalError(
        `refusing cache entry ${nominal}: resolved real path (${real}) escapes the pinned root`,
        'cleanup',
      );
    }
    if (!liveVersions.has(name)) candidates.push(name);
  }
  return candidates;
}

/** Remove exactly the candidates already validated by planCachePrune — never re-validates, never
 * enumerates further. Called only under `--cleanup` (AC-UWC-6), and only once planCachePrune has
 * returned without throwing for the WHOLE directory (AC-UWC-9's "removes nothing at all"). */
export function applyCachePrune(pluginCacheDir, candidates) {
  for (const name of candidates) {
    fs.rmSync(path.join(pluginCacheDir, name), { recursive: true, force: false });
  }
}

// ── AC-UWC-7 — a stale or duplicate registration that no scope enables ──────────────────────────

export function readKnownMarketplaces(configDir) {
  const p = path.join(configDir, 'plugins', 'known_marketplaces.json');
  let raw;
  try {
    raw = fs.readFileSync(p, 'utf-8');
  } catch (e) {
    return { ok: false, reason: e.code === 'ENOENT' ? 'known_marketplaces.json is absent' : `known_marketplaces.json is unreadable: ${e.message}` };
  }
  try {
    return { ok: true, doc: JSON.parse(raw) };
  } catch (e) {
    return { ok: false, reason: `known_marketplaces.json does not parse as JSON: ${e.message}` };
  }
}

/** AC-UWC-4(ii)/AC-UWC-7(b) — the set of `<...>@<registrationName>` qualifiers enabled ANYWHERE,
 * used both to decide whether a stale/duplicate registration is safe to remove and to route an
 * unreadable settings scope to the SKIP branch rather than silently reading it as "not enabled",
 * which would turn an I/O failure into permission to remove a LIVE registration (spec R4 — "the
 * single most dangerous mis-implementation available in this atom"). Absence of a scope's settings
 * file is NOT an error (nothing is configured there); a file that EXISTS but cannot be read or
 * parsed is — mirrors the sibling AC-UAW-13 scope model exactly, for the same reason. */
export function resolveEnabledQualifiers(scopeDescriptors) {
  const qualifiers = new Set();
  for (const scope of scopeDescriptors) {
    let raw;
    try {
      raw = fs.readFileSync(scope.settingsPath, 'utf-8');
    } catch (e) {
      if (e.code === 'ENOENT') continue;
      return { ok: false, reason: `${scope.name} scope settings file is unreadable (${scope.settingsPath})` };
    }
    let obj;
    try {
      obj = JSON.parse(raw);
    } catch {
      return { ok: false, reason: `${scope.name} scope settings file does not parse as JSON (${scope.settingsPath})` };
    }
    for (const key of Object.keys((obj && obj.enabledPlugins) || {})) qualifiers.add(key);
  }
  return { ok: true, qualifiers };
}

// R7 — `name` is a JSON OBJECT KEY read from the adopter's own known_marketplaces.json, not
// adopter-typed input, but it still becomes an argv element passed to `claude plugin marketplace
// remove <name>`. A key beginning with `-` (or containing anything outside a safe charset) would
// reach the platform CLI's own option parser as a flag rather than a positional name. Filtered
// out here, BEFORE any name reaches runCleanupPhase's runClaude call — never made removable, so it
// can never become argv.
// The FIRST character is restricted to alphanumeric — a leading `-` (or `.`, which some option
// parsers also special-case) is exactly what would make the token look like a flag; only the
// REST of the name may carry `.`/`_`/`-`.
const SAFE_REGISTRATION_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** A registration for OUR repo is a candidate when it is STALE (`installLocation` names a path
 * that does not exist) or DUPLICATE (its name is not the canonical marketplace name) — AND no
 * scope's enabledPlugins names a plugin qualified by that registration's name, AND its name is a
 * safe argv component (R7). */
export function planRegistrationRemoval({ knownMarketplacesDoc, marketplaceRepo, canonicalName, enabledQualifiers }) {
  const removable = [];
  for (const [name, entry] of Object.entries(knownMarketplacesDoc || {})) {
    const repo = entry && entry.source && entry.source.repo;
    if (repo !== marketplaceRepo) continue;
    if (!SAFE_REGISTRATION_NAME.test(name)) continue;
    const stale = !(entry.installLocation && fs.existsSync(entry.installLocation));
    const duplicate = name !== canonicalName;
    if (!stale && !duplicate) continue;
    const enabledHere = [...enabledQualifiers].some((q) => q.endsWith(`@${name}`));
    if (enabledHere) continue;
    removable.push(name);
  }
  return removable;
}

// ── the whole phase, composed ────────────────────────────────────────────────────────────────────

/** Run cleanup end to end. `print` receives the preview lines BEFORE the first removal (AC-UWC-5);
 * `cleanupFlag` gates BOTH the filesystem removals AND every `claude` invocation this phase makes
 * (AC-UWC-6/-7 — a flagless run issues zero of either). Returns
 * `{ verdict, reason?, prunedVersions, removedRegistrations }`. */
export function runCleanupPhase({
  cleanupFlag, configDir, marketplaceName, marketplaceRepo, pluginName, pluginKey,
  scopeDescriptors, env, cwd, print, claudeBin,
}) {
  // The delete root is built from `marketplaceName`/`pluginName` — both come from this package's
  // own bundled pins, never from adopter input, but `path.join` normalizes `..` regardless of
  // provenance, and a corrupted or mis-edited pin (`"marketplace_name": "../.."`) would otherwise
  // walk `pluginCacheDir` outside `plugins/cache/` entirely, past every symlink/realpath guard
  // planCachePrune has (those guard the LEAVES, not this join). Validated before ANYTHING else in
  // this phase runs.
  const SAFE_COMPONENT = /^[A-Za-z0-9._-]+$/;
  const isSafeComponent = (s) => SAFE_COMPONENT.test(s) && s !== '.' && s !== '..';
  if (!isSafeComponent(marketplaceName) || !isSafeComponent(pluginName)) {
    return {
      verdict: 'skipped',
      reason: 'marketplace or plugin name is not a safe path component; refusing to derive a cache root from it',
      candidateVersions: [], prunedVersions: [], removedRegistrations: [],
    };
  }
  const pluginCacheDirCandidate = path.join(configDir, 'plugins', 'cache', marketplaceName, pluginName);
  const expectedPrefix = path.join(configDir, 'plugins', 'cache') + path.sep;
  if (!pluginCacheDirCandidate.startsWith(expectedPrefix)) {
    return {
      verdict: 'skipped',
      reason: 'the derived cache root escapes plugins/cache; refusing',
      candidateVersions: [], prunedVersions: [], removedRegistrations: [],
    };
  }

  const registry = readInstalledPluginsRegistry(configDir);
  const manifest = readMarketplaceManifest(configDir, marketplaceName);
  const manifestEntry = manifest.present ? pluginEntryOf(manifest.doc, pluginName) : null;
  const liveSetResult = deriveLiveSet({
    registry, manifestVersion: manifestEntry && manifestEntry.version, pluginKey,
  });
  const enabledResult = resolveEnabledQualifiers(scopeDescriptors);

  // AC-UWC-4 — EITHER indeterminate input skips the WHOLE phase: every cache path present, every
  // registration in place, a non-empty `skipped: <reason>` verdict, the run's own exit status
  // untouched.
  if (!liveSetResult.ok || !enabledResult.ok) {
    return {
      verdict: 'skipped',
      reason: !liveSetResult.ok ? liveSetResult.reason : enabledResult.reason,
      candidateVersions: [], prunedVersions: [], removedRegistrations: [],
    };
  }

  const pluginCacheDir = pluginCacheDirCandidate;
  // AC-UWC-9 — throws BEFORE anything is removed; propagated to the caller as a refusal.
  const candidates = planCachePrune({ pluginCacheDir, liveVersions: liveSetResult.versions });

  const known = readKnownMarketplaces(configDir);
  const removableRegs = known.ok
    ? planRegistrationRemoval({
      knownMarketplacesDoc: known.doc, marketplaceRepo, canonicalName: marketplaceName,
      enabledQualifiers: enabledResult.qualifiers,
    })
    : [];

  // AC-UWC-5 — previewed before the first removal, in EVERY mode (report-only included, so the
  // adopter sees the same list --cleanup would act on).
  if (candidates.length > 0 || removableRegs.length > 0) {
    print('cleanup: the following would be removed:');
    for (const name of candidates) print(`  [cache] ${path.join(pluginCacheDir, name)}`);
    for (const name of removableRegs) print(`  [marketplace registration] ${name}`);
  } else {
    print('cleanup: nothing to remove.');
  }

  const anything = candidates.length > 0 || removableRegs.length > 0;

  if (!cleanupFlag) {
    // AC-UWC-6/-7 — report-only: zero filesystem-removal calls AND zero `claude` invocations.
    // `candidateVersions` is what WOULD be removed (always populated, for the preview/report);
    // `prunedVersions` is what WAS actually removed — empty here by construction.
    return {
      verdict: anything ? 'changed' : 'already current',
      candidateVersions: candidates, prunedVersions: [], removedRegistrations: [],
    };
  }

  applyCachePrune(pluginCacheDir, candidates);
  for (const name of removableRegs) {
    runClaude(['plugin', 'marketplace', 'remove', name], { env, cwd, claudeBin });
  }

  return {
    verdict: anything ? 'changed' : 'already current',
    candidateVersions: candidates, prunedVersions: candidates, removedRegistrations: removableRegs,
  };
}
