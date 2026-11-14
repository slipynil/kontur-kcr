package middlewares

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

const testSecret = "0123456789abcdef0123456789abcdef"

func testTokens(t *testing.T) *auth.TokenManager {
	t.Helper()
	tokens, err := auth.NewTokenManager(testSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	return tokens
}

func TestBearerToken(t *testing.T) {
	tests := []struct {
		header string
		want   string
		wantOK bool
	}{
		{header: "Bearer abc.def.ghi", want: "abc.def.ghi", wantOK: true},
		{header: "bearer abc", want: "abc", wantOK: true},
		{header: "BEARER abc", want: "abc", wantOK: true},
		{header: "Bearer  spaced", want: "spaced", wantOK: true},
		{header: "Bearer ", want: "", wantOK: false},
		{header: "Basic dXNlcjpwYXNz", want: "", wantOK: false},
		{header: "", want: "", wantOK: false},
		{header: "abc.def.ghi", want: "", wantOK: false},
	}
	for _, tt := range tests {
		t.Run(tt.header, func(t *testing.T) {
			got, ok := bearerToken(tt.header)
			if ok != tt.wantOK || got != tt.want {
				t.Errorf("bearerToken(%q) = %q, %v; want %q, %v", tt.header, got, ok, tt.want, tt.wantOK)
			}
		})
	}
}

func TestAuthenticateAllowsValidToken(t *testing.T) {
	tokens := testTokens(t)
	userID := uuid.MustParse("11111111-1111-1111-1111-111111111111")
	token, _, err := tokens.Issue(userID, auth.RoleOperator)
	if err != nil {
		t.Fatal(err)
	}

	var gotIdentity auth.Identity
	next := http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		gotIdentity, _ = auth.FromContext(r.Context())
	})

	r := httptest.NewRequest(http.MethodGet, "/api/v1/clients", http.NoBody)
	r.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()

	Authenticate(tokens)(next).ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	if gotIdentity.UserID != userID || gotIdentity.Role != auth.RoleOperator {
		t.Fatalf("identity = %+v", gotIdentity)
	}
}

func TestAuthenticateRejects(t *testing.T) {
	tokens := testTokens(t)
	nextCalled := false
	next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) { nextCalled = true })

	tests := []struct {
		name        string
		header      string
		wantCode    int
		wantErrCode string
	}{
		{name: "no header", header: "", wantCode: http.StatusUnauthorized, wantErrCode: "UNAUTHORIZED"},
		{name: "wrong scheme", header: "Basic abc", wantCode: http.StatusUnauthorized, wantErrCode: "UNAUTHORIZED"},
		{name: "garbage token", header: "Bearer garbage", wantCode: http.StatusUnauthorized, wantErrCode: "INVALID_TOKEN"},
		{name: "foreign signature", header: "Bearer eyJhbGciOiJIUzI1NiJ9.e30.abc", wantCode: http.StatusUnauthorized, wantErrCode: "INVALID_TOKEN"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodGet, "/api/v1/clients", http.NoBody)
			if tt.header != "" {
				r.Header.Set("Authorization", tt.header)
			}
			w := httptest.NewRecorder()

			Authenticate(tokens)(next).ServeHTTP(w, r)

			if w.Code != tt.wantCode {
				t.Fatalf("status = %d, want %d", w.Code, tt.wantCode)
			}
			if !strings.Contains(w.Body.String(), tt.wantErrCode) {
				t.Fatalf("body = %q, want code %q", w.Body.String(), tt.wantErrCode)
			}
		})
	}
	if nextCalled {
		t.Error("next handler must not be called for rejected requests")
	}
}
