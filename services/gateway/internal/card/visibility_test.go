package card_test

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/card"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

const (
	publishedCodesPath = "/internal/v1/pool/published-codes"
	unblockedCodesPath = "/internal/v1/clients/unblocked-codes"
	registryPath       = "/internal/v1/clients/operator-registry"
	poolPath           = "/internal/v1/pool"
)

// recordingNext — следующий обработчик после visibility-обёртки.
func recordingNext(called *bool) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		*called = true
		w.WriteHeader(http.StatusNoContent)
	}
}

// newVisibilityEnv повторяет маршруты server.New, которые используют
// visibility-обёртки карточки (server.go, группы обеих ролей).
func newVisibilityEnv(
	t *testing.T, clients, operator *upstreamStub, users card.Users, next http.HandlerFunc,
) env {
	t.Helper()
	clientsServer := httptest.NewServer(clients)
	operatorServer := httptest.NewServer(operator)
	t.Cleanup(func() {
		clientsServer.Close()
		operatorServer.Close()
	})
	aggregator, err := card.New(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		0,
		clientsServer.URL,
		operatorServer.URL,
		users,
	)
	if err != nil {
		t.Fatal(err)
	}
	router := chi.NewRouter()
	router.Get("/api/v1/clients", aggregator.Registry)
	router.Get("/api/v1/clients/{kcr_code}/snapshots/history", aggregator.VisibleClient(next))
	router.Get("/api/v1/pool", aggregator.Pool(next))
	return env{router: router}
}

// postAs отправляет POST с проверенной идентичностью, как middleware аутентификации.
func publishedPool(codes string) *upstreamStub {
	return &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path != publishedCodesPath {
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
		return http.StatusOK, codes
	}}
}

// unblockedClients отвечает списком разблокированных клиентов.
func unblockedClients(codes string) *upstreamStub {
	return &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path != unblockedCodesPath {
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
		return http.StatusOK, codes
	}}
}

