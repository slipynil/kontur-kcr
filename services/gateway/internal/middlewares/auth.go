// Package middlewares содержит HTTP-middleware gateway: JWT-аутентификация,
// проверка ролей, логирование, восстановление после паники и CORS.
package middlewares

import (
	"net/http"
	"strings"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

// Authenticate проверяет заголовок Authorization: Bearer <JWT>
// и кладёт проверенную идентичность в контекст запроса.
func Authenticate(tokens *auth.TokenManager) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			token, ok := bearerToken(r.Header.Get("Authorization"))
			if !ok {
				httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "Требуется авторизация")
				return
			}
			identity, err := tokens.Verify(token)
			if err != nil {
				httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidToken, "Недействительный или истёкший токен")
				return
			}
			next.ServeHTTP(w, r.WithContext(auth.WithIdentity(r.Context(), identity)))
		})
	}
}

// bearerToken достаёт токен из заголовка Authorization; схема нечувствительна к регистру.
func bearerToken(header string) (string, bool) {
	scheme, token, found := strings.Cut(header, " ")
	if !found || !strings.EqualFold(scheme, "Bearer") {
		return "", false
	}
	token = strings.TrimSpace(token)
	if token == "" {
		return "", false
	}
	return token, true
}
