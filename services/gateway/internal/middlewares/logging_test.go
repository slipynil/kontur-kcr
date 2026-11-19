package middlewares

import (
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

func testLogger() (*slog.Logger, *strings.Builder) {
	buf := &strings.Builder{}
	return slog.New(slog.NewTextHandler(buf, nil)), buf
}

func TestRequestLoggerWritesAttrs(t *testing.T) {
	log, buf := testLogger()
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusAccepted)
	})

	r := httptest.NewRequest(http.MethodGet, "/api/v1/clients?search=11", http.NoBody)
	identity := auth.Identity{
		UserID: uuid.MustParse("33333333-3333-3333-3333-333333333333"),
		Role:   auth.RoleOperator,
	}
	r = r.WithContext(auth.WithIdentity(r.Context(), identity))
	w := httptest.NewRecorder()

	RequestLogger(log)(next).ServeHTTP(w, r)

	output := buf.String()
	for _, want := range []string{
		"http request",
		"method=GET",
		"path=/api/v1/clients",
		"status=202",
		"duration_ms=",
		"user_id=" + identity.UserID.String(),
		"role=OPERATOR",
	} {
		if !strings.Contains(output, want) {
			t.Errorf("log %q does not contain %q", output, want)
		}
	}
}

func TestRequestLoggerDefaultsStatusOK(t *testing.T) {
	log, buf := testLogger()
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("pong"))
	})

	w := httptest.NewRecorder()
	RequestLogger(log)(next).ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/healthz", http.NoBody))

	if !strings.Contains(buf.String(), "status=200") {
		t.Errorf("log %q does not contain status=200", buf.String())
	}
}

func TestStatusWriterUnwrap(t *testing.T) {
	recorder := httptest.NewRecorder()
	writer := &statusWriter{ResponseWriter: recorder}

	if got := writer.Unwrap(); got != recorder {
		t.Fatalf("Unwrap() = %v, want the wrapped writer", got)
	}
}

func TestRecoverer(t *testing.T) {
	log, buf := testLogger()
	next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic(errors.New("boom"))
	})

	r := httptest.NewRequest(http.MethodGet, "/api/v1/users", http.NoBody)
	w := httptest.NewRecorder()

	Recoverer(log)(next).ServeHTTP(w, r)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
	if !strings.Contains(w.Body.String(), "INTERNAL_ERROR") {
		t.Errorf("body = %q, want INTERNAL_ERROR", w.Body.String())
	}
	if !strings.Contains(buf.String(), "panic recovered") {
		t.Errorf("log %q does not record the panic", buf.String())
	}
}