func errorCode(t *testing.T, w *httptest.ResponseRecorder) string {
	t.Helper()
	var payload struct {
		Code string `json:"code"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
		t.Fatalf("response is not a json error: %s", w.Body.String())
	}
	return payload.Code
}

func TestVisibleClientAllowsPublishedUnblockedOperator(t *testing.T) {
	operator := publishedPool(`{"codes":["` + operatorCode + `"]}`)
	clients := unblockedClients(`{"codes":["` + operatorCode + `"]}`)
	called := false
	e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

	w := e.get(t, "/api/v1/clients/"+operatorCode+"/snapshots/history")

	if w.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204; body %s", w.Code, w.Body.String())
	}
	if !called {
		t.Fatal("visible client must reach the next handler")
	}

	publishedCalls := operator.recorded()
	if len(publishedCalls) != 1 {
		t.Fatalf("published calls = %d, want 1", len(publishedCalls))
	}
	if call := publishedCalls[0]; call.method != http.MethodGet || call.path != publishedCodesPath ||
		call.query != "kcr_code="+operatorCode {
		t.Errorf("published call = %s %s?%s", call.method, call.path, call.query)
	}
	if got := publishedCalls[0].header.Get("X-User-Id"); got != operatorID {
		t.Errorf("published X-User-Id = %q", got)
	}

	unblockedCalls := clients.recorded()
	if len(unblockedCalls) != 1 {
		t.Fatalf("unblocked calls = %d, want 1", len(unblockedCalls))
	}
	call := unblockedCalls[0]
	if call.method != http.MethodPost || call.path != unblockedCodesPath || call.query != "" {
		t.Errorf("unblocked call = %s %s?%s", call.method, call.path, call.query)
	}
	if string(call.body) != `{"codes":["`+operatorCode+`"]}` {
		t.Errorf("unblocked body = %s", call.body)
	}
	if got := call.header.Get("Content-Type"); got != "application/json" {
		t.Errorf("unblocked Content-Type = %q", got)
	}
	if got := call.header.Get("X-User-Role"); got != string(auth.RoleOperator) {
		t.Errorf("unblocked X-User-Role = %q", got)
	}
}

func TestVisibleClientHidesClientFromOperator(t *testing.T) {
	tests := []struct {
		name          string
		published     string
		unblocked     string
		wantUnblocked int
	}{
		{
			name:          "client is not published",
			published:     `{"codes":[]}`,
			unblocked:     `{"codes":["` + operatorCode + `"]}`,
			wantUnblocked: 0,
		},
		{
			name:          "client is blocked in clients",
			published:     `{"codes":["` + operatorCode + `"]}`,
			unblocked:     `{"codes":[]}`,
			wantUnblocked: 1,
		},
		{
			name:          "client is filtered out",
			published:     `{"codes":["` + operatorCode + `"]}`,
			unblocked:     `{"codes":["OTHER0001"]}`,
			wantUnblocked: 1,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := publishedPool(test.published)
			clients := unblockedClients(test.unblocked)
			called := false
			e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

			w := e.get(t, "/api/v1/clients/"+operatorCode+"/snapshots/history")

			if w.Code != http.StatusNotFound {
				t.Fatalf("status = %d, want 404; body %s", w.Code, w.Body.String())
			}
			if got := errorCode(t, w); got != "NOT_FOUND" {
				t.Errorf("code = %q, want NOT_FOUND", got)
			}
			if called {
				t.Error("hidden client must not reach the next handler")
			}
			if got := len(clients.recorded()); got != test.wantUnblocked {
				t.Errorf("unblocked calls = %d, want %d", got, test.wantUnblocked)
			}
		})
	}
}

func TestVisibleClientAdminBypassesVisibilityScope(t *testing.T) {
	operator := publishedPool(`{"codes":[]}`)
	clients := unblockedClients(`{"codes":[]}`)
	called := false
	e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

	w := e.getAs(t, "/api/v1/clients/"+operatorCode+"/snapshots/history", auth.RoleAdmin)

	if w.Code != http.StatusNoContent || !called {
		t.Fatalf("status = %d, called = %t; want 204 and next call", w.Code, called)
	}
	if len(operator.recorded()) != 0 || len(clients.recorded()) != 0 {
		t.Errorf("admin must not request visibility scope: operator %v, clients %v",
			operator.recorded(), clients.recorded())
	}
}

func TestVisibleClientRejectsAnonymous(t *testing.T) {
	operator := publishedPool(`{"codes":["` + operatorCode + `"]}`)
	clients := unblockedClients(`{"codes":["` + operatorCode + `"]}`)
	called := false
	e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

	request := httptest.NewRequest(http.MethodGet, "/api/v1/clients/"+operatorCode+"/snapshots/history", http.NoBody)
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, request)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", w.Code)
	}
	if called {
		t.Error("anonymous request must not reach the next handler")
	}
	if len(operator.recorded()) != 0 || len(clients.recorded()) != 0 {
		t.Error("anonymous request must not reach the upstreams")
	}
}

func TestVisibleClientUpstreamFailures(t *testing.T) {
	tests := []struct {
		name       string
		operator   func(recordedCall) (int, string)
		clients    func(recordedCall) (int, string)
		wantStatus int
		wantCode   string
	}{
		{
			name: "published codes unavailable",
			operator: func(recordedCall) (int, string) {
				return http.StatusInternalServerError, `<html>boom</html>`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":[]}` },
			wantStatus: http.StatusServiceUnavailable,
			wantCode:   "SERVICE_UNAVAILABLE",
		},
		{
			name: "published codes are not json",
			operator: func(recordedCall) (int, string) {
				return http.StatusOK, `not-json`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":[]}` },
			wantStatus: http.StatusBadGateway,
			wantCode:   "BAD_GATEWAY",
		},
		{
			name: "published codes are null",
			operator: func(recordedCall) (int, string) {
				return http.StatusOK, `{"codes":null}`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":[]}` },
			wantStatus: http.StatusBadGateway,
			wantCode:   "BAD_GATEWAY",
		},
		{
			name: "published error is passed through",
			operator: func(recordedCall) (int, string) {
				return http.StatusInternalServerError, `{"code":"INTERNAL_ERROR","message":"boom"}`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":[]}` },
			wantStatus: http.StatusInternalServerError,
			wantCode:   "INTERNAL_ERROR",
		},
		{
			name:     "unblocked codes unavailable",
			operator: func(recordedCall) (int, string) { return http.StatusOK, `{"codes":["` + operatorCode + `"]}` },
			clients: func(recordedCall) (int, string) {
				return http.StatusInternalServerError, `<html>boom</html>`
			},
			wantStatus: http.StatusServiceUnavailable,
			wantCode:   "SERVICE_UNAVAILABLE",
		},
		{
			name:       "unblocked codes are not json",
			operator:   func(recordedCall) (int, string) { return http.StatusOK, `{"codes":["` + operatorCode + `"]}` },
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `not-json` },
			wantStatus: http.StatusBadGateway,
			wantCode:   "BAD_GATEWAY",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := &upstreamStub{router: test.operator}
			clients := &upstreamStub{router: test.clients}
			called := false
			e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

			w := e.get(t, "/api/v1/clients/"+operatorCode+"/snapshots/history")

			if w.Code != test.wantStatus {
				t.Fatalf("status = %d, want %d; body %s", w.Code, test.wantStatus, w.Body.String())
			}
			if got := errorCode(t, w); got != test.wantCode {
				t.Errorf("code = %q, want %q", got, test.wantCode)
			}
			if called {
				t.Error("failed visibility scope must not reach the next handler")
			}
		})
	}
}

