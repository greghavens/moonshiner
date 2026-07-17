// test_hooks_runtime.ts — spec suite for the miniature hooks runtime.
// Protected test file: do not modify. Run with: node --test test_hooks_runtime.ts
//
// Plain TypeScript under node's type stripping: annotations only, no JSX,
// no React import anywhere. The module under test is ./hooks_runtime.ts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRoot, useState, useReducer, useMemo } from "./hooks_runtime.ts";

// ------------------------------------------------------------- useState

test("useState: first render sees the initial value", () => {
  const root = createRoot((props: { label: string }) => {
    const [count] = useState(0);
    return `${props.label}:${count}`;
  });
  assert.equal(root.render({ label: "a" }), "a:0");
});

test("useState: setter called between renders updates state; state survives prop changes", () => {
  let set: any = null;
  const root = createRoot((props: { label: string }) => {
    const [count, setCount] = useState(0);
    set = setCount;
    return `${props.label}:${count}`;
  });
  assert.equal(root.render({ label: "a" }), "a:0");
  set(5);
  assert.equal(root.render({ label: "b" }), "b:5");
  assert.equal(root.render({ label: "c" }), "c:5");
});

test("useState: lazy initializer runs exactly once, on mount only", () => {
  let calls = 0;
  const root = createRoot(() => {
    const [v] = useState(() => {
      calls += 1;
      return 7;
    });
    return v;
  });
  assert.equal(root.render({}), 7);
  root.render({});
  root.render({});
  assert.equal(calls, 1);
});

test("useState: queued updater functions each see the latest pending state", () => {
  let set: any = null;
  const root = createRoot(() => {
    const [count, setCount] = useState(0);
    set = setCount;
    return count;
  });
  root.render({});
  set((c: number) => c + 1);
  set((c: number) => c + 1);
  set((c: number) => c + 1);
  assert.equal(root.render({}), 3);
});

test("useState: direct values and updaters drain in FIFO order", () => {
  let set: any = null;
  const root = createRoot(() => {
    const [count, setCount] = useState(0);
    set = setCount;
    return count;
  });
  root.render({});
  set(10);
  set((c: number) => c + 1);
  assert.equal(root.render({}), 11);
  set((c: number) => c * 2);
  set(1);
  assert.equal(root.render({}), 1);
});

test("useState: setter identity is stable across renders", () => {
  const seen: unknown[] = [];
  const root = createRoot(() => {
    const [count, setCount] = useState(0);
    seen.push(setCount);
    return count;
  });
  root.render({});
  root.render({});
  assert.equal(seen.length, 2);
  assert.equal(seen[0], seen[1]);
  (seen[0] as (n: number) => void)(9);
  assert.equal(root.render({}), 9);
});

// ------------------------------------------------------- rerender loop

test("render-phase updates re-invoke the component until it settles", () => {
  let invocations = 0;
  const root = createRoot(() => {
    invocations += 1;
    const [count, setCount] = useState(0);
    if (count < 3) setCount(count + 1);
    return `count=${count}`;
  });
  assert.equal(root.render({}), "count=3");
  assert.equal(invocations, 4);
});

test("an unconditional render-phase update throws after exactly 25 invocations", () => {
  let invocations = 0;
  const root = createRoot(() => {
    invocations += 1;
    const [count, setCount] = useState(0);
    setCount(count + 1);
    return count;
  });
  assert.throws(() => root.render({}), { message: "too many re-renders (limit 25)" });
  assert.equal(invocations, 25);
});

// ----------------------------------------------------------- useReducer

test("useReducer: dispatches queued between renders apply the reducer", () => {
  let dispatch: any = null;
  const reducer = (s: number, a: string) => (a === "inc" ? s + 1 : a === "double" ? s * 2 : s);
  const root = createRoot(() => {
    const [value, d] = useReducer(reducer, 0);
    dispatch = d;
    return value;
  });
  assert.equal(root.render({}), 0);
  dispatch("inc");
  dispatch("inc");
  assert.equal(root.render({}), 2);
});

test("useReducer: actions drain in FIFO order", () => {
  let dispatch: any = null;
  const reducer = (s: number, a: string) => (a === "inc" ? s + 1 : a === "double" ? s * 2 : s);
  const root = createRoot(() => {
    const [value, d] = useReducer(reducer, 1);
    dispatch = d;
    return value;
  });
  root.render({});
  dispatch("inc");
  dispatch("double");
  assert.equal(root.render({}), 4); // (1 + 1) * 2, not 1 * 2 + 1
});

