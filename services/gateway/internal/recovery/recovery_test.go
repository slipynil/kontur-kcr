package recovery_test

import (
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

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/recovery"
)

const adminID = "11111111-1111-1111-1111-111111111111"

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

func newCoordinator(t *testing.T, clients, operator *upstreamStub) *CoordinatorEnv {
	t.Helper()
	clientsServer := httptest.NewServer(clients)
	operatorServer := httptest.NewServer(operator)
	t.Cleanup(func() {
		clientsServer.Close()
		operatorServer.Close()
	})
	coordinator, err := recovery.New(
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		2*time.Second,
		clientsServer.URL,
		operatorServer.URL,
	)
	if err != nil {
		t.Fatal(err)
	}
	// Те же маршруты, что собирает server.New (server.go, группа ADMIN).
	router := chi.NewRouter()
	router.Delete("/api/v1/clients/{kcr_code}", coordinator.DeleteClient)
	router.Delete("/api/v1/clients/{kcr_code}/snapshots/{quarter}", coordinator.DeleteQuarter)
	return &CoordinatorEnv{router: router}
}

type CoordinatorEnv struct {
	router http.Handler
}

func (e *CoordinatorEnv) do(t *testing.T, method, target, body string) *httptest.ResponseRecorder {
	t.Helper()
	identity := auth.Identity{
		UserID: uuid.MustParse(adminID),
		Role:   auth.RoleAdmin,
	}
	request := httptest.NewRequest(method, target, strings.NewReader(body))
	request = request.WithContext(auth.WithIdentity(request.Context(), identity))
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, request)
	return w
}

// clientsOK отвечает успехом локального удаления Clients.
func clientsOK(call recordedCall) (status int, body string) {
	if call.method != http.MethodDelete {
		return http.StatusMethodNotAllowed, `{"code":"INVALID_REQUEST","message":"Invalid request"}`
	}
	return http.StatusOK, `{}` // локальный результат Clients с нулевыми счётчиками
}

func TestDeleteClientCoordinatesBothServices(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.method != http.MethodPost || call.path != "/internal/v1/data-cleanup" {
			return http.StatusNotFound, `{"code":"NOT_FOUND","message":"no route"}`
		}
		return http.StatusOK, `{"status":"COMPLETED"}`
	}}
	env := newCoordinator(t, clients, operator)

	operationID := uuid.NewString()
	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042", fmt.Sprintf(`{"operation_id":%q}`, operationID))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if len(clients.recorded()) != 1 {
		t.Fatalf("clients calls = %d, want 1", len(clients.recorded()))
	}
	clientCall := clients.recorded()[0]
	if clientCall.path != "/internal/v1/clients/6671-0042" {
		t.Errorf("clients path = %q", clientCall.path)
	}
	var clientsBody struct {
		OperationID string `json:"operation_id"`
		RequestedBy string `json:"requested_by"`
	}
	if err := json.Unmarshal(clientCall.body, &clientsBody); err != nil {
		t.Fatal(err)
	}
	if clientsBody.OperationID != operationID {
		t.Errorf("operation_id = %q, want %q", clientsBody.OperationID, operationID)
	}
	// requested_by Gateway берёт из проверенного контекста, а не из тела запроса.
	if clientsBody.RequestedBy != adminID {
		t.Errorf("requested_by = %q, want %q", clientsBody.RequestedBy, adminID)
	}
	if got := clientCall.header.Get("X-User-Role"); got != "ADMIN" {
		t.Errorf("clients X-User-Role = %q, want ADMIN", got)
	}

	operatorCalls := operator.recorded()
	if len(operatorCalls) != 1 {
		t.Fatalf("operator calls = %d, want 1", len(operatorCalls))
	}
	var cleanupBody struct {
		OperationID string `json:"operation_id"`
		KcrCode     string `json:"kcr_code"`
		Scope       string `json:"scope"`
	}
	if err := json.Unmarshal(operatorCalls[0].body, &cleanupBody); err != nil {
		t.Fatal(err)
	}
	if cleanupBody.OperationID != operationID {
		t.Errorf("cleanup operation_id = %q, want %q", cleanupBody.OperationID, operationID)
	}
	if cleanupBody.KcrCode != "6671-0042" || cleanupBody.Scope != "CLIENT" {
		t.Errorf("cleanup body = %s", operatorCalls[0].body)
	}
	if strings.Contains(string(operatorCalls[0].body), "quarter") {
		t.Errorf("client cleanup must not carry quarter targets: %s", operatorCalls[0].body)
	}
}

// eventsPage строит страницу ленты Operator Work с суммарным total.
func eventsPage(items []map[string]any, total int) string {
	payload, _ := json.Marshal(map[string]any{
		"items": items, "total": total, "page": 1, "page_size": 100,
	})
	return string(payload)
}

