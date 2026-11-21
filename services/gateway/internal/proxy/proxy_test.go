package proxy

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

func TestRewritePath(t *testing.T) {
	tests := []struct {
		in   string
		want string
	}{
		{in: "/api/v1", want: "/internal/v1"},
		{in: "/api/v1/clients", want: "/internal/v1/clients"},
		{in: "/api/v1/clients/6671-0042/events", want: "/internal/v1/clients/6671-0042/events"},
		{in: "/healthz", want: "/healthz"},
		{in: "/api/v2/clients", want: "/api/v2/clients"},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			if got := rewritePath(tt.in); got != tt.want {
				t.Errorf("rewritePath(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestNewRejectsInvalidURLs(t *testing.T) {
	tests := []struct {
		name    string
		clients string
	}{
		{name: "unparsable", clients: "://bad url"},
		{name: "no host", clients: "http://"},
		{name: "no scheme", clients: "127.0.0.1:8001"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := New(slog.Default(), time.Second, tt.clients, "http://import:80", "http://op:80", "http://ai:80")
			if err == nil {
				t.Fatal("New = nil error, want error")
			}
		})
	}
}

// upstreamRecord фиксирует, что пришло во внутренний сервис.
type upstreamRecord struct {
	method        string
	path          string
	rawQuery      string
	userIDHeader  string
	roleHeader    string
	authorization string
	contentType   string
	body          string
}

func newRecordingUpstream(t *testing.T) (*httptest.Server, *upstreamRecord) {
	t.Helper()
	rec := &upstreamRecord{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read upstream body: %v", err)
		}
		rec.method = r.Method
		rec.path = r.URL.Path
		rec.rawQuery = r.URL.RawQuery
		rec.userIDHeader = r.Header.Get(HeaderUserID)
		rec.roleHeader = r.Header.Get(HeaderUserRole)
		rec.authorization = r.Header.Get("Authorization")
		rec.contentType = r.Header.Get("Content-Type")
		rec.body = string(body)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"upstream":true}`))
	}))
	t.Cleanup(server.Close)
	return server, rec
}

func newTestUpstreams(t *testing.T, clientsURL string) *Upstreams {
	t.Helper()
	upstreams, err := New(slog.Default(), 2*time.Second, clientsURL, "http://import.test", "http://op.test", "http://ai.test")
	if err != nil {
		t.Fatal(err)
	}
	return upstreams
}

func TestProxyForwardsToInternalPath(t *testing.T) {
	upstream, rec := newRecordingUpstream(t)
	upstreams := newTestUpstreams(t, upstream.URL)

	r := httptest.NewRequest(http.MethodGet, "/api/v1/clients/6671-0042?search=11", strings.NewReader(""))
	w := httptest.NewRecorder()

	upstreams.HandlerFunc(ServiceClients).ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"upstream":true`) {
		t.Fatalf("upstream body not proxied: %q", w.Body.String())
	}
	if rec.path != "/internal/v1/clients/6671-0042" {
		t.Errorf("upstream path = %q, want /internal/v1/clients/6671-0042", rec.path)
	}
	if rec.rawQuery != "search=11" {
		t.Errorf("upstream query = %q, want search=11", rec.rawQuery)
	}
}

