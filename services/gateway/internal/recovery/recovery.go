// Package recovery координирует публичное удаление клиента и его квартальных данных.
// Gateway подтверждает локальную команду Clients, затем очистку Operator Work, и
// возвращает успех только после обоих участников (spec.md §3.2 «Локальная очистка
// Operator Work»; docs/api/public-api.yaml deleteClient/deleteClientQuarter; правила
// выбора целей — docs/api/import-operator-work-contract.md).
package recovery

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
	"github.com/slipynil/kontur-kcr/services/identity/internal/proxy"
)

// Внутренние маршруты участников очистки (docs/api/internal/*.yaml).
const (
	operatorCleanupEndpoint = "/internal/v1/data-cleanup"
	operatorEventsEndpoint  = "/internal/v1/clients/%s/events"
	eventsPageSize          = 100
	// 5000 — контрактный лимит целей-событий на клиента; 100 страниц по 100 записей
	// покрывают его с запасом и ограничивают время координации.
	maxEventPages = 100
)

// quarterPattern совпадает с контрактом deleteClientQuarter (public-api.yaml).
var quarterPattern = regexp.MustCompile(`^\d{4}_Q[1-4]$`)

// Coordinator проводит публичные DELETE-команды через оба сервиса.
// Обе стороны идемпотентны по operation_id, поэтому потерянный ответ лечится
// повтором той же команды.
type Coordinator struct {
	log         *slog.Logger
	client      *http.Client
	clientsURL  *url.URL
	operatorURL *url.URL
}

// New собирает координатор; некорректный URL — ошибка запуска.
func New(log *slog.Logger, timeout time.Duration, clientsURL, operatorURL string) (*Coordinator, error) {
	clients, err := url.Parse(clientsURL)
	if err != nil {
		return nil, fmt.Errorf("parse clients upstream url %q: %w", clientsURL, err)
	}
	operator, err := url.Parse(operatorURL)
	if err != nil {
		return nil, fmt.Errorf("parse operator-work upstream url %q: %w", operatorURL, err)
	}
	if clients.Scheme == "" || clients.Host == "" || operator.Scheme == "" || operator.Host == "" {
		return nil, errors.New("invalid recovery upstream url: scheme and host are required")
	}
	return &Coordinator{
		log:         log,
		client:      &http.Client{Timeout: timeout},
		clientsURL:  clients,
		operatorURL: operator,
	}, nil
}

// DeleteClient удаляет клиента целиком: данные Clients и вся локальная работа Operator Work.
func (c *Coordinator) DeleteClient(w http.ResponseWriter, r *http.Request) {
	c.delete(w, r, "")
}

// DeleteQuarter удаляет данные выбранного исходного квартала; остальные кварталы,
// профиль и общая операторская история сохраняются.
func (c *Coordinator) DeleteQuarter(w http.ResponseWriter, r *http.Request) {
	c.delete(w, r, chi.URLParam(r, "quarter"))
}

// clientsCommand — тело внутренней команды Clients; requested_by Gateway
// добавляет из проверенного пользовательского контекста (public-api.yaml).
type clientsCommand struct {
	OperationID uuid.UUID `json:"operation_id"`
	RequestedBy uuid.UUID `json:"requested_by"`
}

// cleanupBody — команда POST /internal/v1/data-cleanup в Operator Work.
// Поля целей включаются только для выборочной очистки: ClientCleanupRequest
// запрещает неизвестные поля.
type cleanupBody struct {
	OperationID uuid.UUID   `json:"operation_id"`
	KcrCode     string      `json:"kcr_code"`
	Scope       string      `json:"scope"`
	Quarter     string      `json:"quarter,omitempty"`
	CycleIDs    []uuid.UUID `json:"cycle_ids,omitempty"`
	EventIDs    []uuid.UUID `json:"event_ids,omitempty"`
}

// upstreamFailure — ошибка участника очистки; ожидаемые 4xx уходят наружу как есть,
// остальное маскируется единым 503 без технических деталей.
type upstreamFailure struct {
	status  int
	code    string
	message string
}

func serviceUnavailable() *upstreamFailure {
	return &upstreamFailure{
		status:  http.StatusServiceUnavailable,
		code:    httpx.CodeServiceUnavailable,
		message: "Сервис временно недоступен, повторите позже",
	}
}

// publicStatus переводит статус участника в статус публичного ответа удаления
// (public-api.yaml: 200, 400, 409, 503).
func (f *upstreamFailure) publicStatus() int {
	switch f.status {
	case http.StatusBadRequest, http.StatusNotFound, http.StatusConflict:
		return f.status
	case http.StatusUnprocessableEntity:
		// Публичный контракт удаления описывает ошибки команды статусом 400.
		return http.StatusBadRequest
	default:
		return http.StatusServiceUnavailable
	}
}