// eventsResponse строит страницу ленты Operator Work из привязок document_quarters.
func eventsResponse(quarters ...[]string) string {
	items := make([]map[string]any, 0, len(quarters))
	for _, documentQuarters := range quarters {
		items = append(items, map[string]any{
			"id":                uuid.NewString(),
			"event_type":        "HISTORICAL_COMMENT",
			"document_quarters": documentQuarters,
		})
	}
	return eventsPage(items, len(items))
}

func TestDeleteQuarterCollectsSingleBoundEvents(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.method == http.MethodGet {
			// Страница ленты: одно событие только удаляемого квартала, одно общее,
			// одно другого квартала.
			return http.StatusOK, eventsResponse(
				[]string{"2026_Q3"},
				[]string{"2026_Q3", "2026_Q4"},
				[]string{"2026_Q4"},
			)
		}
		return http.StatusOK, `{"status":"COMPLETED"}`
	}}
	env := newCoordinator(t, clients, operator)

	operationID := uuid.NewString()
	w := env.do(
		t,
		http.MethodDelete,
		"/api/v1/clients/6671-0042/snapshots/2026_Q3",
		fmt.Sprintf(`{"operation_id":%q}`, operationID),
	)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	operatorCalls := operator.recorded()
	if len(operatorCalls) != 2 {
		t.Fatalf("operator calls = %d (%v), want events page then cleanup", len(operatorCalls), operatorCalls)
	}
	if operatorCalls[0].query != "page=1&page_size=100" {
		t.Errorf("events query = %q", operatorCalls[0].query)
	}
	var cleanupBody struct {
		Scope    string   `json:"scope"`
		Quarter  string   `json:"quarter"`
		EventIDs []string `json:"event_ids"`
		CycleIDs []string `json:"cycle_ids"`
	}
	if err := json.Unmarshal(operatorCalls[1].body, &cleanupBody); err != nil {
		t.Fatal(err)
	}
	if cleanupBody.Scope != "QUARTER" || cleanupBody.Quarter != "2026_Q3" {
		t.Errorf("cleanup body = %s", operatorCalls[1].body)
	}
	if len(cleanupBody.EventIDs) != 1 {
		t.Errorf("event_ids = %v, want only the single-bound event", cleanupBody.EventIDs)
	}
	if len(cleanupBody.CycleIDs) != 0 {
		t.Errorf("cycle_ids = %v, want none: document quarter does not own call cycles", cleanupBody.CycleIDs)
	}
}

func TestDeleteQuarterWithoutTargetsSkipsCleanup(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.method == http.MethodGet {
			return http.StatusOK, eventsResponse([]string{"2026_Q4"}, nil)
		}
		t.Error("cleanup must not be called without targets")
		return http.StatusInternalServerError, `{}`
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042/snapshots/2026_Q3", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if len(operator.recorded()) != 1 {
		t.Fatalf("operator calls = %d, want only the events page", len(operator.recorded()))
	}
}

func TestDeleteQuarterMissingOperatorWorkSucceeds(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	operator := &upstreamStub{router: func(_ recordedCall) (int, string) {
		// Клиент без работы: лента отсутствует целиком.
		return http.StatusNotFound, `{"code":"CLIENT_NOT_FOUND","message":"Клиент не найден"}`
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042/snapshots/2026_Q3", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if len(operator.recorded()) != 1 {
		t.Fatalf("operator calls = %d, want only the events page", len(operator.recorded()))
	}
}

func TestDeleteQuarterPaginatesEventFeed(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	firstPage := make([]map[string]any, 0, 100)
	for range 100 {
		firstPage = append(firstPage, map[string]any{
			"id":                uuid.NewString(),
			"event_type":        "HISTORICAL_COMMENT",
			"document_quarters": []string{"2026_Q1"},
		})
	}
	// total — суммарное число событий ленты, одинаковое на каждой странице.
	pages := map[string]string{
		"page=1&page_size=100": eventsPage(firstPage, 101),
		"page=2&page_size=100": eventsResponse([]string{"2026_Q3"}),
	}
	cleanupBodies := []string{}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.method == http.MethodGet {
			body, ok := pages[call.query]
			if !ok {
				return http.StatusInternalServerError, `{}`
			}
			return http.StatusOK, body
		}
		cleanupBodies = append(cleanupBodies, string(call.body))
		return http.StatusOK, `{"status":"COMPLETED"}`
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042/snapshots/2026_Q3", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if len(cleanupBodies) != 1 {
		t.Fatalf("cleanup calls = %d, want 1", len(cleanupBodies))
	}
	var cleanupBody struct {
		EventIDs []string `json:"event_ids"`
	}
	if err := json.Unmarshal([]byte(cleanupBodies[0]), &cleanupBody); err != nil {
		t.Fatal(err)
	}
	if len(cleanupBody.EventIDs) != 1 {
		t.Errorf("event_ids = %v, want only the target from the second page", cleanupBody.EventIDs)
	}
}

