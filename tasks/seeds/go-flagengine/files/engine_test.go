package flagengine

import (
	"errors"
	"testing"
)

func newEngineWithEnvs(t *testing.T) *Engine {
	t.Helper()
	e := New()
	if err := e.AddEnv("default", ""); err != nil {
		t.Fatalf("AddEnv(default): %v", err)
	}
	if err := e.AddEnv("staging", "default"); err != nil {
		t.Fatalf("AddEnv(staging): %v", err)
	}
	if err := e.AddEnv("prod", "staging"); err != nil {
		t.Fatalf("AddEnv(prod): %v", err)
	}
	return e
}

func mustSet(t *testing.T, e *Engine, env string, f Flag) {
	t.Helper()
	if err := e.SetFlag(env, f); err != nil {
		t.Fatalf("SetFlag(%s, %s): %v", env, f.Key, err)
	}
}

func mustEval(t *testing.T, e *Engine, env, key string, u User) Result {
	t.Helper()
	r, err := e.Eval(env, key, u)
	if err != nil {
		t.Fatalf("Eval(%s, %s): %v", env, key, err)
	}
	return r
}

func TestEnvValidation(t *testing.T) {
	e := New()
	if err := e.AddEnv("", ""); err == nil {
		t.Fatal("AddEnv with empty name must fail")
	}
	if err := e.AddEnv("prod", "default"); err == nil {
		t.Fatal("AddEnv with a parent that does not exist yet must fail")
	}
	if err := e.AddEnv("default", ""); err != nil {
		t.Fatalf("AddEnv(default): %v", err)
	}
	if err := e.AddEnv("default", ""); err == nil {
		t.Fatal("duplicate environment must fail")
	}
	if err := e.SetFlag("nope", Flag{Key: "x", On: true}); err == nil {
		t.Fatal("SetFlag on an unknown environment must fail")
	}
	if _, err := e.Eval("nope", "x", User{ID: "u1"}); err == nil {
		t.Fatal("Eval on an unknown environment must fail")
	}
}

func TestFlagValidation(t *testing.T) {
	e := newEngineWithEnvs(t)
	if err := e.SetFlag("default", Flag{Key: "", On: true}); err == nil {
		t.Fatal("empty flag key must be rejected")
	}
	if err := e.SetFlag("default", Flag{Key: "x", On: true, RolloutBPS: -1}); err == nil {
		t.Fatal("RolloutBPS below 0 must be rejected")
	}
	if err := e.SetFlag("default", Flag{Key: "x", On: true, RolloutBPS: 10001}); err == nil {
		t.Fatal("RolloutBPS above 10000 must be rejected")
	}
	bad := Flag{Key: "x", On: true, Rules: []Rule{{
		Clauses: []Clause{{Attr: "plan", Op: "matches", Values: []string{"pro"}}},
		Value:   true,
	}}}
	if err := e.SetFlag("default", bad); err == nil {
		t.Fatal("unknown clause op must be rejected")
	}
	noAttr := Flag{Key: "x", On: true, Rules: []Rule{{
		Clauses: []Clause{{Attr: "", Op: "in", Values: []string{"pro"}}},
		Value:   true,
	}}}
	if err := e.SetFlag("default", noAttr); err == nil {
		t.Fatal("clause with empty attribute name must be rejected")
	}
	if _, err := e.Eval("default", "never-set", User{ID: "u1"}); !errors.Is(err, ErrFlagNotFound) {
		t.Fatalf("Eval of a missing flag: got %v, want ErrFlagNotFound", err)
	}
}

func TestOffFlagShortCircuits(t *testing.T) {
	e := newEngineWithEnvs(t)
	mustSet(t, e, "default", Flag{
		Key: "checkout-v2",
		On:  false,
		Rules: []Rule{{
			Clauses: []Clause{{Attr: "plan", Op: "in", Values: []string{"pro"}}},
			Value:   true,
		}},
		RolloutBPS: 10000,
	})
	r := mustEval(t, e, "default", "checkout-v2", User{ID: "u1", Attrs: map[string]string{"plan": "pro"}})
	if r.Value != false || r.Reason != "off" {
		t.Fatalf("off flag: got %+v, want Value=false Reason=off even when rules and rollout would match", r)
	}
}

