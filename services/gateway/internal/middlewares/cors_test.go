package middlewares

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCORSAllowedOrigin(t *testing.T) {
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusTeapot)
	})

	tests := []struct {
		name          string
		origins       []string
		requestOrigin string
		wantHeader    string
	}{
		{
			name:          "explicit origin allowed",
			origins:       []string{"http://localhost:3000"},
			requestOrigin: "http://localhost:3000",
			wantHeader:    "http://localhost:3000",
		},
		{
			name:          "wildcard allows any origin",
			origins:       []string{"*"},
			requestOrigin: "https://kcr.example.local",
			wantHeader:    "*",
		},
		{
			name:          "origin not allowed",
			origins:       []string{"http://localhost:3000"},
			requestOrigin: "https://evil.example",
			wantHeader:    "",
		},
		{
			name:          "cors disabled",
			origins:       nil,
			requestOrigin: "http://localhost:3000",
			wantHeader:    "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodGet, "/api/v1/clients", http.NoBody)
			if tt.requestOrigin != "" {
				r.Header.Set("Origin", tt.requestOrigin)
			}
			w := httptest.NewRecorder()

			CORS(tt.origins)(next).ServeHTTP(w, r)

			if got := w.Header().Get("Access-Control-Allow-Origin"); got != tt.wantHeader {
				t.Errorf("Access-Control-Allow-Origin = %q, want %q", got, tt.wantHeader)
			}
			if w.Code != http.StatusTeapot {
				t.Errorf("status = %d, want passthrough %d", w.Code, http.StatusTeapot)
			}
		})
	}
}

func TestCORSPreflight(t *testing.T) {
	next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("preflight must not reach the next handler")
	})

	r := httptest.NewRequest(http.MethodOptions, "/api/v1/auth/login", http.NoBody)
	r.Header.Set("Origin", "http://localhost:3000")
	r.Header.Set("Access-Control-Request-Method", http.MethodPost)
	w := httptest.NewRecorder()

	CORS([]string{"http://localhost:3000"})(next).ServeHTTP(w, r)

	if w.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", w.Code)
	}
	if got := w.Header().Get("Access-Control-Allow-Methods"); got == "" {
		t.Error("Access-Control-Allow-Methods is empty")
	}
	if got := w.Header().Get("Access-Control-Allow-Headers"); got == "" {
		t.Error("Access-Control-Allow-Headers is empty")
	}
}

func TestCORSPreflightDisabledStillShortCircuits(t *testing.T) {
	// Без разрешённых origins заголовки CORS не ставятся,
	// но браузерный preflight всё равно не уходит в роутер.
	next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("preflight must not reach the next handler")
	})

	r := httptest.NewRequest(http.MethodOptions, "/api/v1/clients", http.NoBody)
	r.Header.Set("Access-Control-Request-Method", http.MethodGet)
	w := httptest.NewRecorder()

	CORS(nil)(next).ServeHTTP(w, r)

	if w.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", w.Code)
	}
}

func TestCORSPlainOptionsPassesThrough(t *testing.T) {
	// OPTIONS без заголовка preflight — обычный запрос, идёт дальше.
	nextCalled := false
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		nextCalled = true
		w.WriteHeader(http.StatusOK)
	})

	r := httptest.NewRequest(http.MethodOptions, "/api/v1/clients", http.NoBody)
	w := httptest.NewRecorder()

	CORS([]string{"http://localhost:3000"})(next).ServeHTTP(w, r)

	if !nextCalled || w.Code != http.StatusOK {
		t.Fatalf("next = %v, status = %d; want true, 200", nextCalled, w.Code)
	}
}
