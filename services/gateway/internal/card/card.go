// Package card собирает публичную карточку клиента из трёх источников: профиль
// и срезы — Clients, рабочее состояние цикла обзвона — Operator Work, имя
// ответственного — Identity DB (spec.md §3.6; docs/api/public-api.yaml getClientCard).
package card

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"regexp"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
	"github.com/slipynil/kontur-kcr/services/identity/internal/proxy"
)

const (
	// Внутренние маршруты участников (docs/api/internal/*.yaml).
	clientsCardEndpoint   = "/internal/v1/clients/%s"
	operatorStateEndpoint = "/internal/v1/clients/%s/work-state"

	// workQuarterParam — квартал цикла обзвона: выбирает цикл и для карточки Clients,
	// и для состояния Operator Work.
	workQuarterParam = "work_quarter"
)

// quarterPattern — формат квартала (docs/api/public-api.yaml).
var quarterPattern = regexp.MustCompile(`^\d{4}_Q[1-4]$`)

// Users находит логин по идентификатору; identity.Store удовлетворяет интерфейсу.
type Users interface {
	GetByID(ctx context.Context, id uuid.UUID) (identity.User, error)
}

// Aggregator агрегирует карточку вместо прозрачного проксирования.
type Aggregator struct {
	log         *slog.Logger
	client      *http.Client
	clientsURL  *url.URL
	operatorURL *url.URL
	users       Users
}

// New создаёт агрегатор; некорректный URL — ошибка запуска.
func New(log *slog.Logger, timeout time.Duration, clientsURL, operatorURL string, users Users) (*Aggregator, error) {
	clients, err := url.Parse(clientsURL)
	if err != nil {
		return nil, fmt.Errorf("parse clients upstream url %q: %w", clientsURL, err)
	}
	operator, err := url.Parse(operatorURL)
	if err != nil {
		return nil, fmt.Errorf("parse operator-work upstream url %q: %w", operatorURL, err)
	}
	if clients.Scheme == "" || clients.Host == "" || operator.Scheme == "" || operator.Host == "" {
		return nil, errors.New("invalid card upstream url: scheme and host are required")
	}
	return &Aggregator{
		log:         log,
		client:      &http.Client{Timeout: timeout},
		clientsURL:  clients,
		operatorURL: operator,
		users:       users,
	}, nil
}

// upstreamFailure — ошибка внутреннего сервиса; статус, код и сообщение уходят
// наружу как есть (единый формат ошибок public-api.yaml).
type upstreamFailure struct {
	status  int
	code    string
	message string
}

func unavailable() *upstreamFailure {
	return &upstreamFailure{
		status:  http.StatusServiceUnavailable,
		code:    httpx.CodeServiceUnavailable,
		message: "Сервис временно недоступен, повторите позже",
	}
}

// workState — рабочее состояние цикла Operator Work (AssignmentState);
// строки проходят насквозь без перезаписи форматов.
type workState struct {
	WorkQuarter           string  `json:"work_quarter"`
	AssignmentID          *string `json:"assignment_id"`
	AssignedOperatorID    *string `json:"assigned_operator_id"`
	AssignedAt            *string `json:"assigned_at"`
	Stage                 *string `json:"stage"`
	WaitUntil             *string `json:"wait_until"`
	WaitSourceWorkQuarter *string `json:"wait_source_work_quarter"`
}

