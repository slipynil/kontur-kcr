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
	publishedCodesPath  = "/internal/v1/pool/published-codes"
	registeredCodesPath = "/internal/v1/clients/registered-codes"
	registryPath        = "/internal/v1/clients/operator-registry"
	poolPath            = "/internal/v1/pool"
	notificationsPath   = "/internal/v1/reassignment-notifications"
	rejectID            = "11111111-1111-1111-1111-111111111111"
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
	router.Get("/api/v1/reassignment-notifications", aggregator.Notifications(next))
	router.Post("/api/v1/reassignment-notifications/{request_id}/reject", aggregator.Reject(next))
	return env{router: router}
}

// postAs отправляет POST с проверенной идентичностью, как middleware аутентификации.
func (e *env) postAs(t *testing.T, target string, role auth.Role) *httptest.ResponseRecorder {
	t.Helper()
	id := auth.Identity{UserID: uuid.MustParse(operatorID), Role: role}
	request := httptest.NewRequest(http.MethodPost, target, http.NoBody)
	request = request.WithContext(auth.WithIdentity(request.Context(), id))
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, request)
	return w
}

// publishedPool отвечает списком опубликованных клиентов.
func publishedPool(codes string) *upstreamStub {
	return &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path != publishedCodesPath {
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
		return http.StatusOK, codes
	}}
}

// registeredClients отвечает кодами клиентов, которые есть в реестре.
func registeredClients(codes string) *upstreamStub {
	return &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path != registeredCodesPath {
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

func TestVisibleClientAllowsPublishedRegisteredOperator(t *testing.T) {
	operator := publishedPool(`{"codes":["` + operatorCode + `"]}`)
	clients := registeredClients(`{"codes":["` + operatorCode + `"]}`)
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

	registeredCalls := clients.recorded()
	if len(registeredCalls) != 1 {
		t.Fatalf("registered calls = %d, want 1", len(registeredCalls))
	}
	call := registeredCalls[0]
	if call.method != http.MethodPost || call.path != registeredCodesPath || call.query != "" {
		t.Errorf("registered call = %s %s?%s", call.method, call.path, call.query)
	}
	if string(call.body) != `{"codes":["`+operatorCode+`"]}` {
		t.Errorf("registered body = %s", call.body)
	}
	if got := call.header.Get("Content-Type"); got != "application/json" {
		t.Errorf("registered Content-Type = %q", got)
	}
	if got := call.header.Get("X-User-Role"); got != string(auth.RoleOperator) {
		t.Errorf("registered X-User-Role = %q", got)
	}
}

func TestVisibleClientHidesClientFromOperator(t *testing.T) {
	tests := []struct {
		name           string
		published      string
		registered     string
		wantRegistered int
	}{
		{
			name:           "client is not published",
			published:      `{"codes":[]}`,
			registered:     `{"codes":["` + operatorCode + `"]}`,
			wantRegistered: 0,
		},
		{
			name:           "published client has no profile",
			published:      `{"codes":["` + operatorCode + `"]}`,
			registered:     `{"codes":[]}`,
			wantRegistered: 1,
		},
		{
			name:           "clients returned a different code",
			published:      `{"codes":["` + operatorCode + `"]}`,
			registered:     `{"codes":["OTHER0001"]}`,
			wantRegistered: 1,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := publishedPool(test.published)
			clients := registeredClients(test.registered)
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
			if got := len(clients.recorded()); got != test.wantRegistered {
				t.Errorf("registered calls = %d, want %d", got, test.wantRegistered)
			}
		})
	}
}

func TestVisibleClientAdminBypassesVisibilityScope(t *testing.T) {
	operator := publishedPool(`{"codes":[]}`)
	clients := registeredClients(`{"codes":[]}`)
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
	clients := registeredClients(`{"codes":["` + operatorCode + `"]}`)
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
			name:     "registered codes unavailable",
			operator: func(recordedCall) (int, string) { return http.StatusOK, `{"codes":["` + operatorCode + `"]}` },
			clients: func(recordedCall) (int, string) {
				return http.StatusInternalServerError, `<html>boom</html>`
			},
			wantStatus: http.StatusServiceUnavailable,
			wantCode:   "SERVICE_UNAVAILABLE",
		},
		{
			name:       "registered codes are not json",
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
	clients := registeredClients(`{"codes":["` + operatorCode + `"]}`)
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
		t.Errorf("registered calls = %d, want 1", len(clients.recorded()))
	}
}

