package identity_test

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

const testJWTSecret = "0123456789abcdef0123456789abcdef"

func newTestHandlers(t *testing.T) (*identity.Handlers, *memStore) {
	t.Helper()
	tokens, err := auth.NewTokenManager(testJWTSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	store := &memStore{}
	return &identity.Handlers{Store: store, Tokens: tokens}, store
}

func seedUser(t *testing.T, store *memStore, login, password string, role auth.Role, active bool) identity.User {
	t.Helper()
	hash, err := identity.HashPassword(password)
	if err != nil {
		t.Fatal(err)
	}
	user := identity.User{
		ID:           uuid.New(),
		Login:        login,
		PasswordHash: hash,
		Role:         role,
		IsActive:     active,
		CreatedAt:    time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC),
	}
	store.users = append(store.users, user)
	return user
}

func doJSON(handler http.HandlerFunc, method, target, body string) *httptest.ResponseRecorder {
	var reader *strings.Reader
	if body == "" {
		reader = strings.NewReader("")
	} else {
		reader = strings.NewReader(body)
	}
	r := httptest.NewRequest(method, target, reader)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(w, r)
	return w
}

func decodeBody(t *testing.T, w *httptest.ResponseRecorder, dst any) {
	t.Helper()
	if err := json.NewDecoder(w.Body).Decode(dst); err != nil {
		t.Fatalf("decode response body %q: %v", w.Body.String(), err)
	}
}

