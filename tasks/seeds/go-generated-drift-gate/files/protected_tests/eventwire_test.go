package protected_tests_test

import (
	"encoding/json"
	"reflect"
	"testing"

	"example.com/releasepipe/internal/eventwire"
)

func TestReleasedEventIncludesSchemaFields(t *testing.T) {
	event := eventwire.BuildReleased{
		BuildID:        "build-204",
		ArtifactDigest: "sha256:cafe",
		ReleaseChannel: "stable",
	}
	encoded, err := eventwire.Marshal(event)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}

	var envelope struct {
		Code    string         `json:"code"`
		Payload map[string]any `json:"payload"`
	}
	if err := json.Unmarshal(encoded, &envelope); err != nil {
		t.Fatalf("decode envelope: %v", err)
	}
	if envelope.Code != "build.released" {
		t.Fatalf("code = %q, want build.released", envelope.Code)
	}
	if got := envelope.Payload["release_channel"]; got != "stable" {
		t.Fatalf("release_channel = %#v, want stable", got)
	}
}

func TestEventCodesAreStable(t *testing.T) {
	want := []string{"build.queued", "build.released"}
	if !reflect.DeepEqual(eventwire.EventCodes, want) {
		t.Fatalf("EventCodes = %#v, want %#v", eventwire.EventCodes, want)
	}
}