func TestRulesFirstMatchWinsWithIndexReason(t *testing.T) {
	e := newEngineWithEnvs(t)
	mustSet(t, e, "default", Flag{
		Key: "beta-panel",
		On:  true,
		Rules: []Rule{
			{Clauses: []Clause{{Attr: "banned", Op: "in", Values: []string{"yes"}}}, Value: false},
			{Clauses: []Clause{{Attr: "plan", Op: "in", Values: []string{"pro", "team"}}}, Value: true},
			{Clauses: []Clause{{Attr: "plan", Op: "in", Values: []string{"team"}}}, Value: false},
		},
		RolloutBPS: 0,
	})

	r := mustEval(t, e, "default", "beta-panel", User{ID: "u1", Attrs: map[string]string{"plan": "team"}})
	if r.Value != true || r.Reason != "rule:1" {
		t.Fatalf("first matching rule must win: got %+v, want Value=true Reason=rule:1", r)
	}
	r = mustEval(t, e, "default", "beta-panel", User{ID: "u2", Attrs: map[string]string{"plan": "pro", "banned": "yes"}})
	if r.Value != false || r.Reason != "rule:0" {
		t.Fatalf("earlier rule must shadow later ones: got %+v, want Value=false Reason=rule:0", r)
	}
}

func TestClauseSemantics(t *testing.T) {
	e := newEngineWithEnvs(t)
	mustSet(t, e, "default", Flag{
		Key: "audit-mode",
		On:  true,
		Rules: []Rule{{
			Clauses: []Clause{
				{Attr: "region", Op: "in", Values: []string{"eu", "uk"}},
				{Attr: "role", Op: "not_in", Values: []string{"intern"}},
			},
			Value: true,
		}},
		RolloutBPS: 0,
	})

	// All clauses must match (AND).
	r := mustEval(t, e, "default", "audit-mode", User{ID: "a", Attrs: map[string]string{"region": "eu", "role": "admin"}})
	if r.Value != true || r.Reason != "rule:0" {
		t.Fatalf("both clauses hold: got %+v, want rule:0/true", r)
	}
	r = mustEval(t, e, "default", "audit-mode", User{ID: "b", Attrs: map[string]string{"region": "eu", "role": "intern"}})
	if r.Value != false || r.Reason != "fallthrough" {
		t.Fatalf("not_in clause fails for excluded value: got %+v, want fallthrough/false", r)
	}
	r = mustEval(t, e, "default", "audit-mode", User{ID: "c", Attrs: map[string]string{"region": "us", "role": "admin"}})
	if r.Value != false || r.Reason != "fallthrough" {
		t.Fatalf("in clause fails for other region: got %+v, want fallthrough/false", r)
	}
	// A missing attribute matches NEITHER op — a user with no role attr is not "not_in intern".
	r = mustEval(t, e, "default", "audit-mode", User{ID: "d", Attrs: map[string]string{"region": "eu"}})
	if r.Value != false || r.Reason != "fallthrough" {
		t.Fatalf("missing attribute must not satisfy not_in: got %+v, want fallthrough/false", r)
	}
	// nil Attrs map must be handled like an empty one.
	r = mustEval(t, e, "default", "audit-mode", User{ID: "e"})
	if r.Value != false || r.Reason != "fallthrough" {
		t.Fatalf("nil Attrs: got %+v, want fallthrough/false", r)
	}
}

func TestEnvironmentLayering(t *testing.T) {
	e := newEngineWithEnvs(t)
	mustSet(t, e, "default", Flag{Key: "new-nav", On: true, RolloutBPS: 10000})
	mustSet(t, e, "staging", Flag{Key: "new-nav", On: false})

	// prod has no definition: walks to staging first, not default.
	r := mustEval(t, e, "prod", "new-nav", User{ID: "u1"})
	if r.Value != false || r.Reason != "off" || r.Env != "staging" {
		t.Fatalf("prod must inherit the NEAREST ancestor definition: got %+v, want off/false from env=staging", r)
	}
	// staging's own definition also wins over default's for staging itself.
	r = mustEval(t, e, "staging", "new-nav", User{ID: "u1"})
	if r.Value != false || r.Env != "staging" {
		t.Fatalf("staging eval: got %+v, want its own definition", r)
	}
	// default still serves its own copy.
	r = mustEval(t, e, "default", "new-nav", User{ID: "u1"})
	if r.Value != true || r.Reason != "rollout" || r.Env != "default" {
		t.Fatalf("default eval: got %+v, want rollout/true from env=default", r)
	}

	// A flag defined only at the root is visible from the leaf, and reports where it came from.
	mustSet(t, e, "default", Flag{Key: "root-only", On: true, RolloutBPS: 10000})
	r = mustEval(t, e, "prod", "root-only", User{ID: "u1"})
	if r.Value != true || r.Env != "default" {
		t.Fatalf("prod eval of root-only: got %+v, want Value=true Env=default", r)
	}

	// Redefining replaces in place.
	mustSet(t, e, "staging", Flag{Key: "new-nav", On: true, RolloutBPS: 10000})
	r = mustEval(t, e, "prod", "new-nav", User{ID: "u1"})
	if r.Value != true || r.Reason != "rollout" {
		t.Fatalf("after replacing staging definition: got %+v, want rollout/true", r)
	}
}