func TestLoginSuccess(t *testing.T) {
	h, store := newTestHandlers(t)
	admin := seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)

	w := doJSON(h.Login, http.MethodPost, "/api/v1/auth/login", `{"login":"ivanov","password":"password123"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}

	var resp struct {
		Token     string `json:"token"`
		TokenType string `json:"token_type"`
		ExpiresIn int64  `json:"expires_in"`
		User      struct {
			ID        string `json:"id"`
			Login     string `json:"login"`
			Role      string `json:"role"`
			IsActive  bool   `json:"is_active"`
			CreatedAt string `json:"created_at"`
		} `json:"user"`
	}
	decodeBody(t, w, &resp)

	if resp.TokenType != "Bearer" {
		t.Errorf("token_type = %q, want Bearer", resp.TokenType)
	}
	if resp.ExpiresIn != 3600 {
		t.Errorf("expires_in = %d, want 3600", resp.ExpiresIn)
	}
	if resp.User.ID != admin.ID.String() || resp.User.Login != "ivanov" ||
		resp.User.Role != "ADMIN" || !resp.User.IsActive {
		t.Errorf("user payload = %+v", resp.User)
	}
	if strings.Contains(w.Body.String(), "password") {
		t.Error("response must not contain the password hash")
	}
}

func TestLoginCredentialsRejected(t *testing.T) {
	tests := []struct {
		name  string
		login string
		pass  string
	}{
		{name: "unknown login", login: "ghost", pass: "password123"},
		{name: "wrong password", login: "ivanov", pass: "wrong-password"},
		{name: "inactive user", login: "petrov", pass: "password123"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			h, store := newTestHandlers(t)
			seedUser(t, store, "ivanov", "password123", auth.RoleOperator, true)
			seedUser(t, store, "petrov", "password123", auth.RoleOperator, false)

			w := doJSON(h.Login, http.MethodPost, "/api/v1/auth/login",
				`{"login":"`+tt.login+`","password":"`+tt.pass+`"}`)
			if w.Code != http.StatusUnauthorized {
				t.Fatalf("status = %d, want 401", w.Code)
			}
			var body struct {
				Code string `json:"code"`
			}
			decodeBody(t, w, &body)
			if body.Code != "INVALID_CREDENTIALS" {
				t.Errorf("code = %q, want INVALID_CREDENTIALS", body.Code)
			}
		})
	}
}

func TestLoginBadRequest(t *testing.T) {
	h, _ := newTestHandlers(t)

	tests := []struct {
		name string
		body string
	}{
		{name: "malformed json", body: `{`},
		{name: "empty login", body: `{"login":"","password":"x"}`},
		{name: "empty password", body: `{"login":"ivanov","password":""}`},
		{name: "unknown field", body: `{"login":"ivanov","password":"x","extra":1}`},
		{name: "no body", body: ``},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := doJSON(h.Login, http.MethodPost, "/api/v1/auth/login", tt.body)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400, body %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestLoginStoreFailure(t *testing.T) {
	h, store := newTestHandlers(t)
	seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)
	store.fail = errors.New("db is down")

	w := doJSON(h.Login, http.MethodPost, "/api/v1/auth/login", `{"login":"ivanov","password":"password123"}`)
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
}

func TestListUsers(t *testing.T) {
	h, store := newTestHandlers(t)
	seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)
	seedUser(t, store, "petrov", "password123", auth.RoleOperator, true)

	w := doJSON(h.ListUsers, http.MethodGet, "/api/v1/users", "")
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	var resp struct {
		Items    []json.RawMessage `json:"items"`
		Page     int               `json:"page"`
		PageSize int               `json:"page_size"`
		Total    int               `json:"total"`
	}
	decodeBody(t, w, &resp)
	if len(resp.Items) != 2 || resp.Page != 1 || resp.PageSize != 20 || resp.Total != 2 {
		t.Errorf("response = items %d, page %d, size %d, total %d", len(resp.Items), resp.Page, resp.PageSize, resp.Total)
	}
	if store.lastListLimit != 20 || store.lastListOffset != 0 {
		t.Errorf("store List args = %d, %d; want 20, 0", store.lastListLimit, store.lastListOffset)
	}
}

func TestListUsersPagination(t *testing.T) {
	h, store := newTestHandlers(t)
	seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)

	w := doJSON(h.ListUsers, http.MethodGet, "/api/v1/users?page=3&page_size=10", "")
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	if store.lastListLimit != 10 || store.lastListOffset != 20 {
		t.Errorf("store List args = %d, %d; want 10, 20", store.lastListLimit, store.lastListOffset)
	}
}

func TestListUsersInvalidParams(t *testing.T) {
	h, store := newTestHandlers(t)
	seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)

	tests := []string{
		"/api/v1/users?page=0",
		"/api/v1/users?page=-1",
		"/api/v1/users?page=abc",
		"/api/v1/users?page_size=0",
		"/api/v1/users?page_size=101",
		"/api/v1/users?page_size=abc",
	}
	for _, target := range tests {
		t.Run(target, func(t *testing.T) {
			w := doJSON(h.ListUsers, http.MethodGet, target, "")
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400", w.Code)
			}
		})
	}
}

func TestListUsersStoreFailure(t *testing.T) {
	h, store := newTestHandlers(t)
	store.fail = errors.New("db is down")

	w := doJSON(h.ListUsers, http.MethodGet, "/api/v1/users", "")
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
}

func TestCreateUser(t *testing.T) {
	h, _ := newTestHandlers(t)

	w := doJSON(h.CreateUser, http.MethodPost, "/api/v1/users",
		`{"login":"  sidorov  ","password":"password123","role":"OPERATOR"}`)
	if w.Code != http.StatusCreated {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}

	var resp struct {
		Login string `json:"login"`
		Role  string `json:"role"`
	}
	decodeBody(t, w, &resp)
	if resp.Login != "sidorov" {
		t.Errorf("login = %q, want trimmed sidorov", resp.Login)
	}
	if resp.Role != "OPERATOR" {
		t.Errorf("role = %q", resp.Role)
	}

	// Созданный пользователь может войти.
	w = doJSON(h.Login, http.MethodPost, "/api/v1/auth/login", `{"login":"sidorov","password":"password123"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("login after create: status = %d", w.Code)
	}
}

func TestCreateUserValidation(t *testing.T) {
	h, _ := newTestHandlers(t)

	tests := []struct {
		name string
		body string
	}{
		{name: "empty login", body: `{"login":"","password":"password123","role":"OPERATOR"}`},
		{name: "login too long", body: `{"login":"` + strings.Repeat("a", 65) + `","password":"password123","role":"OPERATOR"}`},
		{name: "short password", body: `{"login":"sidorov","password":"1234567","role":"OPERATOR"}`},
		{name: "long password", body: `{"login":"sidorov","password":"` + strings.Repeat("a", 73) + `","role":"OPERATOR"}`},
		{name: "unknown role", body: `{"login":"sidorov","password":"password123","role":"SUPERUSER"}`},
		{name: "empty role", body: `{"login":"sidorov","password":"password123"}`},
		{name: "malformed json", body: `{`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := doJSON(h.CreateUser, http.MethodPost, "/api/v1/users", tt.body)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400, body %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestCreateUserLoginTaken(t *testing.T) {
	h, store := newTestHandlers(t)
	seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)

	w := doJSON(h.CreateUser, http.MethodPost, "/api/v1/users",
		`{"login":"ivanov","password":"password123","role":"OPERATOR"}`)
	if w.Code != http.StatusConflict {
		t.Fatalf("status = %d, want 409", w.Code)
	}
	var body struct {
		Code string `json:"code"`
	}
	decodeBody(t, w, &body)
	if body.Code != "LOGIN_ALREADY_EXISTS" {
		t.Errorf("code = %q", body.Code)
	}
}

func TestCreateUserStoreFailure(t *testing.T) {
	h, store := newTestHandlers(t)
	store.fail = errors.New("db is down")

	w := doJSON(h.CreateUser, http.MethodPost, "/api/v1/users",
		`{"login":"sidorov","password":"password123","role":"OPERATOR"}`)
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
}

func TestGetInternalUser(t *testing.T) {
	h, store := newTestHandlers(t)
	user := seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)

	// chi.URLParam работает только внутри chi-роутера.
	router := chi.NewRouter()
	router.Get("/internal/v1/users/{user_id}", h.GetInternalUser)
	do := func(path string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, http.NoBody))
		return w
	}

	t.Run("found", func(t *testing.T) {
		w := do("/internal/v1/users/" + user.ID.String())
		if w.Code != http.StatusOK {
			t.Fatalf("status = %d", w.Code)
		}
		var resp struct {
			ID          string `json:"id"`
			Login       string `json:"login"`
			DisplayName string `json:"display_name"`
			Role        string `json:"role"`
			IsActive    bool   `json:"is_active"`
		}
		decodeBody(t, w, &resp)
		if resp.ID != user.ID.String() || resp.Login != "ivanov" || resp.DisplayName != "ivanov" ||
			resp.Role != "ADMIN" || !resp.IsActive {
			t.Errorf("response = %+v", resp)
		}
	})

	t.Run("not found", func(t *testing.T) {
		missing := uuid.New()
		w := do("/internal/v1/users/" + missing.String())
		if w.Code != http.StatusNotFound {
			t.Fatalf("status = %d, want 404", w.Code)
		}
	})

	t.Run("invalid uuid", func(t *testing.T) {
		w := do("/internal/v1/users/not-a-uuid")
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status = %d, want 400", w.Code)
		}
	})
}

