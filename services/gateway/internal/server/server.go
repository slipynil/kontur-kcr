// Package server собирает маршруты gateway: собственные эндпоинты авторизации
// и пользователей, внутренний API пользователей и проксирование в микросервисы
// (spec.md §3.1, docs/api/public-api.yaml).
package server

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/slipynil/kontur-kcr/services/identity/configs"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/card"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
	"github.com/slipynil/kontur-kcr/services/identity/internal/middlewares"
	"github.com/slipynil/kontur-kcr/services/identity/internal/proxy"
	"github.com/slipynil/kontur-kcr/services/identity/internal/recovery"
)

// Deps — зависимости сборки роутера.
type Deps struct {
	Log    *slog.Logger
	Config *configs.Config
	Store  identity.Store
	Tokens *auth.TokenManager
}

// New собирает http.Handler gateway.
func New(deps Deps) (http.Handler, error) {
	cfg := deps.Config.Service
	upstreams, err := proxy.New(
		deps.Log,
		cfg.UpstreamTimeout,
		cfg.ClientsEndpoint,
		cfg.ImportAPIEndpoint,
		cfg.OperatorWorkEndpoint,
		cfg.AssistantEndpoint,
	)
	if err != nil {
		return nil, fmt.Errorf("init upstreams: %w", err)
	}

	// Публичное удаление клиента/квартала координирует Clients и Operator Work
	// вместо прозрачного проксирования (ADR-0006, import-operator-work-contract.md).
	recoveryCoordinator, err := recovery.New(
		deps.Log,
		cfg.UpstreamTimeout,
		cfg.ClientsEndpoint,
		cfg.OperatorWorkEndpoint,
	)
	if err != nil {
		return nil, fmt.Errorf("init recovery coordinator: %w", err)
	}

	// Публичная карточка агрегирует Clients, Operator Work и Identity DB
	// вместо прозрачного проксирования (spec.md §3.6, ADR-0012).
	cardAggregator, err := card.New(
		deps.Log,
		cfg.UpstreamTimeout,
		cfg.ClientsEndpoint,
		cfg.OperatorWorkEndpoint,
		deps.Store,
	)
	if err != nil {
		return nil, fmt.Errorf("init card aggregator: %w", err)
	}

	users := &identity.Handlers{Store: deps.Store, Tokens: deps.Tokens}
	allRoles := []auth.Role{auth.RoleAdmin, auth.RoleOperator}

	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middlewares.Recoverer(deps.Log))
	r.Use(middlewares.CORS(cfg.CORSAllowedOrigins))
	r.Use(middlewares.RequestLogger(deps.Log))

	r.Get("/healthz", healthz)

	// Внутренний API для микросервисов: доверенная сеть, без JWT
	// (docs/api/internal/gateway-identity.yaml).
	r.Route("/internal/v1", func(r chi.Router) {
		r.Get("/users/{user_id}", users.GetInternalUser)
		r.Get("/users", users.ListInternalUsers)
	})

	r.Route("/api/v1", func(r chi.Router) {
		// Аутентификация доступна без токена.
		r.Post("/auth/login", users.Login)

		r.Group(func(r chi.Router) {
			r.Use(middlewares.Authenticate(deps.Tokens))

			// Управление пользователями, импортом, публикацией и удалением данных — только ADMIN.
			r.Group(func(r chi.Router) {
				r.Use(middlewares.RequireRole(auth.RoleAdmin))
				r.Get("/users", users.ListUsers)
				r.Post("/users", users.CreateUser)
				r.Post("/imports", upstreams.HandlerFunc(proxy.ServiceImport))
				r.Get("/imports/{batch_id}", upstreams.HandlerFunc(proxy.ServiceImport))
				r.Post("/imports/{batch_id}/rollback", upstreams.HandlerFunc(proxy.ServiceImport))
				r.Put("/pool/publication", upstreams.HandlerFunc(proxy.ServiceOperatorWork))
				r.Put("/clients/{kcr_code}/publication", upstreams.HandlerFunc(proxy.ServiceOperatorWork))
				r.Delete("/clients/{kcr_code}", recoveryCoordinator.DeleteClient)
				r.Delete("/clients/{kcr_code}/snapshots/{quarter}", recoveryCoordinator.DeleteQuarter)
				r.Post("/assistant/filter-query", upstreams.HandlerFunc(proxy.ServiceAssistant))
				r.Post("/assistant/chat", upstreams.HandlerFunc(proxy.ServiceAssistant))
			})

			// Чтение реестра, карточек, лента событий и ИИ — обе роли.
			r.Group(func(r chi.Router) {
				r.Use(middlewares.RequireRole(allRoles...))
				r.Get("/clients", cardAggregator.Registry)
				r.Get("/clients/{kcr_code}", cardAggregator.Client)
				r.Get("/clients/{kcr_code}/snapshots/history", cardAggregator.VisibleClient(upstreams.HandlerFunc(proxy.ServiceClients)))
				r.Get("/pool", cardAggregator.Pool(upstreams.HandlerFunc(proxy.ServiceOperatorWork)))
				r.Get("/clients/{kcr_code}/events", cardAggregator.VisibleClient(upstreams.HandlerFunc(proxy.ServiceOperatorWork)))
				r.Post("/clients/{kcr_code}/events", cardAggregator.VisibleClient(upstreams.HandlerFunc(proxy.ServiceOperatorWork)))
				r.Get("/assistant/summary/{kcr_code}", cardAggregator.VisibleClient(upstreams.HandlerFunc(proxy.ServiceAssistant)))
			})

			// Рабочие действия оператора — только OPERATOR.
			r.Group(func(r chi.Router) {
				r.Use(middlewares.RequireRole(auth.RoleOperator))
				r.Post("/clients/{kcr_code}/take", cardAggregator.VisibleClient(upstreams.HandlerFunc(proxy.ServiceOperatorWork)))
			})
		})
	})

	r.NotFound(notFound)
	r.MethodNotAllowed(methodNotAllowed)
	return r, nil
}