func TestNotificationsScopesOperatorCollection(t *testing.T) {
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		switch call.path {
		case publishedCodesPath:
			return http.StatusOK, `{"codes":["` + operatorCode + `"]}`
		case notificationsPath:
			return http.StatusOK, `{"items":[]}`
		default:
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
	}}
	clients := registeredClients(`{"codes":["` + operatorCode + `"]}`)
	called := false
	e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

	w := e.get(t, "/api/v1/reassignment-notifications?page=1")

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if called {
		t.Error("scoped collection must answer without the proxy handler")
	}
	calls := operator.recorded()
	if len(calls) != 2 {
		t.Fatalf("operator calls = %d, want published scope and notifications", len(calls))
	}
	notificationsCall := calls[1]
	if notificationsCall.method != http.MethodPost || notificationsCall.path != notificationsPath ||
		notificationsCall.query != "page=1" {
		t.Errorf("notifications call = %s %s?%s",
			notificationsCall.method, notificationsCall.path, notificationsCall.query)
	}
	if string(notificationsCall.body) != `{"codes":["`+operatorCode+`"]}` {
		t.Errorf("notifications body = %s", notificationsCall.body)
	}
}

func TestRejectScopesNotification(t *testing.T) {
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		switch call.path {
		case publishedCodesPath:
			return http.StatusOK, `{"codes":["` + operatorCode + `"]}`
		case notificationsPath + "/" + rejectID + "/reject":
			if call.method != http.MethodPost {
				return http.StatusMethodNotAllowed, `{"code":"NOT_FOUND","message":"no route"}`
			}
			return http.StatusOK, `{"status":"REJECTED"}`
		default:
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
	}}
	clients := registeredClients(`{"codes":["` + operatorCode + `"]}`)
	called := false
	e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

	w := e.postAs(t, "/api/v1/reassignment-notifications/"+rejectID+"/reject", auth.RoleOperator)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if called {
		t.Error("reject must answer without the proxy handler")
	}
	calls := operator.recorded()
	if len(calls) != 2 {
		t.Fatalf("operator calls = %d, want published scope and reject", len(calls))
	}
	rejectCall := calls[1]
	if rejectCall.path != notificationsPath+"/"+rejectID+"/reject" {
		t.Errorf("reject path = %q", rejectCall.path)
	}
	if string(rejectCall.body) != `{"codes":["`+operatorCode+`"]}` {
		t.Errorf("reject body = %s", rejectCall.body)
	}
}

func TestRejectValidatesRequestID(t *testing.T) {
	operator := publishedPool(`{"codes":[]}`)
	clients := registeredClients(`{"codes":[]}`)
	called := false
	e := newVisibilityEnv(t, clients, operator, &fakeUsers{}, recordingNext(&called))

	w := e.postAs(t, "/api/v1/reassignment-notifications/not-a-uuid/reject", auth.RoleOperator)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400; body %s", w.Code, w.Body.String())
	}
	if called {
		t.Error("invalid request id must not reach the next handler")
	}
	if len(operator.recorded()) != 0 || len(clients.recorded()) != 0 {
		t.Error("invalid request id must not reach the upstreams")
	}
}

func TestScopedCollectionsBypassForAdmin(t *testing.T) {
	tests := []struct {
		name   string
		target string
	}{
		{name: "pool", target: "/api/v1/pool"},
		{name: "notifications", target: "/api/v1/reassignment-notifications"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := publishedPool(`{"codes":[]}`)
			clients := registeredClients(`{"codes":[]}`)
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
		{name: "notifications", method: http.MethodGet, target: "/api/v1/reassignment-notifications"},
		{
			name:   "reject notification",
			method: http.MethodPost,
			target: "/api/v1/reassignment-notifications/" + rejectID + "/reject",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			operator := publishedPool(`{"codes":[]}`)
			clients := registeredClients(`{"codes":[]}`)
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
			name:     "registered scope unavailable",
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
