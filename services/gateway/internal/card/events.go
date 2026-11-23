package card

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/google/uuid"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

// EventFeed supplies actor names for pages still served by Operator Work.
// Client History stores the names with the events and can serve them directly.
func (a *Aggregator) EventFeed(w http.ResponseWriter, r *http.Request) {
	who, ok := auth.FromContext(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
		return
	}
	path := "/internal/v1" + strings.TrimPrefix(r.URL.Path, "/api/v1")
	body, failure := a.scopeRequest(r, who, a.operatorURL, path, r.URL.RawQuery, nil)
	if failure != nil {
		a.respondUpstreamFailure(w, failure, "operator-work")
		return
	}

	var page map[string]json.RawMessage
	var items []map[string]json.RawMessage
	if err := json.Unmarshal(body, &page); err != nil || json.Unmarshal(page["items"], &items) != nil || items == nil {
		httpx.WriteError(w, http.StatusBadGateway, httpx.CodeBadGateway, "Некорректный ответ ленты событий")
		return
	}

	byAuthor := make(map[uuid.UUID][]map[string]json.RawMessage)
	ids := make([]uuid.UUID, 0, len(items))
	for _, item := range items {
		if _, hasName := item["actor_name"]; hasName {
			continue
		}
		var id uuid.UUID
		if err := json.Unmarshal(item["author_id"], &id); err != nil || id == uuid.Nil {
			continue
		}
		if len(byAuthor[id]) == 0 {
			ids = append(ids, id)
		}
		byAuthor[id] = append(byAuthor[id], item)
	}
	if len(ids) == 0 {
		httpx.WriteJSON(w, http.StatusOK, json.RawMessage(body))
		return
	}
	users, err := a.users.GetByIDs(r.Context(), ids)
	if err != nil {
		a.log.Error("event author lookup failed", "error", err)
		httpx.WriteJSON(w, http.StatusOK, json.RawMessage(body))
		return
	}
	for _, user := range users {
		name, _ := json.Marshal(user.Login)
		for _, item := range byAuthor[user.ID] {
			item["actor_name"] = name
		}
	}
	page["items"], err = json.Marshal(items)
	if err != nil {
		httpx.WriteInternalError(w)
		return
	}
	httpx.WriteJSON(w, http.StatusOK, page)
}
