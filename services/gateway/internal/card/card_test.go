package card_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/card"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

const (
	operatorID   = "22222222-2222-2222-2222-222222222222"
	assignmentID = "33333333-3333-3333-3333-333333333333"
	operatorCode = "AAA00412"
)

// recordedCall — зафиксированный запрос к внутреннему сервису.
type recordedCall struct {
	method string
	path   string
	query  string
	header http.Header
	body   []byte
}

// upstreamStub — подмена внутреннего сервиса с настраиваемыми ответами.
type upstreamStub struct {
	mu     sync.Mutex
	calls  []recordedCall
	router func(call recordedCall) (status int, body string)
}

func (s *upstreamStub) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	call := recordedCall{
		method: r.Method,
		path:   r.URL.Path,
		query:  r.URL.RawQuery,
		header: r.Header.Clone(),
		body:   body,
	}
	s.mu.Lock()
	s.calls = append(s.calls, call)
	router := s.router
	s.mu.Unlock()

	status, payload := router(call)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(payload))
}

func (s *upstreamStub) recorded() []recordedCall {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]recordedCall(nil), s.calls...)
}

// fakeUsers — замена identity.Store: только поиск по id.
type fakeUsers struct {
	user identity.User
	err  error
}

func (f *fakeUsers) GetByID(context.Context, uuid.UUID) (identity.User, error) {
	return f.user, f.err
}

func (f *fakeUsers) GetByIDs(context.Context, []uuid.UUID) ([]identity.User, error) {
	return []identity.User{f.user}, f.err
}

type env struct {
	router http.Handler
}

func (e *env) get(t *testing.T, target string) *httptest.ResponseRecorder {
	return e.getAs(t, target, auth.RoleOperator)
}

func (e *env) getAs(t *testing.T, target string, role auth.Role) *httptest.ResponseRecorder {
	t.Helper()
	id := auth.Identity{
		UserID: uuid.MustParse(operatorID),
		Role:   role,
	}
	request := httptest.NewRequest(http.MethodGet, target, http.NoBody)
	request = request.WithContext(auth.WithIdentity(request.Context(), id))
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, request)
	return w
}

func newAggregator(t *testing.T, clients, operator *upstreamStub, users card.Users) env {
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
	// Тот же маршрут, что собирает server.New (server.go, обе роли).
	router := chi.NewRouter()
	router.Get("/api/v1/clients/{kcr_code}", aggregator.Client)
	return env{router: router}
}

// clientsCard — минимальный профиль Clients: рабочих полей в нём нет, их
// добавляет агрегатор (ClientCard в Clients их не отдаёт).
const clientsCard = `{"kcr_code":"` + operatorCode + `","inn":"7701000012","organization_name":"ООО Ромашка",` +
	`"tags":[],"snapshots":[],"quarter_summary":null}`

// operatorState — ответ Operator Work; nil означает свободного клиента.
func operatorState(operator any) string {
	state := map[string]any{
		"kcr_code":                 operatorCode,
		"work_quarter":             "2026_Q4",
		"assignment_id":            nil,
		"assigned_operator_id":     operator,
		"assigned_at":              nil,
		"stage":                    "POOL",
		"wait_until":               nil,
		"wait_source_work_quarter": nil,
		"awaiting_balance":         false,
	}
	if operator != nil {
		state["assignment_id"] = assignmentID
		state["assigned_at"] = "2026-11-17T09:00:00Z"
		state["stage"] = "IN_WORK"
	}
	payload, _ := json.Marshal(state)
	return string(payload)
}

func decodeCard(t *testing.T, body string) map[string]any {
	t.Helper()
	var cardMap map[string]any
	if err := json.Unmarshal([]byte(body), &cardMap); err != nil {
		t.Fatalf("card is not a json object: %s", body)
	}
	return cardMap
}

