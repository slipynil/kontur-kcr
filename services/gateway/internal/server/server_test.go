package server_test

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/configs"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
	"github.com/slipynil/kontur-kcr/services/identity/internal/server"
)

const testJWTSecret = "0123456789abcdef0123456789abcdef"

// fakeStore — минимальный Store для тестов роутера.
type fakeStore struct {
	mu    sync.Mutex
	users []identity.User
}

func (s *fakeStore) GetByLogin(_ context.Context, login string) (identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, user := range s.users {
		if user.Login == login {
			return user, nil
		}
	}
	return identity.User{}, identity.ErrNotFound
}

func (s *fakeStore) GetByID(_ context.Context, id uuid.UUID) (identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, user := range s.users {
		if user.ID == id {
			return user, nil
		}
	}
	return identity.User{}, identity.ErrNotFound
}

func (s *fakeStore) GetByIDs(_ context.Context, ids []uuid.UUID) ([]identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	wanted := make(map[uuid.UUID]struct{}, len(ids))
	for _, id := range ids {
		wanted[id] = struct{}{}
	}
	users := make([]identity.User, 0, len(ids))
	for _, user := range s.users {
		if _, ok := wanted[user.ID]; ok {
			users = append(users, user)
		}
	}
	return users, nil
}

func (s *fakeStore) List(_ context.Context, _, _ int) ([]identity.User, int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.users, len(s.users), nil
}

func (s *fakeStore) Create(_ context.Context, login, passwordHash string, role auth.Role) (identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, user := range s.users {
		if user.Login == login {
			return identity.User{}, identity.ErrLoginTaken
		}
	}
	user := identity.User{
		ID:           uuid.New(),
		Login:        login,
		PasswordHash: passwordHash,
		Role:         role,
		IsActive:     true,
		CreatedAt:    time.Now().UTC(),
	}
	s.users = append(s.users, user)
	return user, nil
}

// upstreamSpy фиксирует обращения прокси к внутреннему сервису.
type upstreamSpy struct {
	mu     sync.Mutex
	paths  []string
	header http.Header
}

func (s *upstreamSpy) record(r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.paths = append(s.paths, r.Method+" "+r.URL.Path)
	s.header = r.Header.Clone()
}

func (s *upstreamSpy) lastHeader() http.Header {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.header
}

type testEnv struct {
	handler   http.Handler
	store     *fakeStore
	tokens    *auth.TokenManager
	clients   *upstreamSpy
	operator  *upstreamSpy
	importAPI *upstreamSpy
	assistant *upstreamSpy
}