func (c *Coordinator) delete(w http.ResponseWriter, r *http.Request, quarter string) {
	if quarter != "" && !quarterPattern.MatchString(quarter) {
		httpx.WriteValidationError(w)
		return
	}
	identity, ok := auth.FromContext(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
		return
	}
	var command struct {
		OperationID uuid.UUID `json:"operation_id"`
		// requested_by приходит в теле по контракту, но Gateway подменяет его
		// проверенной личностью; значение клиента игнорируется.
		RequestedBy json.RawMessage `json:"requested_by"`
	}
	if err := httpx.DecodeJSON(r, &command); err != nil {
		httpx.WriteValidationError(w)
		return
	}
	if command.OperationID == uuid.Nil {
		httpx.WriteValidationError(w)
		return
	}

	code := chi.URLParam(r, "kcr_code")
	if failure := c.callClients(r.Context(), identity, command.OperationID, code, quarter); failure != nil {
		c.respondUpstreamFailure(w, failure, "clients")
		return
	}

	// Удаление исходного квартала не затрагивает циклы обзвона: исходный квартал
	// документов не определяет квартал обзвона. Убираются только события,
	// привязанные ровно к этому кварталу (import-operator-work-contract.md).
	if quarter != "" {
		targets, failure := c.quarterEventTargets(r.Context(), identity, code, quarter)
		if failure != nil {
			c.respondUpstreamFailure(w, failure, "operator-work")
			return
		}
		// Без затронутых событий шаг очистки Operator Work не нужен.
		if len(targets) == 0 {
			w.WriteHeader(http.StatusOK)
			return
		}
		if failure := c.cleanup(r.Context(), identity, cleanupBody{
			OperationID: command.OperationID,
			KcrCode:     code,
			Scope:       "QUARTER",
			Quarter:     quarter,
			EventIDs:    targets,
		}); failure != nil {
			c.respondUpstreamFailure(w, failure, "operator-work")
			return
		}
		w.WriteHeader(http.StatusOK)
		return
	}

	if failure := c.cleanup(r.Context(), identity, cleanupBody{
		OperationID: command.OperationID,
		KcrCode:     code,
		Scope:       "CLIENT",
	}); failure != nil {
		c.respondUpstreamFailure(w, failure, "operator-work")
		return
	}
	w.WriteHeader(http.StatusOK)
}

func (c *Coordinator) respondUpstreamFailure(w http.ResponseWriter, failure *upstreamFailure, upstream string) {
	if failure.status >= 500 || failure.status == 0 {
		c.log.Error("recovery upstream failed",
			"upstream", upstream, "status", failure.status, "code", failure.code)
	}
	httpx.WriteError(w, failure.publicStatus(), failure.code, failure.message)
}

func (c *Coordinator) setIdentity(request *http.Request, identity auth.Identity) {
	// Те же доверенные заголовки, что проставляет прокси (docs/api/internal/*.yaml).
	request.Header.Set(proxy.HeaderUserID, identity.UserID.String())
	request.Header.Set(proxy.HeaderUserRole, string(identity.Role))
}

func (c *Coordinator) callClients(
	ctx context.Context, identity auth.Identity, operationID uuid.UUID, code, quarter string,
) *upstreamFailure {
	target := *c.clientsURL
	target.Path = c.clientsURL.Path + "/internal/v1/clients/" + url.PathEscape(code)
	if quarter != "" {
		target.Path += "/snapshots/" + url.PathEscape(quarter)
	}
	body, err := json.Marshal(clientsCommand{OperationID: operationID, RequestedBy: identity.UserID})
	if err != nil {
		return serviceUnavailable()
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodDelete, target.String(), bytes.NewReader(body))
	if err != nil {
		return serviceUnavailable()
	}
	c.setIdentity(request, identity)
	request.Header.Set("Content-Type", "application/json")
	_, _, failure := c.do(request)
	return failure
}

// cleanup отправляет очистку Operator Work. Повтор потерянного ответа использует
// тот же operation_id; если цели очистки изменились с прошлой попытки, контракт
// предписывает утверждать план как новую команду — координатор один раз повторяет
// с новым ID.
func (c *Coordinator) cleanup(ctx context.Context, identity auth.Identity, body cleanupBody) *upstreamFailure {
	if failure := c.postCleanup(ctx, identity, body); !isOperationIDConflict(failure) {
		return failure
	}
	c.log.Warn("cleanup operation id conflict, retrying with a new operation id",
		"kcr_code", body.KcrCode, "scope", body.Scope)
	body.OperationID = uuid.New()
	return c.postCleanup(ctx, identity, body)
}