func TestCardMergesClientsProfileAndWorkState(t *testing.T) {
	clients := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path != "/internal/v1/clients/"+operatorCode {
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
		return http.StatusOK, clientsCard
	}}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path != "/internal/v1/clients/"+operatorCode+"/work-state" {
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
		return http.StatusOK, operatorState(operatorID)
	}}
	users := &fakeUsers{user: identity.User{ID: uuid.MustParse(operatorID), Login: "op.login"}}
	e := newAggregator(t, clients, operator, users)

	w := e.get(t, "/api/v1/clients/"+operatorCode+"?quarter=2026_Q4&snapshots_limit=3")

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	cardBody := decodeCard(t, w.Body.String())
	if cardBody["work_quarter"] != "2026_Q4" {
		t.Errorf("work_quarter = %v, want 2026_Q4", cardBody["work_quarter"])
	}
	if cardBody["current_stage"] != "IN_WORK" {
		t.Errorf("current_stage = %v, want IN_WORK", cardBody["current_stage"])
	}
	if cardBody["organization_name"] != "ООО Ромашка" {
		t.Errorf("profile field lost: %v", cardBody["organization_name"])
	}
	assignment, ok := cardBody["assignment"].(map[string]any)
	if !ok {
		t.Fatalf("assignment = %v, want object", cardBody["assignment"])
	}
	if assignment["operator_name"] != "op.login" || assignment["operator_id"] != operatorID ||
		assignment["work_quarter"] != "2026_Q4" {
		t.Errorf("assignment = %v", assignment)
	}

	// Identity-заголовки проставлены обоим участникам.
	for name, stub := range map[string]*upstreamStub{"clients": clients, "operator-work": operator} {
		call := stub.recorded()[0]
		if got := call.header.Get("X-User-Id"); got != operatorID {
			t.Errorf("%s X-User-Id = %q", name, got)
		}
		if got := call.header.Get("X-User-Role"); got != "OPERATOR" {
			t.Errorf("%s X-User-Role = %q", name, got)
		}
	}
	// Карточке уходят параметры запроса целиком, Operator Work — без них.
	if clientsQuery := clients.recorded()[0].query; clientsQuery != "quarter=2026_Q4&snapshots_limit=3" {
		t.Errorf("clients query = %q", clientsQuery)
	}
	if stateQuery := operator.recorded()[0].query; stateQuery != "" {
		t.Errorf("operator query = %q, want empty", stateQuery)
	}
}

func TestCardServesFreePublishedClientWithoutAssignment(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, operatorState(nil)
	}}
	users := &fakeUsers{user: identity.User{ID: uuid.MustParse(operatorID), Login: "op.login"}}
	e := newAggregator(t, clients, operator, users)

	w := e.get(t, "/api/v1/clients/"+operatorCode)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	cardBody := decodeCard(t, w.Body.String())
	if cardBody["assignment"] != nil || cardBody["assignment_id"] != nil {
		t.Errorf("free client must have no assignment: %v", cardBody)
	}
	if cardBody["work_quarter"] != "2026_Q4" || cardBody["current_stage"] != "POOL" {
		t.Errorf("free published state lost: %v", cardBody)
	}
}

func TestCardTreatsMissingCycleAsEmptyWorkState(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusNotFound, `{"code":"WORK_CYCLE_NOT_FOUND","message":"Действующий цикл обзвона не найден"}`
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	if w := e.get(t, "/api/v1/clients/"+operatorCode); w.Code != http.StatusNotFound {
		t.Fatalf("operator status = %d; body %s", w.Code, w.Body.String())
	}
	w := e.getAs(t, "/api/v1/clients/"+operatorCode, auth.RoleAdmin)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	cardBody := decodeCard(t, w.Body.String())
	for _, field := range []string{"work_quarter", "assignment_id", "current_stage", "assignment", "wait_until"} {
		if value, present := cardBody[field]; value != nil || !present {
			t.Errorf("card[%q] = %v (present %t), want null", field, value, present)
		}
	}
}

func TestCardFailsWith502WhenOperatorWorkUnavailable(t *testing.T) {
	for name, respond := range map[string]func(recordedCall) (int, string){
		"upstream 500": func(recordedCall) (int, string) {
			return http.StatusInternalServerError, `{"code":"INTERNAL_ERROR","message":"boom"}`
		},
		"upstream 403": func(recordedCall) (int, string) {
			return http.StatusForbidden, `{"code":"FORBIDDEN","message":"denied"}`
		},
		"non-json 500": func(recordedCall) (int, string) {
			return http.StatusInternalServerError, `<html>boom</html>`
		},
	} {
		t.Run(name, func(t *testing.T) {
			clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
			operator := &upstreamStub{router: respond}
			e := newAggregator(t, clients, operator, &fakeUsers{})

			w := e.get(t, "/api/v1/clients/"+operatorCode)

			if w.Code != http.StatusBadGateway {
				t.Fatalf("status = %d, want 502; body %s", w.Code, w.Body.String())
			}
			var payload struct{ Code string }
			if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "BAD_GATEWAY" {
				t.Errorf("body = %s, want BAD_GATEWAY", w.Body.String())
			}
		})
	}
}

func TestCardFailsWith502WhenOperatorWorkUnreachable(t *testing.T) {
	closed := httptest.NewServer(http.NotFoundHandler())
	closed.Close() // URL остаётся, соединение отклоняется
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	clientsServer := httptest.NewServer(clients)
	t.Cleanup(clientsServer.Close)
	aggregator, err := card.New(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		0,
		clientsServer.URL,
		closed.URL,
		&fakeUsers{},
	)
	if err != nil {
		t.Fatal(err)
	}
	router := chi.NewRouter()
	router.Get("/api/v1/clients/{kcr_code}", aggregator.Client)

	id := auth.Identity{UserID: uuid.MustParse(operatorID), Role: auth.RoleOperator}
	request := httptest.NewRequest(http.MethodGet, "/api/v1/clients/"+operatorCode, http.NoBody)
	request = request.WithContext(auth.WithIdentity(request.Context(), id))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, request)

	if w.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502; body %s", w.Code, w.Body.String())
	}
}

