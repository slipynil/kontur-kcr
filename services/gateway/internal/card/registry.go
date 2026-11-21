package card

import (
	"bytes"
	"encoding/json"
	"net/http"

	"github.com/google/uuid"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

const currentAssigneesEndpoint = "/internal/v1/clients/current-assignees"

// Registry adds active assignees after Clients has filtered and paginated the list.
func (a *Aggregator) Registry(w http.ResponseWriter, r *http.Request) {
	who, ok := auth.FromContext(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
		return
	}

	path := "/internal/v1/clients"
	var scope *clientScope
	if who.Role == auth.RoleOperator {
		published, failure := a.publishedScope(r, who, "")
		if failure != nil {
			a.respondUpstreamFailure(w, failure, "client visibility")
			return
		}
		scope = published
		path = "/internal/v1/clients/operator-registry"
	}
	body, failure := a.scopeRequest(r, who, a.clientsURL, path, r.URL.RawQuery, scope)
	if failure != nil {
		a.respondUpstreamFailure(w, failure, "clients")
		return
	}

	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var page map[string]any
	err := decoder.Decode(&page)
	items, ok := page["items"].([]any)
	if err != nil || !ok || len(items) > 100 || !json.Valid(body) {
		httpx.WriteError(w, http.StatusBadGateway, httpx.CodeBadGateway, "Некорректный ответ реестра")
		return
	}
	codes := make([]string, 0, len(items))
	rows := make(map[string]map[string]any, len(items))
	for _, item := range items {
		row, _ := item.(map[string]any)
		code, _ := row["kcr_code"].(string)
		if code == "" || rows[code] != nil {
			httpx.WriteError(w, http.StatusBadGateway, httpx.CodeBadGateway, "Некорректный ответ реестра")
			return
		}
		row["assigned_operator_id"] = nil
		row["operator_name"] = nil
		codes = append(codes, code)
		rows[code] = row
	}
	if len(codes) > 0 && !a.applyRegistryAssignees(w, r, who, codes, rows) {
		return
	}
	httpx.WriteJSON(w, http.StatusOK, page)
}

func (a *Aggregator) applyRegistryAssignees(
	w http.ResponseWriter, r *http.Request, who auth.Identity, codes []string, rows map[string]map[string]any,
) bool {
	body, failure := a.scopeRequest(r, who, a.operatorURL, currentAssigneesEndpoint, "", &clientScope{Codes: codes})
	if failure != nil {
		a.respondBadGateway(w, failure)
		return false
	}
	var result struct {
		Items []struct {
			KCRCode    string    `json:"kcr_code"`
			OperatorID uuid.UUID `json:"assigned_operator_id"`
		} `json:"items"`
	}
	if err := json.Unmarshal(body, &result); err != nil || result.Items == nil {
		httpx.WriteError(w, http.StatusBadGateway, httpx.CodeBadGateway, "Некорректный ответ о назначениях")
		return false
	}
	ids := make([]uuid.UUID, 0, len(result.Items))
	seen := make(map[uuid.UUID]bool, len(result.Items))
	for _, assignment := range result.Items {
		row := rows[assignment.KCRCode]
		if row == nil || assignment.OperatorID == uuid.Nil || row["assigned_operator_id"] != nil {
			httpx.WriteError(w, http.StatusBadGateway, httpx.CodeBadGateway, "Некорректный ответ о назначениях")
			return false
		}
		row["assigned_operator_id"] = assignment.OperatorID
		if !seen[assignment.OperatorID] {
			ids = append(ids, assignment.OperatorID)
			seen[assignment.OperatorID] = true
		}
	}
	if len(ids) == 0 {
		return true
	}
	users, err := a.users.GetByIDs(r.Context(), ids)
	if err != nil {
		a.log.Error("registry assignee lookup failed", "error", err)
		httpx.WriteInternalError(w)
		return false
	}
	names := make(map[uuid.UUID]string, len(users))
	for _, user := range users {
		names[user.ID] = user.Login
	}
	for _, assignment := range result.Items {
		if name := names[assignment.OperatorID]; name != "" {
			rows[assignment.KCRCode]["operator_name"] = name
		}
	}
	return true
}
