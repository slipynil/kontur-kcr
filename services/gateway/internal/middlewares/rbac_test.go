package middlewares

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

func withIdentity(role auth.Role) *http.Request {
	r := httptest.NewRequest(http.MethodGet, "/api/v1/users", http.NoBody)
	identity := auth.Identity{
		UserID: uuid.MustParse("22222222-2222-2222-2222-222222222222"),
		Role:   role,
	}
	return r.WithContext(auth.WithIdentity(r.Context(), identity))
}

func TestRequireRoleAllows(t *testing.T) {
	tests := []struct {
		name  string
		role  auth.Role
		allow []auth.Role
	}{
		{name: "admin on admin", role: auth.RoleAdmin, allow: []auth.Role{auth.RoleAdmin}},
		{name: "operator on both", role: auth.RoleOperator, allow: []auth.Role{auth.RoleAdmin, auth.RoleOperator}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			nextCalled := false
			next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) { nextCalled = true })
			w := httptest.NewRecorder()

			RequireRole(tt.allow...)(next).ServeHTTP(w, withIdentity(tt.role))

			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200", w.Code)
			}
			if !nextCalled {
				t.Error("next handler was not called")
			}
		})
	}
}

func TestRequireRoleForbids(t *testing.T) {
	next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("next handler must not be called")
	})
	w := httptest.NewRecorder()

	RequireRole(auth.RoleAdmin)(next).ServeHTTP(w, withIdentity(auth.RoleOperator))

	if w.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", w.Code)
	}
	if !strings.Contains(w.Body.String(), "FORBIDDEN") {
		t.Fatalf("body = %q, want FORBIDDEN", w.Body.String())
	}
}

func TestRequireRoleWithoutIdentity(t *testing.T) {
	// Защита: маршрут забыл Authenticate — запрос не должен пройти дальше.
	next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("next handler must not be called")
	})
	w := httptest.NewRecorder()

	RequireRole(auth.RoleAdmin)(next).ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/v1/users", http.NoBody))

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", w.Code)
	}
}
