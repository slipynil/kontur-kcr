package middlewares

import (
	"net/http"
)

const (
	accessControlMaxAge         = "600"
	accessControlAllowMethods   = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
	accessControlAllowHeaders   = "Authorization, Content-Type"
	accessControlRequestMethods = "Access-Control-Request-Method"
)

// CORS добавляет заголовки CORS для разрешённых источников (spec.md §1.1:
// контроль CORS на gateway). Предполётные OPTIONS отвечают до аутентификации.
// Пустой список origins отключает CORS (заголовки не выставляются).
func CORS(allowedOrigins []string) func(http.Handler) http.Handler {
	allowAll := false
	allowed := make(map[string]struct{}, len(allowedOrigins))
	for _, origin := range allowedOrigins {
		if origin == "*" {
			allowAll = true
		}
		allowed[origin] = struct{}{}
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			_, originAllowed := allowed[origin]
			if origin != "" && (allowAll || originAllowed) {
				header := w.Header()
				if allowAll {
					header.Set("Access-Control-Allow-Origin", "*")
				} else {
					header.Set("Access-Control-Allow-Origin", origin)
				}
				header.Add("Vary", "Origin")
				header.Set("Access-Control-Allow-Methods", accessControlAllowMethods)
				header.Set("Access-Control-Allow-Headers", accessControlAllowHeaders)
				header.Set("Access-Control-Max-Age", accessControlMaxAge)
			}

			// Предполётный запрос браузера: отвечаем сразу, токена у него нет.
			if r.Method == http.MethodOptions && r.Header.Get(accessControlRequestMethods) != "" {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