func TestCardPassesHistoricalCycleConflictThrough(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path == "/internal/v1/pool/published-codes" {
			return http.StatusOK, `{"codes":["` + operatorCode + `"]}`
		}
		if call.query != "work_quarter=2026_Q3" {
			t.Errorf("operator query = %q, want explicit quarter", call.query)
		}
		return http.StatusConflict, `{"code":"INVALID_STATE","message":"Цикл обзвона изменился"}`
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.get(t, "/api/v1/clients/"+operatorCode+"?work_quarter=2026_Q3")

	if w.Code != http.StatusConflict {
		t.Fatalf("status = %d, want 409; body %s", w.Code, w.Body.String())
	}
	var payload struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "INVALID_STATE" {
		t.Errorf("body = %s, want INVALID_STATE", w.Body.String())
	}
}

func TestCardValidatesWorkQuarterLocally(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, operatorState(nil)
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.get(t, "/api/v1/clients/"+operatorCode+"?work_quarter=2026-Q3")

	if w.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400; body %s", w.Code, w.Body.String())
	}
	if calls := operator.recorded(); len(calls) != 0 {
		t.Errorf("operator must not be called: %v", calls)
	}
}

func TestCardPassesClientsErrorsThrough(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusNotFound, `{"code":"CLIENT_NOT_FOUND","message":"Client not found"}`
	}}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, operatorState(nil)
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.getAs(t, "/api/v1/clients/UNKNOWN0001", auth.RoleAdmin)

	if w.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404; body %s", w.Code, w.Body.String())
	}
	var payload struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "CLIENT_NOT_FOUND" {
		t.Errorf("body = %s, want CLIENT_NOT_FOUND", w.Body.String())
	}
}

func TestCardOperatorNameResolution(t *testing.T) {
	tests := []struct {
		name  string
		users *fakeUsers
		want  string
	}{
		{"found", &fakeUsers{user: identity.User{ID: uuid.MustParse(operatorID), Login: "op.login"}}, "op.login"},
		{"deleted", &fakeUsers{err: identity.ErrNotFound}, ""},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
			operator := &upstreamStub{router: func(recordedCall) (int, string) {
				return http.StatusOK, operatorState(operatorID)
			}}
			e := newAggregator(t, clients, operator, test.users)

			w := e.get(t, "/api/v1/clients/"+operatorCode)

			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
			}
			assignment := decodeCard(t, w.Body.String())["assignment"].(map[string]any)
			if assignment["operator_name"] != test.want {
				t.Errorf("operator_name = %v, want %q", assignment["operator_name"], test.want)
			}
		})
	}
}

func TestCardReturns500WhenIdentityLookupFails(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, operatorState(operatorID)
	}}
	users := &fakeUsers{err: errors.New("db down")}
	e := newAggregator(t, clients, operator, users)

	w := e.get(t, "/api/v1/clients/"+operatorCode)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500; body %s", w.Code, w.Body.String())
	}
	var payload struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "INTERNAL_ERROR" {
		t.Errorf("body = %s, want INTERNAL_ERROR", w.Body.String())
	}
}

func TestCardRejectsAnonymousRequest(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, operatorState(nil) }}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	request := httptest.NewRequest(http.MethodGet, "/api/v1/clients/"+operatorCode, http.NoBody)
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, request)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", w.Code)
	}
}

func TestNewRejectsInvalidUpstreamURLs(t *testing.T) {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	tests := []struct {
		name        string
		clientsURL  string
		operatorURL string
	}{
		{"malformed clients url", "http://[::1]:namedport", "http://127.0.0.1:8003"},
		{"malformed operator-work url", "http://127.0.0.1:8001", "ht tp://bad"},
		{"clients without host", "http://", "http://127.0.0.1:8003"},
		{"operator-work without scheme", "127.0.0.1:8003", "http://127.0.0.1:8001"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := card.New(log, 0, test.clientsURL, test.operatorURL, &fakeUsers{}); err == nil {
				t.Fatal("New must reject the url")
			}
		})
	}
}

func TestCardFailsWhenClientsProfileIsNotJSON(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, `<html>boom</html>`
	}}
	operator := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, operatorState(nil) }}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.get(t, "/api/v1/clients/"+operatorCode)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500; body %s", w.Code, w.Body.String())
	}
	var payload struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "INTERNAL_ERROR" {
		t.Errorf("body = %s, want INTERNAL_ERROR", w.Body.String())
	}
}

