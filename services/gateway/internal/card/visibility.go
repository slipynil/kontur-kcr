package card

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

type clientScope struct {
	Codes []string `json:"codes"`
}

func (a *Aggregator) scopeRequest(
	r *http.Request, who auth.Identity, upstream *url.URL, path, query string, input *clientScope,
) ([]byte, *upstreamFailure) {
	target := *upstream
	target.Path += path
	target.RawQuery = query
	method := http.MethodGet
	var body io.Reader = http.NoBody
	if input != nil {
		data, err := json.Marshal(input)
		if err != nil {
			return nil, unavailable()
		}
		method = http.MethodPost
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(r.Context(), method, target.String(), body)
	if err != nil {
		return nil, unavailable()
	}
	if input != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	a.setIdentity(req, who)
	return a.do(req)
}

func (a *Aggregator) readScope(
	r *http.Request, who auth.Identity, upstream *url.URL, path, query string, input *clientScope,
) (*clientScope, *upstreamFailure) {
	body, failure := a.scopeRequest(r, who, upstream, path, query, input)
	if failure != nil {
		return nil, failure
	}
	var result clientScope
	if err := json.Unmarshal(body, &result); err != nil || result.Codes == nil {
		return nil, &upstreamFailure{http.StatusBadGateway, httpx.CodeBadGateway, "Некорректный ответ проверки доступа"}
	}
	return &result, nil
}

func (a *Aggregator) publishedScope(r *http.Request, who auth.Identity, code string) (*clientScope, *upstreamFailure) {
	query := ""
	if code != "" {
		query = url.Values{"kcr_code": {code}}.Encode()
	}
	return a.readScope(r, who, a.operatorURL, "/internal/v1/pool/published-codes", query, nil)
}

func (a *Aggregator) unblockedScope(r *http.Request, who auth.Identity, scope *clientScope) (*clientScope, *upstreamFailure) {
	if len(scope.Codes) == 0 {
		return scope, nil
	}
	return a.readScope(r, who, a.clientsURL, "/internal/v1/clients/unblocked-codes", "", scope)
}

func hiddenClient(w http.ResponseWriter) {
	httpx.WriteError(w, http.StatusNotFound, httpx.CodeNotFound, "Клиент недоступен")
}

// VisibleClient guards operator routes that act on a specific client.
func (a *Aggregator) VisibleClient(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		who, ok := auth.FromContext(r.Context())
		if !ok {
			httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
			return
		}
		if who.Role == auth.RoleOperator {
			code := chi.URLParam(r, "kcr_code")
			scope, failure := a.publishedScope(r, who, code)
			if failure == nil {
				scope, failure = a.unblockedScope(r, who, scope)
			}
			if failure != nil {
				a.respondUpstreamFailure(w, failure, "client visibility")
				return
			}
			if !slices.Contains(scope.Codes, code) {
				hiddenClient(w)
				return
			}
		}
		next(w, r)
	}
}

func (a *Aggregator) Registry(next http.HandlerFunc) http.HandlerFunc {
	return a.scopedCollection(next, true, func(_ *http.Request) string {
		return "/internal/v1/clients/operator-registry"
	})
}

func (a *Aggregator) Pool(next http.HandlerFunc) http.HandlerFunc {
	return a.scopedCollection(next, false, func(_ *http.Request) string {
		return "/internal/v1/pool"
	})
}

func (a *Aggregator) Notifications(next http.HandlerFunc) http.HandlerFunc {
	return a.scopedCollection(next, false, func(_ *http.Request) string {
		return "/internal/v1/reassignment-notifications"
	})
}

func (a *Aggregator) Reject(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id, err := uuid.Parse(chi.URLParam(r, "request_id"))
		if err != nil {
			httpx.WriteValidationError(w)
			return
		}
		a.scopedCollection(next, false, func(_ *http.Request) string {
			return fmt.Sprintf("/internal/v1/reassignment-notifications/%s/reject", id)
		})(w, r)
	}
}

func (a *Aggregator) scopedCollection(next http.HandlerFunc, registry bool, path func(*http.Request) string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		who, ok := auth.FromContext(r.Context())
		if !ok {
			httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
			return
		}
		if who.Role == auth.RoleAdmin {
			next(w, r)
			return
		}
		scope, failure := a.publishedScope(r, who, "")
		if failure == nil && !registry {
			scope, failure = a.unblockedScope(r, who, scope)
		}
		if failure != nil {
			a.respondUpstreamFailure(w, failure, "client visibility")
			return
		}
		upstream := a.operatorURL
		if registry {
			upstream = a.clientsURL
		}
		data, failure := a.scopeRequest(r, who, upstream, path(r), r.URL.RawQuery, scope)
		if failure != nil {
			a.respondUpstreamFailure(w, failure, "scoped client data")
			return
		}
		if !json.Valid(data) {
			httpx.WriteError(w, http.StatusBadGateway, httpx.CodeBadGateway, "Некорректный ответ сервиса")
			return
		}
		httpx.WriteJSON(w, http.StatusOK, json.RawMessage(data))
	}
}