func isOperationIDConflict(failure *upstreamFailure) bool {
	return failure != nil && failure.status == http.StatusConflict && failure.code == "OPERATION_ID_CONFLICT"
}

func (c *Coordinator) postCleanup(ctx context.Context, identity auth.Identity, body cleanupBody) *upstreamFailure {
	payload, err := json.Marshal(body)
	if err != nil {
		return serviceUnavailable()
	}
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, c.operatorURL.String()+operatorCleanupEndpoint, bytes.NewReader(payload),
	)
	if err != nil {
		return serviceUnavailable()
	}
	c.setIdentity(request, identity)
	request.Header.Set("Content-Type", "application/json")
	_, _, failure := c.do(request)
	return failure
}

// quarterEventTargets собирает ID событий, привязанных ровно к удаляемому кварталу.
// Общие события (пустая или множественная привязка) остаются в истории.
func (c *Coordinator) quarterEventTargets(
	ctx context.Context, identity auth.Identity, code, quarter string,
) ([]uuid.UUID, *upstreamFailure) {
	var targets []uuid.UUID
	for page := 1; page <= maxEventPages; page++ {
		// URL собирается из полей распарсенного при запуске адреса сервиса и
		// экранированного кода клиента: хост подменить нельзя (ADR-0010 —
		// внутренний контур без сервисных токенов).
		target := *c.operatorURL
		target.Path = c.operatorURL.Path + fmt.Sprintf(operatorEventsEndpoint, url.PathEscape(code))
		target.RawQuery = url.Values{
			"page":      []string{strconv.Itoa(page)},
			"page_size": []string{strconv.Itoa(eventsPageSize)},
		}.Encode()
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), http.NoBody) //nolint:gosec // G704: host is fixed config, path is escaped
		if err != nil {
			return nil, serviceUnavailable()
		}
		c.setIdentity(request, identity)
		_, body, failure := c.do(request)
		if failure != nil {
			// Клиент мог не иметь работы в Operator Work вовсе (удаление
			// отсутствующего объекта успешно с нулевыми счётчиками — ADR-0006).
			if failure.status == http.StatusNotFound && failure.code == "CLIENT_NOT_FOUND" {
				return targets, nil
			}
			return nil, failure
		}
		var payload struct {
			Items []struct {
				ID               uuid.UUID `json:"id"`
				DocumentQuarters []string  `json:"document_quarters"`
			} `json:"items"`
			Total int `json:"total"`
		}
		if err := json.Unmarshal(body, &payload); err != nil {
			return nil, serviceUnavailable()
		}
		for _, item := range payload.Items {
			if len(item.DocumentQuarters) == 1 && item.DocumentQuarters[0] == quarter {
				targets = append(targets, item.ID)
			}
		}
		if len(payload.Items) < eventsPageSize || page*eventsPageSize >= payload.Total {
			return targets, nil
		}
	}
	// Лента больше контрактного лимита целей: автоматическая очистка невозможна.
	return nil, &upstreamFailure{
		status:  http.StatusServiceUnavailable,
		code:    httpx.CodeServiceUnavailable,
		message: "Слишком много затронутых событий, очистка требует согласования",
	}
}

// do выполняет запрос к участнику и разбирает ответ: тело — всегда, ошибку —
// если статус не 2xx или транспорт сорвался.
func (c *Coordinator) do(request *http.Request) (status int, body []byte, failure *upstreamFailure) {
	response, err := c.client.Do(request) //nolint:gosec // G704: request URL targets fixed config hosts only
	if err != nil {
		return 0, nil, serviceUnavailable()
	}
	defer func() { _ = response.Body.Close() }()
	body, err = io.ReadAll(io.LimitReader(response.Body, httpx.MaxRequestBodySize))
	if err != nil {
		return response.StatusCode, nil, serviceUnavailable()
	}
	if response.StatusCode >= 300 {
		return response.StatusCode, body, decodeUpstreamError(response.StatusCode, body)
	}
	return response.StatusCode, body, nil
}

// decodeUpstreamError сохраняет машинночитаемые код и сообщение участника;
// ответ без распознаваемого формата маскируется общим 503.
func decodeUpstreamError(status int, body []byte) *upstreamFailure {
	var payload struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || payload.Code == "" {
		return serviceUnavailable()
	}
	return &upstreamFailure{status: status, code: payload.Code, message: payload.Message}
}