func TestCardFailsWhenOperatorStateIsNotJSON(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, `not-json`
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.get(t, "/api/v1/clients/"+operatorCode)

	if w.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502; body %s", w.Code, w.Body.String())
	}
	var payload struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "BAD_GATEWAY" {
		t.Errorf("body = %s, want BAD_GATEWAY", w.Body.String())
	}
}

func TestCardRejectsMalformedOperatorID(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, clientsCard }}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		state := map[string]any{
			"kcr_code": operatorCode, "work_quarter": "2026_Q4",
			"assignment_id": assignmentID, "assigned_operator_id": "not-a-uuid",
			"assigned_at": "2026-11-17T09:00:00Z", "stage": "IN_WORK",
			"wait_until": nil, "wait_source_work_quarter": nil, "awaiting_balance": false,
		}
		payload, _ := json.Marshal(state)
		return http.StatusOK, string(payload)
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.get(t, "/api/v1/clients/"+operatorCode)

	if w.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502; body %s", w.Code, w.Body.String())
	}
	var payload struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "BAD_GATEWAY" {
		t.Errorf("body = %s, want BAD_GATEWAY", w.Body.String())
	}
}

func TestCardHidesMissingProfileFromOperator(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusNotFound, `{"code":"CLIENT_NOT_FOUND","message":"Client not found"}`
	}}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, operatorState(nil)
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})
	target := "/api/v1/clients/MISSING0001"

	w := e.get(t, target)

	if w.Code != http.StatusNotFound {
		t.Fatalf("operator status = %d, want 404; body %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "Клиент недоступен") {
		t.Errorf("operator body = %s, want the hidden-client message", w.Body.String())
	}

	// Администратору ошибка Clients уходит как есть.
	w = e.getAs(t, target, auth.RoleAdmin)
	if w.Code != http.StatusNotFound {
		t.Fatalf("admin status = %d, want 404; body %s", w.Code, w.Body.String())
	}
	if got := errorCode(t, w); got != "CLIENT_NOT_FOUND" {
		t.Errorf("admin code = %q, want CLIENT_NOT_FOUND", got)
	}
}

func TestCardHidesHistoricalConflictFromOperatorWithoutPublishedAccess(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, clientsCard
	}}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path == publishedCodesPath {
			return http.StatusOK, `{"codes":[]}`
		}
		return http.StatusConflict, `{"code":"INVALID_STATE","message":"Цикл обзвона изменился"}`
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})
	target := "/api/v1/clients/" + operatorCode + "?work_quarter=2026_Q3"

	if w := e.get(t, target); w.Code != http.StatusNotFound {
		t.Fatalf("operator status = %d, want 404; body %s", w.Code, w.Body.String())
	}

	// Администратор получает конфликт исторического цикла как есть.
	w := e.getAs(t, target, auth.RoleAdmin)
	if w.Code != http.StatusConflict {
		t.Fatalf("admin status = %d, want 409; body %s", w.Code, w.Body.String())
	}
	if got := errorCode(t, w); got != "INVALID_STATE" {
		t.Errorf("admin code = %q, want INVALID_STATE", got)
	}
}

func TestCardPropagatesVisibilityFailureOnHistoricalConflict(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, clientsCard
	}}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.path == publishedCodesPath {
			return http.StatusInternalServerError, `<html>boom</html>`
		}
		return http.StatusConflict, `{"code":"INVALID_STATE","message":"Цикл обзвона изменился"}`
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.get(t, "/api/v1/clients/"+operatorCode+"?work_quarter=2026_Q3")

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503; body %s", w.Code, w.Body.String())
	}
	if got := errorCode(t, w); got != "SERVICE_UNAVAILABLE" {
		t.Errorf("code = %q, want SERVICE_UNAVAILABLE", got)
	}
}

func TestCardMapsUnprocessableProfileToValidationError(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusUnprocessableEntity, `<html>invalid</html>`
	}}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, operatorState(nil)
	}}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.getAs(t, "/api/v1/clients/"+operatorCode, auth.RoleAdmin)

	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want 422; body %s", w.Code, w.Body.String())
	}
	if got := errorCode(t, w); got != "VALIDATION_ERROR" {
		t.Errorf("code = %q, want VALIDATION_ERROR", got)
	}
}

func TestCardPassesClientsServerErrorsThrough(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusInternalServerError, `{"code":"INTERNAL_ERROR","message":"boom"}`
	}}
	operator := &upstreamStub{router: func(recordedCall) (int, string) { return http.StatusOK, operatorState(nil) }}
	e := newAggregator(t, clients, operator, &fakeUsers{})

	w := e.get(t, "/api/v1/clients/"+operatorCode)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500 passthrough; body %s", w.Code, w.Body.String())
	}
	var payload struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil || payload.Code != "INTERNAL_ERROR" {
		t.Errorf("body = %s, want INTERNAL_ERROR", w.Body.String())
	}
}