// Client отдаёт агрегированную карточку: GET /api/v1/clients/{kcr_code}.
// Clients — источник карточки; его ошибки уходят наружу как есть, как при проксировании.
// Operator Work: 404 скрывает карточку у оператора, администратор получает
// карточку с пустыми рабочими полями.
// (решение по ADR-0012, docs/api/public-api.yaml getClientCard).
func (a *Aggregator) Client(w http.ResponseWriter, r *http.Request) {
	who, ok := auth.FromContext(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
		return
	}
	code := chi.URLParam(r, "kcr_code")

	quarter := r.URL.Query().Get(workQuarterParam)
	if quarter != "" && !quarterPattern.MatchString(quarter) {
		httpx.WriteValidationError(w)
		return
	}

	type fetch struct {
		body    []byte
		failure *upstreamFailure
	}
	cardCh := make(chan fetch, 1)
	stateCh := make(chan fetch, 1)
	go func() {
		body, failure := a.fetchCard(r, who, code)
		cardCh <- fetch{body: body, failure: failure}
	}()
	go func() {
		body, failure := a.fetchState(r, who, code, quarter)
		stateCh <- fetch{body: body, failure: failure}
	}()
	cardResult, stateResult := <-cardCh, <-stateCh

	if cardResult.failure != nil {
		if who.Role == auth.RoleOperator && cardResult.failure.status == http.StatusNotFound {
			hiddenClient(w)
			return
		}
		a.respondUpstreamFailure(w, cardResult.failure, "clients")
		return
	}
	var card map[string]any
	if err := json.Unmarshal(cardResult.body, &card); err != nil {
		a.log.Error("clients card is not a json object", "error", err)
		httpx.WriteInternalError(w)
		return
	}
	if who.Role == auth.RoleOperator && card["is_blocked"] != false {
		hiddenClient(w)
		return
	}

	var state *workState
	switch {
	case stateResult.failure == nil:
		var parsed workState
		if err := json.Unmarshal(stateResult.body, &parsed); err != nil {
			a.log.Error("operator work state is not a json object", "error", err)
			httpx.WriteError(w, http.StatusBadGateway, httpx.CodeBadGateway,
				"Сервис рабочих состояний вернул некорректный ответ")
			return
		}
		state = &parsed
	case stateResult.failure.status == http.StatusNotFound:
		if who.Role == auth.RoleOperator {
			hiddenClient(w)
			return
		}
	case stateResult.failure.status == http.StatusConflict:
		if who.Role == auth.RoleOperator {
			scope, failure := a.publishedScope(r, who, code)
			if failure != nil {
				a.respondUpstreamFailure(w, failure, "client visibility")
				return
			}
			if len(scope.Codes) == 0 {
				hiddenClient(w)
				return
			}
		}
		// Исторический цикл запрошен явно: ответ Operator Work в едином формате.
		a.respondUpstreamFailure(w, stateResult.failure, "operator-work")
		return
	default:
		// Недоступность Operator Work срывает карточку целиком — решением
		// зафиксирован полный 502, деградации нет.
		a.respondBadGateway(w, stateResult.failure)
		return
	}

	operatorName := ""
	if state != nil && state.AssignedOperatorID != nil {
		name, failure := a.operatorName(r.Context(), *state.AssignedOperatorID)
		if failure != nil {
			a.respond(w, failure.status, failure.code, failure.message)
			return
		}
		operatorName = name
	}
	applyState(card, state, operatorName)
	httpx.WriteJSON(w, http.StatusOK, card)
}

// applyState переносит рабочее состояние в карточку; без цикла поля пустые.
func applyState(card map[string]any, state *workState, operatorName string) {
	if state == nil {
		card["work_quarter"] = nil
		card["assignment_id"] = nil
		card["current_stage"] = nil
		card["assignment"] = nil
		card["wait_until"] = nil
		card["wait_source_work_quarter"] = nil
		return
	}
	card["work_quarter"] = state.WorkQuarter
	card["assignment_id"] = state.AssignmentID
	card["current_stage"] = state.Stage
	card["wait_until"] = state.WaitUntil
	card["wait_source_work_quarter"] = state.WaitSourceWorkQuarter
	if state.AssignedOperatorID == nil {
		card["assignment"] = nil
		return
	}
	card["assignment"] = map[string]any{
		"work_quarter":  state.WorkQuarter,
		"operator_id":   *state.AssignedOperatorID,
		"operator_name": operatorName,
		"assigned_at":   state.AssignedAt,
	}
}

// operatorName разрешает логин ответственного; удалённый пользователь даёт пустое имя.
func (a *Aggregator) operatorName(ctx context.Context, rawID string) (string, *upstreamFailure) {
	id, err := uuid.Parse(rawID)
	if err != nil {
		a.log.Error("operator work returned a malformed operator id", "operator_id", rawID)
		return "", &upstreamFailure{
			status:  http.StatusBadGateway,
			code:    httpx.CodeBadGateway,
			message: "Сервис рабочих состояний вернул некорректный ответ",
		}
	}
	user, err := a.users.GetByID(ctx, id)
	switch {
	case err == nil:
		return user.Login, nil
	case errors.Is(err, identity.ErrNotFound):
		return "", nil
	default:
		a.log.Error("identity lookup failed", "operator_id", rawID, "error", err)
		return "", &upstreamFailure{
			status:  http.StatusInternalServerError,
			code:    httpx.CodeInternalError,
			message: "Внутренняя ошибка сервера",
		}
	}
}

