package middlewares

import (
	"log/slog"
	"net/http"
	"runtime/debug"
	"time"

	"github.com/go-chi/chi/v5/middleware"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

// RequestLogger логирует каждый запрос: метод, путь, статус, длительность,
// request id и идентификатор пользователя, если он аутентифицирован.
func RequestLogger(log *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			recorder := &statusWriter{ResponseWriter: w, status: http.StatusOK}

			next.ServeHTTP(recorder, r)

			attrs := []any{
				"request_id", middleware.GetReqID(r.Context()),
				"method", r.Method,
				"path", r.URL.Path,
				"status", recorder.status,
				"duration_ms", time.Since(start).Milliseconds(),
			}
			if identity, ok := auth.FromContext(r.Context()); ok {
				attrs = append(attrs, "user_id", identity.UserID.String(), "role", string(identity.Role))
			}
			log.Info("http request", attrs...)
		})
	}
}

// Recoverer возвращает panic в 500 INTERNAL_ERROR вместо обрыва соединения.
func Recoverer(log *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if recovered := recover(); recovered != nil {
					log.Error("panic recovered",
						"panic", recovered,
						"method", r.Method,
						"path", r.URL.Path,
						"stack", string(debug.Stack()),
					)
					httpx.WriteInternalError(w)
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

// statusWriter запоминает статус ответа для логирования.
// Unwrap позволяет http.ResponseController пробудить Flush/Hijack
// нижележащего writer (важно для проксирования).
type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(status int) {
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *statusWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}