test("useReducer: dispatch identity is stable and the initial state is not invoked", () => {
  const seen: unknown[] = [];
  const initial = () => 99; // a function used AS state, not a lazy initializer
  const root = createRoot(() => {
    const [value, d] = useReducer((s: unknown, _a: unknown) => s, initial);
    seen.push(d);
    return value;
  });
  assert.equal(root.render({}), initial);
  root.render({});
  assert.equal(seen[0], seen[1]);
});

// -------------------------------------------------------------- useMemo

test("useMemo: caches the value (same reference) while deps are unchanged", () => {
  let computes = 0;
  const root = createRoot((props: { dep: number }) => {
    const value = useMemo(() => {
      computes += 1;
      return { dep: props.dep };
    }, [props.dep]);
    return value;
  });
  const first = root.render({ dep: 1 });
  const second = root.render({ dep: 1 });
  assert.equal(computes, 1);
  assert.equal(first, second);
});

test("useMemo: deps compare with Object.is (NaN equals NaN)", () => {
  let computes = 0;
  const root = createRoot((props: { dep: number }) => {
    useMemo(() => {
      computes += 1;
      return props.dep;
    }, [props.dep]);
    return computes;
  });
  root.render({ dep: 1 });
  root.render({ dep: 1 });
  assert.equal(computes, 1);
  root.render({ dep: 2 });
  assert.equal(computes, 2);
  root.render({ dep: NaN });
  assert.equal(computes, 3);
  root.render({ dep: NaN });
  assert.equal(computes, 3);
});

test("useMemo: a deps list of different length recomputes", () => {
  let computes = 0;
  const root = createRoot((props: { deps: number[] }) => {
    useMemo(() => {
      computes += 1;
      return null;
    }, props.deps);
    return computes;
  });
  root.render({ deps: [1] });
  root.render({ deps: [1, 2] });
  assert.equal(computes, 2);
  root.render({ deps: [1, 2] });
  assert.equal(computes, 2);
});

// ------------------------------------------------------ call-order rules

test("hooks outside a render throw", () => {
  assert.throws(() => useState(0), {
    message: "hooks may only be called while a component is rendering",
  });
});

test("rendering fewer hooks than the previous render throws", () => {
  const root = createRoot((props: { flag: boolean }) => {
    const [a] = useState(1);
    if (props.flag) {
      const [b] = useState(2);
      return a + b;
    }
    return a;
  });
  assert.equal(root.render({ flag: true }), 3);
  assert.throws(() => root.render({ flag: false }), {
    message: "hook count changed: expected 2, got 1",
  });
});

test("rendering more hooks than the previous render throws", () => {
  const root = createRoot((props: { flag: boolean }) => {
    const [a] = useState(1);
    if (props.flag) {
      const [b] = useState(2);
      return a + b;
    }
    return a;
  });
  assert.equal(root.render({ flag: false }), 1);
  assert.throws(() => root.render({ flag: true }), {
    message: "hook count changed: expected 1, got 2",
  });
});

test("changing which hook occupies a slot throws", () => {
  const root = createRoot((props: { flag: boolean }) => {
    if (props.flag) {
      const [v] = useState(0);
      return v;
    }
    const [v] = useReducer((s: number) => s, 0);
    return v;
  });
  root.render({ flag: true });
  assert.throws(() => root.render({ flag: false }), {
    message: "hook order changed at slot 0: expected useState, got useReducer",
  });
});

// ------------------------------------------------------- roots & safety

test("two roots of the same component keep independent state", () => {
  let set: any = null;
  const component = () => {
    const [count, setCount] = useState(0);
    set = setCount;
    return count;
  };
  const a = createRoot(component);
  const b = createRoot(component);
  a.render({});
  set(41); // captured from root a's render
  assert.equal(a.render({}), 41);
  assert.equal(b.render({}), 0);
  assert.equal(a.render({}), 41);
});

test("a throwing render releases the runtime for other roots", () => {
  const bad = createRoot((props: { flag: boolean }) => {
    const [a] = useState(1);
    if (props.flag) useState(2);
    return a;
  });
  bad.render({ flag: true });
  assert.throws(() => bad.render({ flag: false }));
  const good = createRoot(() => {
    const [v] = useState("fine");
    return v;
  });
  assert.equal(good.render({}), "fine");
});

test("render is not reentrant", () => {
  let self: any = null;
  self = createRoot(() => {
    const [v] = useState(0);
    self.render({});
    return v;
  });
  assert.throws(() => self.render({}), { message: "render is not reentrant" });
});
