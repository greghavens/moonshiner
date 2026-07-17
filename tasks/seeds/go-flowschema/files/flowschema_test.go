// Contract tests for the workflow schema loader — protected file.
//
// Parse([]byte) reads a workflow document into typed structs, normalizing
// as it goes; Dump(*Workflow) re-emits the canonical form. The exact bytes
// and error texts asserted here are relied on by our document tooling.
package flowschema

import (
	"strings"
	"testing"
	"time"
)

const rich = `document:
  dsl: "1.0"
  namespace: ops
  name: intake
  version: 1.2.0
timeout:
  after: PT600S
do:
  - fetch:
      call: http
      with:
        method: GET
        endpoint: /orders
      if: ${ .ready }
      timeout:
        after: PT90S
  - route:
      switch:
        - when: ${ .rush }
          then: fetch
        - then: continue
  - sweep:
      for:
        each: line
        in: ${ .lines }
        at: idx
        do:
          - hold:
              wait: PT3661S
  - stamp:
      set:
        zone: west
        alpha: first
      then: end
`

func mustParse(t *testing.T, src string) *Workflow {
	t.Helper()
	wf, err := Parse([]byte(src))
	if err != nil {
		t.Fatalf("Parse failed: %v", err)
	}
	return wf
}

func wantErr(t *testing.T, src, want string) {
	t.Helper()
	_, err := Parse([]byte(src))
	if err == nil {
		t.Fatalf("Parse succeeded, want error containing %q", want)
	}
	if !strings.Contains(err.Error(), want) {
		t.Fatalf("error = %q, want it to contain %q", err.Error(), want)
	}
}

// ------------------------------------------------------------------ parsing

func TestParseDocumentHeader(t *testing.T) {
	wf := mustParse(t, rich)
	d := wf.Document
	if d.DSL != "1.0" || d.Namespace != "ops" || d.Name != "intake" || d.Version != "1.2.0" {
		t.Fatalf("document = %+v", d)
	}
	if wf.Timeout == nil || wf.Timeout.After != 10*time.Minute {
		t.Fatalf("timeout = %+v", wf.Timeout)
	}
	if len(wf.Do) != 4 {
		t.Fatalf("len(do) = %d", len(wf.Do))
	}
}

func TestParseCallTask(t *testing.T) {
	wf := mustParse(t, rich)
	fetch := wf.Do[0]
	if fetch.Name != "fetch" {
		t.Fatalf("name = %q", fetch.Name)
	}
	task := fetch.Task
	if task.Kind != "call" || task.Call == nil || task.Call.Fn != "http" {
		t.Fatalf("call task = %+v", task)
	}
	if task.Call.With["endpoint"] != "/orders" || task.Call.With["method"] != "GET" {
		t.Fatalf("with = %+v", task.Call.With)
	}
	if task.If != "${ .ready }" {
		t.Fatalf("if = %q", task.If)
	}
	if task.Timeout == nil || task.Timeout.After != 90*time.Second {
		t.Fatalf("timeout = %+v", task.Timeout)
	}
}

func TestParseSwitchTask(t *testing.T) {
	route := mustParse(t, rich).Do[1].Task
	if route.Kind != "switch" || len(route.Switch) != 2 {
		t.Fatalf("switch task = %+v", route)
	}
	if route.Switch[0].When != "${ .rush }" || route.Switch[0].Then != "fetch" {
		t.Fatalf("case 0 = %+v", route.Switch[0])
	}
	if route.Switch[1].When != "" || route.Switch[1].Then != "continue" {
		t.Fatalf("case 1 = %+v", route.Switch[1])
	}
}

func TestParseForTask(t *testing.T) {
	sweep := mustParse(t, rich).Do[2].Task
	if sweep.Kind != "for" || sweep.For == nil {
		t.Fatalf("for task = %+v", sweep)
	}
	if sweep.For.Each != "line" || sweep.For.In != "${ .lines }" || sweep.For.At != "idx" {
		t.Fatalf("for spec = %+v", sweep.For)
	}
	if len(sweep.For.Do) != 1 || sweep.For.Do[0].Name != "hold" {
		t.Fatalf("for body = %+v", sweep.For.Do)
	}
	hold := sweep.For.Do[0].Task
	if hold.Kind != "wait" || hold.Wait != 3661*time.Second {
		t.Fatalf("hold = %+v", hold)
	}
}

func TestParseSetTaskAndExplicitThen(t *testing.T) {
	stamp := mustParse(t, rich).Do[3].Task
	if stamp.Kind != "set" || stamp.Set["zone"] != "west" || stamp.Set["alpha"] != "first" {
		t.Fatalf("set task = %+v", stamp)
	}
	if stamp.Then != "end" {
		t.Fatalf("then = %q", stamp.Then)
	}
}

// ------------------------------------------------------------ normalization