func TestGetInternalUserStoreFailure(t *testing.T) {
	h, store := newTestHandlers(t)
	user := seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)
	store.fail = errors.New("db is down")

	router := chi.NewRouter()
	router.Get("/internal/v1/users/{user_id}", h.GetInternalUser)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/internal/v1/users/"+user.ID.String(), http.NoBody))

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
}

func TestListInternalUsers(t *testing.T) {
	h, store := newTestHandlers(t)
	first := seedUser(t, store, "ivanov", "password123", auth.RoleAdmin, true)
	second := seedUser(t, store, "petrov", "password123", auth.RoleOperator, true)

	target := "/internal/v1/users?ids=" + first.ID.String() + "," + second.ID.String()
	w := doJSON(h.ListInternalUsers, http.MethodGet, target, "")
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	var resp struct {
		Items []struct {
			ID    string `json:"id"`
			Login string `json:"login"`
		} `json:"items"`
	}
	decodeBody(t, w, &resp)
	if len(resp.Items) != 2 {
		t.Fatalf("items = %d, want 2", len(resp.Items))
	}

	t.Run("missing ids are skipped by store contract", func(t *testing.T) {
		target := "/internal/v1/users?ids=" + uuid.New().String()
		w := doJSON(h.ListInternalUsers, http.MethodGet, target, "")
		if w.Code != http.StatusOK {
			t.Fatalf("status = %d", w.Code)
		}
		var resp struct {
			Items []json.RawMessage `json:"items"`
		}
		decodeBody(t, w, &resp)
		if len(resp.Items) != 0 {
			t.Errorf("items = %d, want 0", len(resp.Items))
		}
	})
}

func TestListInternalUsersInvalid(t *testing.T) {
	h, _ := newTestHandlers(t)

	tests := []struct {
		name   string
		target string
	}{
		{name: "no ids", target: "/internal/v1/users"},
		{name: "empty ids", target: "/internal/v1/users?ids="},
		{name: "invalid uuid", target: "/internal/v1/users?ids=not-a-uuid"},
		{name: "too many ids", target: "/internal/v1/users?ids=" + strings.Join(manyUUIDs(t, 101), ",")},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := doJSON(h.ListInternalUsers, http.MethodGet, tt.target, "")
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400", w.Code)
			}
		})
	}
}

func TestListInternalUsersStoreFailure(t *testing.T) {
	h, store := newTestHandlers(t)
	store.fail = errors.New("db is down")

	w := doJSON(h.ListInternalUsers, http.MethodGet, "/internal/v1/users?ids="+uuid.New().String(), "")
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
}

func manyUUIDs(t *testing.T, n int) []string {
	t.Helper()
	ids := make([]string, 0, n)
	for range n {
		ids = append(ids, uuid.New().String())
	}
	return ids
}