func TestRegistryScopesOperatorCollection(t *testing.T) {
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		switch call.path {
		case publishedCodesPath:
			return http.StatusOK, `{"codes":["` + operatorCode + `","OTHER"]}`
		case "/internal/v1/clients/current-assignees":
			return http.StatusOK, `{"items":[{"kcr_code":"` + operatorCode + `","assigned_operator_id":"` + operatorID + `"}]}`
		default:
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
	}}
	clients := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path != registryPath {
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
		return http.StatusOK, `{"items":[{"kcr_code":"` + operatorCode + `"}]}`
	}}
	called := false
	users := &fakeUsers{user: identity.User{ID: uuid.MustParse(operatorID), Login: "operator-login"}}
	e := newVisibilityEnv(t, clients, operator, users, recordingNext(&called))

	w := e.get(t, "/api/v1/clients?page=2&page_size=10")

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if called {
		t.Error("scoped collection must answer without the proxy handler")
	}
	if got := strings.TrimSpace(w.Body.String()); got != `{"items":[{"assigned_operator_id":"`+operatorID+`","kcr_code":"`+operatorCode+`","operator_name":"operator-login"}]}` {
		t.Errorf("body = %s, want enriched registry page", got)
	}
	operatorCalls := operator.recorded()
	if len(operatorCalls) != 2 || operatorCalls[1].method != http.MethodPost ||
		string(operatorCalls[1].body) != `{"codes":["`+operatorCode+`"]}` {
		t.Fatalf("expected one assignee request scoped to the page, got %+v", operatorCalls)
	}
	calls := clients.recorded()
	if len(calls) != 1 {
		t.Fatalf("clients calls = %d, want 1", len(calls))
	}
	call := calls[0]
	if call.method != http.MethodPost || call.path != registryPath || call.query != "page=2&page_size=10" {
		t.Errorf("registry call = %s %s?%s", call.method, call.path, call.query)
	}
	if string(call.body) != `{"codes":["`+operatorCode+`","OTHER"]}` {
		t.Errorf("registry body = %s", call.body)
	}
	if got := call.header.Get("X-User-Id"); got != operatorID {
		t.Errorf("registry X-User-Id = %q", got)
	}
}

func TestPoolScopesOperatorCollection(t *testing.T) {
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		switch call.path {
		case publishedCodesPath:
			return http.StatusOK, `{"codes":["` + operatorCode + `"]}`
		case poolPath:
			return http.StatusOK, `{"items":[]}`
		default:
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
	}}
	clients := unblockedClients(`{"codes":["` + operatorCode + `"]}`)
	called := false
	e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

	w := e.get(t, "/api/v1/pool?work_quarter=2026_Q4")

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if called {
		t.Error("scoped collection must answer without the proxy handler")
	}
	calls := operator.recorded()
	if len(calls) != 2 {
		t.Fatalf("operator calls = %d, want published scope and pool", len(calls))
	}
	poolCall := calls[1]
	if poolCall.method != http.MethodPost || poolCall.path != poolPath ||
		poolCall.query != "work_quarter=2026_Q4" {
		t.Errorf("pool call = %s %s?%s", poolCall.method, poolCall.path, poolCall.query)
	}
	if string(poolCall.body) != `{"codes":["`+operatorCode+`"]}` {
		t.Errorf("pool body = %s", poolCall.body)
	}
	if len(clients.recorded()) != 1 {
		t.Errorf("unblocked calls = %d, want 1", len(clients.recorded()))
	}
}

func TestScopedCollectionsBypassForAdmin(t *testing.T) {
	tests := []struct {
		name   string
		target string
	}{
		{name: "pool", target: "/api/v1/pool"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := publishedPool(`{"codes":[]}`)
			clients := unblockedClients(`{"codes":[]}`)
			called := false
			e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

			w := e.getAs(t, test.target, auth.RoleAdmin)

			if w.Code != http.StatusNoContent || !called {
				t.Fatalf("status = %d, called = %t; want 204 and next call", w.Code, called)
			}
			if len(operator.recorded()) != 0 || len(clients.recorded()) != 0 {
				t.Error("admin must not request the visibility scope")
			}
		})
	}
}

