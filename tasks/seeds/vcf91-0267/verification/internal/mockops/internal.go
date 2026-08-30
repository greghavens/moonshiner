package mockops

import (
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"

	"vcfops.local/opsreport/internal/contract"
)

// regexpMatcher matches a request path against one contract operation and names
// the captured path parameters.
type regexpMatcher struct {
	re    *regexp.Regexp
	names []string
}

func newMatcher(c *contract.Contract, op contract.Operation) *regexpMatcher {
	names := make([]string, 0, len(op.PathParameters))
	for _, p := range op.PathParameters {
		names = append(names, p.Name)
	}
	return &regexpMatcher{re: c.PathMatcher(op), names: names}
}

func (m *regexpMatcher) match(path string) (map[string]string, bool) {
	sub := m.re.FindStringSubmatch(path)
	if sub == nil {
		return nil, false
	}
	params := make(map[string]string, len(m.names))
	for i, name := range m.names {
		if i+1 < len(sub) {
			// Path parameters arrive percent-encoded; report the decoded value so
			// assertions compare against the identifier the caller meant to send.
			if decoded, err := url.PathUnescape(sub[i+1]); err == nil {
				params[name] = decoded
			} else {
				params[name] = sub[i+1]
			}
		}
	}
	return params, true
}

func readBody(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	defer r.Body.Close()
	b, err := io.ReadAll(r.Body)
	if err != nil {
		return nil
	}
	return b
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if payload == nil {
		return
	}
	_ = json.NewEncoder(w).Encode(payload)
}

func cloneValues(v url.Values) url.Values {
	if v == nil {
		return nil
	}
	out := make(url.Values, len(v))
	for k, vals := range v {
		out[k] = append([]string(nil), vals...)
	}
	return out
}

func cloneMap(m map[string]string) map[string]string {
	if m == nil {
		return nil
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}
