# React Router data-router migration notes (protected local copy)

The application is moving from the history/Prompt generation to the current
data-router contract represented by `router/router_v7.ts`.

- Route-object `redirect`, `component`, and `render` properties are removed.
  Redirect routes return `redirect(location, { replace })` from a loader.
- Imperative transitions use `router.navigate(location, { replace })`; a
  mutable `history.push`, `history.replace`, or `history.block` object is not
  exposed.
- Unsaved-work blocking is registered with a named blocker predicate.  A
  blocked transition remains pending until its blocker calls `proceed()` or
  `reset()`.  Registration returns a cleanup callback.
- Nested route objects, layout match order, dynamic parameters, index routes,
  and replace redirects retain their existing URL behavior.