func (a *Aggregator) fetchCard(r *http.Request, who auth.Identity, code string) ([]byte, *upstreamFailure) {
	target := *a.clientsURL
	target.Path = a.clientsURL.Path + fmt.Sprintf(clientsCardEndpoint, url.PathEscape(code))
	// Параметры карточки (quarter, snapshots_limit, work_quarter) передаются как есть.
	target.RawQuery = r.URL.RawQuery
	request, err := http.NewRequestWithContext(r.Context(), http.MethodGet, target.String(), http.NoBody)
	if err != nil {
		return nil, unavailable()
	}
	a.setIdentity(request, who)
	return a.do(request)
}

func (a *Aggregator) fetchState(r *http.Request, who auth.Identity, code, quarter string) ([]byte, *upstreamFailure) {
	target := *a.operatorURL
	target.Path = a.operatorURL.Path + fmt.Sprintf(operatorStateEndpoint, url.PathEscape(code))
	if quarter != "" {
		target.RawQuery = workQuarterParam + "=" + url.QueryEscape(quarter)
	}
	request, err := http.NewRequestWithContext(r.Context(), http.MethodGet, target.String(), http.NoBody)
	if err != nil {
		return nil, unavailable()
	}
	a.setIdentity(request, who)
	return a.do(request)
}

func (a *Aggregator) do(request *http.Request) (body []byte, failure *upstreamFailure) {
	response, err := a.client.Do(request) //nolint:gosec // G704: request URL targets fixed config hosts only
	if err != nil {
		return nil, unavailable()
	}
	defer func() { _ = response.Body.Close() }()
	body, err = io.ReadAll(io.LimitReader(response.Body, httpx.MaxRequestBodySize))
	if err != nil {
		return nil, unavailable()
	}
	if response.StatusCode >= 300 {
		return nil, decodeUpstreamError(response.StatusCode, body)
	}
	return body, nil
}

// decodeUpstreamError сохраняет машинночитаемые код и сообщение участника;
// ответ без распознаваемого формата маскируется общим 503.
func decodeUpstreamError(status int, body []byte) *upstreamFailure {
	var payload struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || payload.Code == "" {
		if status == http.StatusUnprocessableEntity {
			return &upstreamFailure{status: status, code: httpx.CodeValidationError, message: "Некорректные параметры запроса"}
		}
		return unavailable()
	}
	return &upstreamFailure{status: status, code: payload.Code, message: payload.Message}
}

func (a *Aggregator) respondUpstreamFailure(w http.ResponseWriter, failure *upstreamFailure, upstream string) {
	if failure.status >= 500 || failure.status == 0 {
		a.log.Error("card upstream failed", "upstream", upstream, "status", failure.status, "code", failure.code)
	}
	a.respond(w, failure.status, failure.code, failure.message)
}

// respondBadGateway срывает карточку, когда Operator Work недоступен или вернул
// неожиданный статус; технические детали — только в лог gateway.
func (a *Aggregator) respondBadGateway(w http.ResponseWriter, failure *upstreamFailure) {
	a.log.Error("operator work state unavailable",
		"status", failure.status, "code", failure.code, "message", failure.message)
	a.respond(w, http.StatusBadGateway, httpx.CodeBadGateway, "Сервис рабочих состояний недоступен")
}

func (a *Aggregator) respond(w http.ResponseWriter, status int, code, message string) {
	httpx.WriteError(w, status, code, message)
}

func (a *Aggregator) setIdentity(request *http.Request, who auth.Identity) {
	// Те же доверенные заголовки, что проставляет прокси (docs/api/internal/*.yaml).
	request.Header.Set(proxy.HeaderUserID, who.UserID.String())
	request.Header.Set(proxy.HeaderUserRole, string(who.Role))
}
