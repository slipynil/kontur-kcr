package card_test

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/google/uuid"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

const eventFeedPath = "/api/v1/clients/" + operatorCode + "/events"

func TestEventFeedHandlesUpstreamResponses(t *testing.T) {
	const author = `{"author_id":"` + operatorID + `"}`
	const snapshot = `{"author_id":"` + operatorID + `","actor_name":"saved-login"}`
	const legacy = `{"items":[` + author + `,` + author + `,` + snapshot + `,{"author_id":null}],"total":4}`
	const enriched = `{"items":[{"author_id":"` + operatorID + `","actor_name":"operator"},` +
		`{"author_id":"` + operatorID + `","actor_name":"operator"},` + snapshot + `,{"author_id":null}],"total":4}`
	const missingClient = `{"code":"CLIENT_NOT_FOUND","message":"Client not found"}`
	const alreadyNamed = `{"items":[` + snapshot + `,{"actor_name":null}],"total":2}`

	for _, tt := range []struct {
		name           string
		upstreamStatus int
		upstreamBody   string
		lookupErr      error
		wantStatus     int
		wantBody       string
	}{
		{"adds names and preserves recorded names and order", http.StatusOK, legacy, nil, http.StatusOK, enriched},
		{"returns events when Identity is unavailable", http.StatusOK, legacy, errors.New("identity unavailable"), http.StatusOK, legacy},
		{"returns already named and system events", http.StatusOK, alreadyNamed, nil, http.StatusOK, alreadyNamed},
		{"preserves the upstream not found response", http.StatusNotFound, missingClient, nil, http.StatusNotFound, missingClient},
		{"rejects a malformed event page", http.StatusOK, `{"items":null}`, nil, http.StatusBadGateway, `{"code":"BAD_GATEWAY","message":"Некорректный ответ ленты событий"}`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			operator := &upstreamStub{router: func(recordedCall) (int, string) {
				return tt.upstreamStatus, tt.upstreamBody
			}}
			users := &fakeUsers{user: identity.User{ID: uuid.MustParse(operatorID), Login: "operator"}, err: tt.lookupErr}
			e := newAggregator(t, &upstreamStub{}, operator, users)
			w := e.getAs(t, eventFeedPath+"?work_quarter=2026_Q4&page=2", auth.RoleAdmin)
			if w.Code != tt.wantStatus {
				t.Fatalf("status = %d, want %d, body %s", w.Code, tt.wantStatus, w.Body.String())
			}
			var got, want any
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal([]byte(tt.wantBody), &want); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("body = %s, want %s", w.Body.String(), tt.wantBody)
			}
			calls := operator.recorded()
			if len(calls) != 1 || calls[0].path != "/internal/v1/clients/"+operatorCode+"/events" || calls[0].query != "work_quarter=2026_Q4&page=2" {
				t.Fatalf("unexpected Operator Work calls: %+v", calls)
			}
		})
	}
}

func TestEventFeedRequiresIdentity(t *testing.T) {
	operator := &upstreamStub{}
	e := newAggregator(t, &upstreamStub{}, operator, &fakeUsers{})
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, eventFeedPath, http.NoBody))
	if w.Code != http.StatusUnauthorized || len(operator.recorded()) != 0 {
		t.Fatalf("expected unauthorized without an upstream call, got %d", w.Code)
	}
}
