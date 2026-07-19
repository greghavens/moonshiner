# ESLint 9 flat-configuration migration contract (protected local copy)

The checked-in flat-config evaluator models the ESLint 9 behavior needed by
this monorepo without installing packages.

- ESLint reads the ordered array exported by `eslint.config.ts`; legacy
  `.eslintrc` cascade and `ignorePatterns` are not consulted.
- An ignore is global only when it appears in a configuration object that has
  no `files` selector or rule/language configuration. An `ignores` key beside
  `files` excludes matches only from that one object.
- Type-aware TypeScript linting selects the owning package's tsconfig. A root
  project or one package's project cannot analyze another package's files.
- Later matching objects override earlier rules. Test files keep type-aware
  linting but disable the documented test-only `no-explicit-any` and
  `no-console` restrictions.
- Generated and distribution trees are ignored globally. They are not linted
  with a partial or default configuration.
- CI uses `maxWarnings: 0`: an error or even one warning exits nonzero.