func newTestEnv(t *testing.T) *testEnv {
	t.Helper()

	startSpy := func() (*httptest.Server, *upstreamSpy) {
		spy := &upstreamSpy{}
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			spy.record(r)
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"ok":true}`))
		}))
		t.Cleanup(server.Close)
		return server, spy
	}
	clientsServer, clientsSpy := startSpy()
	operatorServer, operatorSpy := startSpy()
	importServer, importSpy := startSpy()
	assistantServer, assistantSpy := startSpy()

	configs.Initialize(".env", t.TempDir(), "env")
	cfg := configs.GetConfig()
	cfg.Service.ClientsEndpoint = clientsServer.URL
	cfg.Service.OperatorWorkEndpoint = operatorServer.URL
	cfg.Service.OperatorWorkToken = "test-operator-service-token"
	cfg.Service.ImportAPIEndpoint = importServer.URL
	cfg.Service.AssistantEndpoint = assistantServer.URL
	cfg.Service.JWTSecret = testJWTSecret
	cfg.Service.JWTTTL = time.Hour
	cfg.Service.CORSAllowedOrigins = []string{"http://localhost:3000"}
	cfg.Service.UpstreamTimeout = 2 * time.Second

	tokens, err := auth.NewTokenManager(testJWTSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	store := &fakeStore{}
	addUser(t, store, "admin", "password123", auth.RoleAdmin)
	addUser(t, store, "operator", "password123", auth.RoleOperator)

	handler, err := server.New(server.Deps{
		Log:    slog.New(slog.NewTextHandler(io.Discard, nil)),
		Config: cfg,
		Store:  store,
		Tokens: tokens,
	})
	if err != nil {
		t.Fatal(err)
	}

	return &testEnv{
		handler:   handler,
		store:     store,
		tokens:    tokens,
		clients:   clientsSpy,
		operator:  operatorSpy,
		importAPI: importSpy,
		assistant: assistantSpy,
	}
}

func addUser(t *testing.T, store *fakeStore, login, password string, role auth.Role) identity.User {
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
		IsActive:     true,
		CreatedAt:    time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
	}
	store.users = append(store.users, user)
	return user
}

// login получает токен через публичный эндпоинт.
func (e *testEnv) login(t *testing.T, login string) string {
	t.Helper()
	body := fmt.Sprintf(`{"login":%q,"password":"password123"}`, login)
	r := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", strings.NewReader(body))
	w := httptest.NewRecorder()
	e.handler.ServeHTTP(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("login %s: status = %d, body %s", login, w.Code, w.Body.String())
	}
	var resp struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatal(err)
	}
	return resp.Token
}

func (e *testEnv) do(method, target, token string) *httptest.ResponseRecorder {
	var body io.Reader
	if method == http.MethodPost && strings.Contains(target, "/events") {
		body = strings.NewReader(`{"event_type":"CALL_RESULT"}`)
	}
	r := httptest.NewRequest(method, target, body)
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	e.handler.ServeHTTP(w, r)
	return w
}

func TestHealthz(t *testing.T) {
	env := newTestEnv(t)
	w := env.do(http.MethodGet, "/healthz", "")
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"status":"ok"`) {
		t.Errorf("body = %q", w.Body.String())
	}
}

func TestLoginIssuesJWT(t *testing.T) {
	env := newTestEnv(t)
	token := env.login(t, "admin")
	if token == "" {
		t.Fatal("token is empty")
	}
}