func TestProxyPassesMethodAndBody(t *testing.T) {
	upstream, rec := newRecordingUpstream(t)
	upstreams := newTestUpstreams(t, upstream.URL)

	body := `{"event_type":"CALL_RESULT"}`
	r := httptest.NewRequest(http.MethodPost, "/api/v1/clients/6671-0042/events", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	upstreams.HandlerFunc(ServiceClients).ServeHTTP(w, r)

	if rec.method != http.MethodPost {
		t.Errorf("upstream method = %q", rec.method)
	}
	if rec.body != body {
		t.Errorf("upstream body = %q, want %q", rec.body, body)
	}
	if rec.contentType != "application/json" {
		t.Errorf("upstream content type = %q", rec.contentType)
	}
}

func TestProxySetsIdentityHeaders(t *testing.T) {
	upstream, rec := newRecordingUpstream(t)
	upstreams := newTestUpstreams(t, upstream.URL)

	identity := auth.Identity{
		UserID: uuid.MustParse("44444444-4444-4444-4444-444444444444"),
		Role:   auth.RoleOperator,
	}
	r := httptest.NewRequest(http.MethodGet, "/api/v1/clients", http.NoBody)
	// Клиент пытается подделать заголовки идентичности — они обязаны затираться.
	r.Header.Set(HeaderUserID, "00000000-0000-0000-0000-000000000000")
	r.Header.Set(HeaderUserRole, "ADMIN")
	r = r.WithContext(auth.WithIdentity(r.Context(), identity))
	w := httptest.NewRecorder()

	upstreams.HandlerFunc(ServiceClients).ServeHTTP(w, r)

	if rec.userIDHeader != identity.UserID.String() {
		t.Errorf("X-User-Id = %q, want %q", rec.userIDHeader, identity.UserID.String())
	}
	if rec.roleHeader != "OPERATOR" {
		t.Errorf("X-User-Role = %q, want OPERATOR", rec.roleHeader)
	}
}

func TestProxyStripsIdentityHeadersWithoutIdentity(t *testing.T) {
	upstream, rec := newRecordingUpstream(t)
	upstreams := newTestUpstreams(t, upstream.URL)

	r := httptest.NewRequest(http.MethodGet, "/api/v1/clients", http.NoBody)
	r.Header.Set(HeaderUserID, "00000000-0000-0000-0000-000000000000")
	r.Header.Set(HeaderUserRole, "ADMIN")
	w := httptest.NewRecorder()

	upstreams.HandlerFunc(ServiceClients).ServeHTTP(w, r)

	if rec.userIDHeader != "" || rec.roleHeader != "" {
		t.Errorf("spoofed headers leaked: X-User-Id=%q X-User-Role=%q", rec.userIDHeader, rec.roleHeader)
	}
}

func TestProxyUpstreamUnavailable(t *testing.T) {
	upstream, _ := newRecordingUpstream(t)
	url := upstream.URL
	upstream.Close() // имитация недоступности сервиса

	upstreams, err := New(slog.Default(), 500*time.Millisecond, url, "http://import.test", "http://op.test", "http://ai.test")
	if err != nil {
		t.Fatal(err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/v1/clients", http.NoBody)
	w := httptest.NewRecorder()

	upstreams.HandlerFunc(ServiceClients).ServeHTTP(w, r)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503, body %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "SERVICE_UNAVAILABLE") {
		t.Errorf("body = %q, want SERVICE_UNAVAILABLE", w.Body.String())
	}
}

func TestProxyPassesUpstreamErrorStatus(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, `{"code":"CLIENT_WAIT_DEADLINE_ACTIVE"}`, http.StatusConflict)
	}))
	t.Cleanup(upstream.Close)
	upstreams := newTestUpstreams(t, upstream.URL)

	r := httptest.NewRequest(http.MethodPost, "/api/v1/clients/6671-0042/take", http.NoBody)
	w := httptest.NewRecorder()

	upstreams.HandlerFunc(ServiceClients).ServeHTTP(w, r)

	if w.Code != http.StatusConflict {
		t.Fatalf("status = %d, want 409", w.Code)
	}
	if !strings.Contains(w.Body.String(), "CLIENT_WAIT_DEADLINE_ACTIVE") {
		t.Errorf("body = %q", w.Body.String())
	}
}

func TestHandlerUnknownService(t *testing.T) {
	upstreams := newTestUpstreams(t, "http://clients.test")

	defer func() {
		if recovered := recover(); recovered == nil {
			t.Error("Handler(unknown) must panic at wiring time")
		}
	}()
	_ = upstreams.Handler("unknown-service")
}
