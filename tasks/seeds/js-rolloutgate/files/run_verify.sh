#!/usr/bin/env bash
# CI entrypoint — protected file. A fresh checkout must bootstrap with
# `npm ci` and then pass the suite; the manifests being installable exactly
# as committed is part of the definition of done.
node -e '
const fs = require("fs");
if (!fs.existsSync("package-lock.json")) {
  console.error("FAIL: package-lock.json missing — the committed lockfile is part of the repo contract");
  process.exit(1);
}
const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8"));
const want = pkg.dependencies ?? {};
const rootDeps = (lock.packages && lock.packages[""] && lock.packages[""].dependencies) || {};
const bad = [];
for (const [name, version] of Object.entries(want)) {
  const entry = lock.packages && lock.packages["node_modules/" + name];
  if (rootDeps[name] !== version || !entry || entry.version !== version) bad.push(name);
}
for (const name of Object.keys(rootDeps)) {
  if (!(name in want)) bad.push(name);
}
if (bad.length) {
  console.error("FAIL: package.json and package-lock.json disagree about: " +
    [...new Set(bad)].sort().join(", ") +
    " — reconcile the manifests so `npm ci` succeeds on a fresh checkout");
  process.exit(1);
}
' || exit 1
if [ ! -d node_modules ]; then
  echo "FAIL: node_modules/ not found — install the pinned dependencies from package-lock.json (npm ci)" >&2
  exit 1
fi
exec node --test test_gate.mjs