func healthz(w http.ResponseWriter, _ *http.Request) {
	httpx.WriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func notFound(w http.ResponseWriter, _ *http.Request) {
	httpx.WriteError(w, http.StatusNotFound, httpx.CodeNotFound, "Объект не найден")
}

func methodNotAllowed(w http.ResponseWriter, _ *http.Request) {
	httpx.WriteError(w, http.StatusMethodNotAllowed, httpx.CodeNotFound, "Метод не поддерживается")
}

// Таймауты http.Server (spec.md §1.1: контроль таймаутов на gateway).
const (
	readHeaderTimeout = 10 * time.Second
	readTimeout       = 30 * time.Second
	writeTimeout      = 60 * time.Second
	idleTimeout       = 120 * time.Second
	shutdownTimeout   = 10 * time.Second
)

// ListenAndServe запускает HTTP-сервер и останавливает его при отмене контекста.
func ListenAndServe(ctx context.Context, log *slog.Logger, handler http.Handler, port int) error {
	srv := &http.Server{
		Addr:              ":" + strconv.Itoa(port),
		Handler:           handler,
		ReadHeaderTimeout: readHeaderTimeout,
		ReadTimeout:       readTimeout,
		WriteTimeout:      writeTimeout,
		IdleTimeout:       idleTimeout,
	}

	serveErr := make(chan error, 1)
	go func() {
		serveErr <- srv.ListenAndServe()
	}()

	select {
	case err := <-serveErr:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return fmt.Errorf("gateway listen: %w", err)
	case <-ctx.Done():
		// Без отмены: выключение не должно прерываться исходным сигналом.
		shutdownCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), shutdownTimeout)
		defer cancel()
		if err := srv.Shutdown(shutdownCtx); err != nil {
			return fmt.Errorf("gateway shutdown: %w", err)
		}
		if err := <-serveErr; err != nil && !errors.Is(err, http.ErrServerClosed) {
			return fmt.Errorf("gateway serve after shutdown: %w", err)
		}
		log.Info("gateway stopped")
		return nil
	}
}