func TestRegistryAdminReadsAssigneesWithoutVisibilityScope(t *testing.T) {
	tests := []struct {
		name   string
		status int
		body   string
		want   int
	}{
		{"free client", http.StatusOK, `{"items":[]}`, http.StatusOK},
		{"unavailable", http.StatusServiceUnavailable, `{"code":"SERVICE_UNAVAILABLE"}`, http.StatusBadGateway},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			clients := &upstreamStub{router: func(call recordedCall) (int, string) {
				if call.method != http.MethodGet || call.path != "/internal/v1/clients" {
					return http.StatusNotFound, `{"code":"NOT_FOUND"}`
				}
				return http.StatusOK, `{"items":[{"kcr_code":"A"}],"total":1}`
			}}
			operator := &upstreamStub{router: func(call recordedCall) (int, string) {
				if call.path != "/internal/v1/clients/current-assignees" {
					return http.StatusNotFound, `{"code":"NOT_FOUND"}`
				}
				return test.status, test.body
			}}
			called := false
			e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))
			w := e.getAs(t, "/api/v1/clients", auth.RoleAdmin)
			if w.Code != test.want {
				t.Fatalf("status = %d, want %d; %s", w.Code, test.want, w.Body)
			}
			if got := operator.recorded(); len(got) != 1 || got[0].path != "/internal/v1/clients/current-assignees" {
				t.Fatalf("expected only the assignee request, got %+v", got)
			}
			if test.status == http.StatusOK && strings.TrimSpace(w.Body.String()) !=
				`{"items":[{"assigned_operator_id":null,"kcr_code":"A","operator_name":null}],"total":1}` {
				t.Fatalf("unexpected registry page: %s", w.Body)
			}
		})
	}
}

func TestScopedCollectionsRejectAnonymous(t *testing.T) {
	tests := []struct {
		name   string
		method string
		target string
	}{
		{name: "registry", method: http.MethodGet, target: "/api/v1/clients"},
		{name: "pool", method: http.MethodGet, target: "/api/v1/pool"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := publishedPool(`{"codes":[]}`)
			clients := unblockedClients(`{"codes":[]}`)
			called := false
			e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

			request := httptest.NewRequest(test.method, test.target, http.NoBody)
			w := httptest.NewRecorder()
			e.router.ServeHTTP(w, request)

			if w.Code != http.StatusUnauthorized {
				t.Fatalf("status = %d, want 401", w.Code)
			}
			if called {
				t.Error("anonymous request must not reach the next handler")
			}
			if len(operator.recorded()) != 0 || len(clients.recorded()) != 0 {
				t.Error("anonymous request must not reach the upstreams")
			}
		})
	}
}

func TestScopedCollectionUpstreamFailures(t *testing.T) {
	tests := []struct {
		name       string
		operator   func(recordedCall) (int, string)
		clients    func(recordedCall) (int, string)
		wantStatus int
		wantCode   string
	}{
		{
			name: "visibility scope unavailable",
			operator: func(recordedCall) (int, string) {
				return http.StatusInternalServerError, `<html>boom</html>`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":[]}` },
			wantStatus: http.StatusServiceUnavailable,
			wantCode:   "SERVICE_UNAVAILABLE",
		},
		{
			name: "visibility scope is not json",
			operator: func(recordedCall) (int, string) {
				return http.StatusOK, `not-json`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":[]}` },
			wantStatus: http.StatusBadGateway,
			wantCode:   "BAD_GATEWAY",
		},
		{
			name:     "unblocked scope unavailable",
			operator: func(recordedCall) (int, string) { return http.StatusOK, `{"codes":["` + operatorCode + `"]}` },
			clients: func(recordedCall) (int, string) {
				return http.StatusInternalServerError, `<html>boom</html>`
			},
			wantStatus: http.StatusServiceUnavailable,
			wantCode:   "SERVICE_UNAVAILABLE",
		},
		{
			name: "scoped collection is not json",
			operator: func(call recordedCall) (int, string) {
				if call.path == publishedCodesPath {
					return http.StatusOK, `{"codes":["` + operatorCode + `"]}`
				}
				return http.StatusOK, `not-json`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":["` + operatorCode + `"]}` },
			wantStatus: http.StatusBadGateway,
			wantCode:   "BAD_GATEWAY",
		},
		{
			name: "scoped collection error is passed through",
			operator: func(call recordedCall) (int, string) {
				if call.path == publishedCodesPath {
					return http.StatusOK, `{"codes":["` + operatorCode + `"]}`
				}
				return http.StatusConflict, `{"code":"INVALID_STATE","message":"Цикл обзвона изменился"}`
			},
			clients:    func(recordedCall) (int, string) { return http.StatusOK, `{"codes":[]}` },
			wantStatus: http.StatusConflict,
			wantCode:   "INVALID_STATE",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := &upstreamStub{router: test.operator}
			clients := &upstreamStub{router: test.clients}
			called := false
			e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

			w := e.get(t, "/api/v1/pool")

			if w.Code != test.wantStatus {
				t.Fatalf("status = %d, want %d; body %s", w.Code, test.wantStatus, w.Body.String())
			}
			if got := errorCode(t, w); got != test.wantCode {
				t.Errorf("code = %q, want %q", got, test.wantCode)
			}
			if called {
				t.Error("failed scoped collection must not reach the next handler")
			}
		})
	}
}