func TestUnknownRouteAndMethod(t *testing.T) {
	env := newTestEnv(t)
	admin := env.login(t, "admin")

	w := env.do(http.MethodGet, "/api/v1/nothing", admin)
	if w.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", w.Code)
	}
	if !strings.Contains(w.Body.String(), "NOT_FOUND") {
		t.Errorf("body = %q", w.Body.String())
	}

	// Удаления клиентов пока нет в контрактах — метод не поддерживается.
	w = env.do(http.MethodDelete, "/api/v1/clients/6671-0042", admin)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"code"`) {
		t.Errorf("body must be JSON error, got %q", w.Body.String())
	}
}

func TestUsersEndpointsRBAC(t *testing.T) {
	env := newTestEnv(t)
	admin := env.login(t, "admin")
	operator := env.login(t, "operator")

	tests := []struct {
		name     string
		token    string
		wantCode int
	}{
		{name: "admin lists users", token: admin, wantCode: http.StatusOK},
		{name: "operator forbidden", token: operator, wantCode: http.StatusForbidden},
		{name: "anonymous unauthorized", token: "", wantCode: http.StatusUnauthorized},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := env.do(http.MethodGet, "/api/v1/users", tt.token)
			if w.Code != tt.wantCode {
				t.Fatalf("status = %d, want %d", w.Code, tt.wantCode)
			}
		})
	}

	t.Run("admin creates user", func(t *testing.T) {
		body := `{"login":"sidorov","password":"password123","role":"OPERATOR"}`
		r := httptest.NewRequest(http.MethodPost, "/api/v1/users", strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer "+admin)
		w := httptest.NewRecorder()
		env.handler.ServeHTTP(w, r)
		if w.Code != http.StatusCreated {
			t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
		}
	})
}

func TestProxyRoutingTable(t *testing.T) {
	env := newTestEnv(t)
	admin := env.login(t, "admin")
	operator := env.login(t, "operator")

	tests := []struct {
		name     string
		method   string
		target   string
		token    string
		wantCode int
		wantSpy  *upstreamSpy
		wantPath string
	}{
		{
			name: "clients list", method: http.MethodGet, target: "/api/v1/clients",
			token: operator, wantCode: http.StatusOK, wantSpy: env.clients, wantPath: "GET /internal/v1/clients",
		},
		{
			name: "client card", method: http.MethodGet, target: "/api/v1/clients/6671-0042",
			token: operator, wantCode: http.StatusOK, wantSpy: env.clients, wantPath: "GET /internal/v1/clients/6671-0042",
		},
		{
			name: "snapshots history", method: http.MethodGet, target: "/api/v1/clients/6671-0042/snapshots/history",
			token: admin, wantCode: http.StatusOK, wantSpy: env.clients, wantPath: "GET /internal/v1/clients/6671-0042/snapshots/history",
		},
		{
			name: "take client", method: http.MethodPost, target: "/api/v1/clients/6671-0042/take?work_quarter=2026_Q4",
			token: operator, wantCode: http.StatusOK, wantSpy: env.operator, wantPath: "POST /internal/v1/clients/6671-0042/take",
		},
		{
			name: "events feed", method: http.MethodGet, target: "/api/v1/clients/6671-0042/events",
			token: operator, wantCode: http.StatusOK, wantSpy: env.operator, wantPath: "GET /internal/v1/clients/6671-0042/events",
		},
		{
			name: "reassignment notifications", method: http.MethodGet, target: "/api/v1/reassignment-notifications",
			token: operator, wantCode: http.StatusOK, wantSpy: env.operator, wantPath: "GET /internal/v1/reassignment-notifications",
		},
		{
			name: "reject reassignment", method: http.MethodPost, target: "/api/v1/reassignment-notifications/11111111-1111-1111-1111-111111111111/reject",
			token: operator, wantCode: http.StatusOK, wantSpy: env.operator, wantPath: "POST /internal/v1/reassignment-notifications/11111111-1111-1111-1111-111111111111/reject",
		},
		{
			name: "upload import", method: http.MethodPost, target: "/api/v1/imports",
			token: admin, wantCode: http.StatusOK, wantSpy: env.importAPI, wantPath: "POST /internal/v1/imports",
		},
		{
			name: "import status", method: http.MethodGet, target: "/api/v1/imports/22222222-2222-2222-2222-222222222222",
			token: admin, wantCode: http.StatusOK, wantSpy: env.importAPI, wantPath: "GET /internal/v1/imports/22222222-2222-2222-2222-222222222222",
		},
		{
			name: "assistant chat", method: http.MethodPost, target: "/api/v1/assistant/chat",
			token: admin, wantCode: http.StatusOK, wantSpy: env.assistant, wantPath: "POST /internal/v1/assistant/chat",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			before := len(tt.wantSpy.paths)
			w := env.do(tt.method, tt.target, tt.token)
			if w.Code != tt.wantCode {
				t.Fatalf("status = %d, want %d, body %s", w.Code, tt.wantCode, w.Body.String())
			}
			paths := tt.wantSpy.paths
			if len(paths) != before+1 || paths[len(paths)-1] != tt.wantPath {
				t.Fatalf("upstream got %v, want last %q", paths, tt.wantPath)
			}
		})
	}
}

func TestProxyRoleRestrictions(t *testing.T) {
	env := newTestEnv(t)
	admin := env.login(t, "admin")
	operator := env.login(t, "operator")

	tests := []struct {
		name   string
		method string
		target string
		token  string
	}{
		// Импорт доступен только админу.
		{name: "operator cannot import", method: http.MethodPost, target: "/api/v1/imports", token: operator},
		// Взятие клиента — действие оператора.
		{name: "admin cannot take client", method: http.MethodPost, target: "/api/v1/clients/6671-0042/take", token: admin},
		{name: "anonymous cannot read clients", method: http.MethodGet, target: "/api/v1/clients", token: ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := env.do(tt.method, tt.target, tt.token)
			if w.Code != http.StatusForbidden && w.Code != http.StatusUnauthorized {
				t.Fatalf("status = %d, want 401/403", w.Code)
			}
		})
	}
}

func TestProxySendsIdentityHeaders(t *testing.T) {
	env := newTestEnv(t)
	operator := env.login(t, "operator")
	ident, err := env.tokens.Verify(operator)
	if err != nil {
		t.Fatal(err)
	}

	r := httptest.NewRequest(http.MethodGet, "/api/v1/clients", http.NoBody)
	r.Header.Set("Authorization", "Bearer "+operator)
	r.Header.Set("X-User-Id", "00000000-0000-0000-0000-000000000000")
	r.Header.Set("X-User-Role", "ADMIN")
	w := httptest.NewRecorder()
	env.handler.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d", w.Code)
	}
	header := env.clients.lastHeader()
	if got := header.Get("X-User-Id"); got != ident.UserID.String() {
		t.Errorf("X-User-Id = %q, want %q", got, ident.UserID.String())
	}
	if got := header.Get("X-User-Role"); got != "OPERATOR" {
		t.Errorf("X-User-Role = %q, want OPERATOR", got)
	}
	if got := header.Get("Authorization"); got != "Bearer "+operator {
		t.Error("client upstream did not receive the user's JWT")
	}

	w = env.do(http.MethodGet, "/api/v1/reassignment-notifications", operator)
	if w.Code != http.StatusOK {
		t.Fatalf("operator upstream status = %d", w.Code)
	}
	header = env.operator.lastHeader()
	if header.Get("Authorization") != "Bearer test-operator-service-token" {
		t.Error("operator upstream did not receive the service token")
	}
	if header.Get("X-User-Id") != ident.UserID.String() || header.Get("X-User-Role") != "OPERATOR" {
		t.Error("operator upstream did not receive the authenticated user's identity")
	}
}

func TestInternalUsersEndpointWithoutJWT(t *testing.T) {
	env := newTestEnv(t)
	admin, _ := env.store.GetByLogin(context.Background(), "admin")

	target := "/internal/v1/users/" + admin.ID.String()
	w := env.do(http.MethodGet, target, "")
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), admin.Login) {
		t.Errorf("body = %q", w.Body.String())
	}
}

func TestCORSPreflightBeforeAuth(t *testing.T) {
	env := newTestEnv(t)

	r := httptest.NewRequest(http.MethodOptions, "/api/v1/clients", http.NoBody)
	r.Header.Set("Origin", "http://localhost:3000")
	r.Header.Set("Access-Control-Request-Method", http.MethodGet)
	w := httptest.NewRecorder()
	env.handler.ServeHTTP(w, r)

	if w.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", w.Code)
	}
	if got := w.Header().Get("Access-Control-Allow-Origin"); got != "http://localhost:3000" {
		t.Errorf("Access-Control-Allow-Origin = %q", got)
	}
}

func TestListenAndServeGracefulShutdown(t *testing.T) {
	env := newTestEnv(t)
	log := slog.New(slog.NewTextHandler(io.Discard, nil))

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- server.ListenAndServe(ctx, log, env.handler, 0)
	}()

	// Даём серверу подняться и проверяем healthz на выделенном порту.
	time.Sleep(100 * time.Millisecond)
	cancel()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("ListenAndServe returned error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("server did not stop after cancel")
	}
}

func TestListenAndServePortBusy(t *testing.T) {
	env := newTestEnv(t)
	log := slog.New(slog.NewTextHandler(io.Discard, nil))

	blocker := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	t.Cleanup(blocker.Close)
	// Порт уже занят test-сервером.
	port := strings.TrimPrefix(blocker.URL, "http://127.0.0.1:")

	err := server.ListenAndServe(context.Background(), log, env.handler, mustAtoi(t, port))
	if err == nil {
		t.Fatal("ListenAndServe = nil, want error for busy port")
	}
}

func mustAtoi(t *testing.T, s string) int {
	t.Helper()
	var port int
	if _, err := fmt.Sscanf(s, "%d", &port); err != nil {
		t.Fatal(err)
	}
	return port
}