func TestClientsConflictPassesThrough(t *testing.T) {
	clients := &upstreamStub{router: func(_ recordedCall) (int, string) {
		return http.StatusConflict, `{"code":"OPERATION_PAYLOAD_CONFLICT","message":"Recovery state conflict"}`
	}}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		t.Error("operator work must not be called after a clients conflict")
		return http.StatusOK, `{}`
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusConflict {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "OPERATION_PAYLOAD_CONFLICT") {
		t.Errorf("body = %q, want the upstream conflict code", w.Body.String())
	}
	if len(operator.recorded()) != 0 {
		t.Errorf("operator calls = %d, want 0", len(operator.recorded()))
	}
}

func TestCleanupConflictRetriesWithNewOperationID(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	var bodies []string
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.method == http.MethodGet {
			return http.StatusOK, eventsResponse([]string{"2026_Q3"})
		}
		bodies = append(bodies, string(call.body))
		if len(bodies) == 1 {
			// Цели очистки изменились с прошлой попытки: контракт требует новый ID.
			return http.StatusConflict, `{"code":"OPERATION_ID_CONFLICT","message":"ID операции уже используется"}`
		}
		return http.StatusOK, `{"status":"COMPLETED"}`
	}}
	env := newCoordinator(t, clients, operator)

	operationID := uuid.NewString()
	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042/snapshots/2026_Q3", `{"operation_id":"`+operationID+`"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if len(bodies) != 2 {
		t.Fatalf("cleanup attempts = %d, want 2", len(bodies))
	}
	var first struct {
		OperationID string `json:"operation_id"`
	}
	if err := json.Unmarshal([]byte(bodies[0]), &first); err != nil {
		t.Fatal(err)
	}
	if first.OperationID != operationID {
		t.Errorf("first attempt operation_id = %q, want %q", first.OperationID, operationID)
	}
	var second struct {
		OperationID string `json:"operation_id"`
	}
	if err := json.Unmarshal([]byte(bodies[1]), &second); err != nil {
		t.Fatal(err)
	}
	if second.OperationID == operationID || second.OperationID == "" {
		t.Errorf("retry operation_id = %q, want a fresh id", second.OperationID)
	}
}

func TestClientsUnavailableReturns503(t *testing.T) {
	clients := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusInternalServerError, `{"code":"SERVICE_UNAVAILABLE","message":"Try again later"}`
	}}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		t.Error("operator work must not be called when clients fails")
		return http.StatusOK, `{}`
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	if len(operator.recorded()) != 0 {
		t.Errorf("operator calls = %d, want 0", len(operator.recorded()))
	}
}

func TestOperatorCleanupUnavailableReturns503(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	operator := &upstreamStub{router: func(call recordedCall) (int, string) {
		if call.method == http.MethodGet {
			return http.StatusOK, eventsResponse([]string{"2026_Q3"})
		}
		return http.StatusInternalServerError, `{}`
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042/snapshots/2026_Q3", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
}

func TestNewRejectsInvalidUpstreamURLs(t *testing.T) {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))

	if _, err := recovery.New(log, time.Second, "http://[::1", "http://operator"); err == nil {
		t.Error("unparsable clients url must fail")
	}
	// url.Parse принимает "clients" как относительный путь: нет схемы и хоста.
	if _, err := recovery.New(log, time.Second, "clients", "http://operator"); err == nil {
		t.Error("relative clients url must fail")
	}
	if _, err := recovery.New(log, time.Second, "http://clients", "operator"); err == nil {
		t.Error("relative operator url must fail")
	}
}

func TestClientsValidationErrorMapsTo400(t *testing.T) {
	clients := &upstreamStub{router: func(_ recordedCall) (int, string) {
		return http.StatusUnprocessableEntity, `{"code":"UNEXPECTED_SNAPSHOT","message":"Снимок не ожидался"}`
	}}
	operator := &upstreamStub{router: func(_ recordedCall) (int, string) {
		t.Error("operator work must not be called when clients rejects the command")
		return http.StatusOK, `{}`
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 for upstream 422", w.Code)
	}
	if !strings.Contains(w.Body.String(), "UNEXPECTED_SNAPSHOT") {
		t.Errorf("body = %q, want the upstream code", w.Body.String())
	}
	if len(operator.recorded()) != 0 {
		t.Errorf("operator calls = %d, want 0", len(operator.recorded()))
	}
}

func TestMissingIdentityReturns401(t *testing.T) {
	env := newCoordinator(t, &upstreamStub{router: clientsOK}, &upstreamStub{router: func(_ recordedCall) (int, string) {
		return http.StatusOK, `{}`
	}})

	// Запрос без идентичности в контексте: middleware аутентификации не выполнялся.
	request := httptest.NewRequest(http.MethodDelete, "/api/v1/clients/6671-0042",
		strings.NewReader(`{"operation_id":"`+uuid.NewString()+`"}`))
	w := httptest.NewRecorder()
	env.router.ServeHTTP(w, request)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", w.Code)
	}
}

