package middlewares

import (
	"net/http"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

// RequireRole пропускает запрос только с одной из указанных ролей
// (x-roles в docs/api/public-api.yaml); остальные получают 403 FORBIDDEN.
func RequireRole(roles ...auth.Role) func(http.Handler) http.Handler {
	allowed := make(map[auth.Role]struct{}, len(roles))
	for _, role := range roles {
		allowed[role] = struct{}{}
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			identity, ok := auth.FromContext(r.Context())
			if !ok {
				// Защита: маршрут без Authenticate не должен пропускать запросы.
				httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
				return
			}
			if _, ok := allowed[identity.Role]; !ok {
				httpx.WriteError(w, http.StatusForbidden, httpx.CodeForbidden, "Недостаточно прав")
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