func TestThenDefaultsToContinueEverywhere(t *testing.T) {
	wf := mustParse(t, rich)
	if got := wf.Do[0].Task.Then; got != "continue" {
		t.Fatalf("task then default = %q", got)
	}
	if got := wf.Do[2].Task.For.Do[0].Task.Then; got != "continue" {
		t.Fatalf("body task then default = %q", got)
	}
	src := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - route:
      switch:
        - when: ${ .a }
          then: route
        - when: ${ .b }
`
	wf = mustParse(t, src)
	if got := wf.Do[0].Task.Switch[1].Then; got != "continue" {
		t.Fatalf("case then default = %q", got)
	}
}

func TestDurationNormalization(t *testing.T) {
	cases := map[string]string{
		"PT45S":    "PT45S",
		"PT90S":    "PT1M30S",
		"PT61S":    "PT1M1S",
		"PT3600S":  "PT1H",
		"PT3661S":  "PT1H1M1S",
		"PT2H":     "PT2H",
		"PT1H30M":  "PT1H30M",
		"PT120M":   "PT2H",
		"PT0H5M0S": "PT5M",
	}
	for in, want := range cases {
		src := "document:\n  dsl: \"1.0\"\n  namespace: n\n  name: x\ndo:\n  - hold:\n      wait: " + in + "\n"
		wf := mustParse(t, src)
		out := Dump(wf)
		if !strings.Contains(out, "wait: "+want+"\n") {
			t.Errorf("%s: dump does not contain %q:\n%s", in, "wait: "+want, out)
		}
	}
}

// ------------------------------------------------------------------ dumping

func TestDumpCanonicalizesAMessyDocument(t *testing.T) {
	messy := `do:
  - fetch:
      then: continue
      with:
        method: GET
        endpoint: /orders
      call: http
      timeout:
        after: PT90S
  - route:
      switch:
        - when: ${ .rush }
          then: fetch
        - then: continue
  - sweep:
      for:
        in: ${ .lines }
        each: line
        do:
          - hold:
              wait: PT3661S
  - stamp:
      set:
        zone: west
        alpha: first
document:
  name: intake
  dsl: "1.0"
  namespace: ops
timeout:
  after: PT600S
`
	want := `document:
  dsl: "1.0"
  namespace: ops
  name: intake
timeout:
  after: PT10M
do:
  - fetch:
      call: http
      with:
        endpoint: /orders
        method: GET
      timeout:
        after: PT1M30S
      then: continue
  - route:
      switch:
        - when: ${ .rush }
          then: fetch
        - then: continue
      then: continue
  - sweep:
      for:
        each: line
        in: ${ .lines }
        do:
          - hold:
              wait: PT1H1M1S
              then: continue
      then: continue
  - stamp:
      set:
        alpha: first
        zone: west
      then: continue
`
	got := Dump(mustParse(t, messy))
	if got != want {
		t.Fatalf("dump mismatch\n--- got ---\n%s\n--- want ---\n%s", got, want)
	}
}

func TestDumpOmitsAbsentOptionalParts(t *testing.T) {
	src := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - hold:
      wait: PT5S
`
	want := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - hold:
      wait: PT5S
      then: continue
`
	if got := Dump(mustParse(t, src)); got != want {
		t.Fatalf("dump mismatch\n--- got ---\n%s\n--- want ---\n%s", got, want)
	}
}

func TestDumpIsAFixpoint(t *testing.T) {
	for _, src := range []string{rich} {
		first := Dump(mustParse(t, src))
		second := Dump(mustParse(t, first))
		if first != second {
			t.Fatalf("dump is not stable\n--- first ---\n%s\n--- second ---\n%s", first, second)
		}
	}
}

// ------------------------------------------------------------------- errors

func TestEntryMustHaveExactlyOneKey(t *testing.T) {
	src := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - a:
      call: http
    b:
      call: http
`
	wantErr(t, src, `line 6: task entry must be a mapping with exactly one key`)
}

func TestTaskMustDeclareExactlyOneType(t *testing.T) {
	none := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - a:
      then: end
`
	wantErr(t, none, `line 7: task must declare exactly one task type (found none)`)
	two := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - a:
      set:
        k: v
      call: http
`
	wantErr(t, two, `line 7: task must declare exactly one task type (found call, set)`)
}

func TestWithIsOnlyForCallTasks(t *testing.T) {
	src := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - a:
      wait: PT5S
      with:
        k: v
`
	wantErr(t, src, `line 8: "with" is only valid on call tasks`)
}

func TestBadDurations(t *testing.T) {
	for _, bad := range []string{"P1D", "PT", "10s", "PT0S", "PT-5S"} {
		src := "document:\n  dsl: \"1.0\"\n  namespace: n\n  name: x\ndo:\n  - hold:\n      wait: " + bad + "\n"
		wantErr(t, src, `line 7: invalid ISO-8601 duration "`+bad+`"`)
	}
}

func TestDSLMustBeTheQuotedString(t *testing.T) {
	float := `document:
  dsl: 1.0
  namespace: n
  name: x
do:
  - a:
      wait: PT1S
`
	wantErr(t, float, `line 2: document.dsl must be the string "1.0"`)
	wrong := `document:
  dsl: "2.0"
  namespace: n
  name: x
do:
  - a:
      wait: PT1S
`
	wantErr(t, wrong, `line 2: document.dsl must be the string "1.0"`)
	missing := `do:
  - a:
      wait: PT1S
`
	wantErr(t, missing, `document.dsl must be the string "1.0"`)
}

func TestUnknownFields(t *testing.T) {
	task := `document:
  dsl: "1.0"
  namespace: n
  name: x
do:
  - a:
      call: http
      retries: 3
`
	wantErr(t, task, `line 8: unknown task field "retries"`)
	doc := `document:
  dsl: "1.0"
  namespace: n
  name: x
  owner: kim
do:
  - a:
      wait: PT1S
`
	wantErr(t, doc, `line 5: unknown document field "owner"`)
}

func TestDoMustBeNonEmpty(t *testing.T) {
	wantErr(t, "document:\n  dsl: \"1.0\"\n  namespace: n\n  name: x\ndo: []\n", "do must be a non-empty list")
	wantErr(t, "document:\n  dsl: \"1.0\"\n  namespace: n\n  name: x\n", "do must be a non-empty list")
}