func TestUpstreamConnectionFailureReturns503(t *testing.T) {
	// Сервис недоступен целиком: транспортный сбой до ответа.
	server := httptest.NewServer(&upstreamStub{router: func(_ recordedCall) (int, string) {
		return http.StatusOK, `{}`
	}})
	coordinator, err := recovery.New(
		slog.New(slog.NewTextHandler(io.Discard, nil)), 2*time.Second, server.URL, server.URL,
	)
	if err != nil {
		t.Fatal(err)
	}
	server.Close()

	request := httptest.NewRequest(http.MethodDelete, "/api/v1/clients/6671-0042",
		strings.NewReader(`{"operation_id":"`+uuid.NewString()+`"}`))
	request = request.WithContext(auth.WithIdentity(request.Context(), auth.Identity{
		UserID: uuid.MustParse(adminID),
		Role:   auth.RoleAdmin,
	}))
	w := httptest.NewRecorder()
	coordinator.DeleteClient(w, request)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", w.Code)
	}
}

func TestOversizedEventFeedReturns503(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	// Лента длиннее контрактного лимита целей: каждая страница полная, total больше
	// вместимости 100 страниц по 100 записей — автоматическая очистка невозможна.
	items := make([]map[string]any, 100)
	for index := range items {
		items[index] = map[string]any{
			"id":                uuid.NewString(),
			"event_type":        "HISTORICAL_COMMENT",
			"document_quarters": []string{"2026_Q3"},
		}
	}
	fullPage := eventsPage(items, 10001)
	operator := &upstreamStub{router: func(_ recordedCall) (int, string) {
		return http.StatusOK, fullPage
	}}
	env := newCoordinator(t, clients, operator)

	w := env.do(t, http.MethodDelete, "/api/v1/clients/6671-0042/snapshots/2026_Q3", `{"operation_id":"`+uuid.NewString()+`"}`)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 for an oversized feed", w.Code)
	}
	if !strings.Contains(w.Body.String(), "Слишком много") {
		t.Errorf("body = %q, want the oversized-feed message", w.Body.String())
	}
}

func TestInvalidCommandReturns400(t *testing.T) {
	env := newCoordinator(t, &upstreamStub{router: clientsOK}, &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, `{}`
	}})

	tests := []struct {
		name   string
		method string
		target string
		body   string
	}{
		{name: "missing operation id", method: http.MethodDelete, target: "/api/v1/clients/6671-0042", body: "{}"},
		{name: "malformed operation id", method: http.MethodDelete, target: "/api/v1/clients/6671-0042", body: `{"operation_id":"oops"}`},
		{name: "empty body", method: http.MethodDelete, target: "/api/v1/clients/6671-0042", body: ""},
		{name: "bad quarter", method: http.MethodDelete, target: "/api/v1/clients/6671-0042/snapshots/2026_Q5", body: `{"operation_id":"` + uuid.NewString() + `"}`},
		{name: "unknown field", method: http.MethodDelete, target: "/api/v1/clients/6671-0042", body: `{"operation_id":"` + uuid.NewString() + `","surprise":1}`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := env.do(t, tt.method, tt.target, tt.body)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestClientSuppliedRequestedByIsIgnored(t *testing.T) {
	clients := &upstreamStub{router: clientsOK}
	operator := &upstreamStub{router: func(recordedCall) (int, string) {
		return http.StatusOK, `{"status":"COMPLETED"}`
	}}
	env := newCoordinator(t, clients, operator)

	// Подделанный requested_by не проходит: Gateway берёт его из проверенного контекста.
	w := env.do(
		t,
		http.MethodDelete,
		"/api/v1/clients/6671-0042",
		`{"operation_id":"`+uuid.NewString()+`","requested_by":"22222222-2222-2222-2222-222222222222"}`,
	)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", w.Code, w.Body.String())
	}
	var clientsBody struct {
		RequestedBy string `json:"requested_by"`
	}
	if err := json.Unmarshal(clients.recorded()[0].body, &clientsBody); err != nil {
		t.Fatal(err)
	}
	if clientsBody.RequestedBy != adminID {
		t.Errorf("requested_by = %q, want the verified identity %q", clientsBody.RequestedBy, adminID)
	}
}
