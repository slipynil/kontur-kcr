// Package app связывает конфигурацию, логгер, Identity DB и HTTP-сервер gateway.
package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/slipynil/kontur-kcr/services/identity/configs"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
	"github.com/slipynil/kontur-kcr/services/identity/internal/server"
)

// storeConnector открывает Identity DB и возвращает функцию освобождения.
type storeConnector func(ctx context.Context, dsn string) (identity.Store, func(), error)

// Option настраивает подключение хранилища (в тестах — подмена).
type Option func(*runner)

type runner struct {
	connect storeConnector
}

func newRunner(opts ...Option) *runner {
	r := &runner{connect: openIdentityStore}
	for _, opt := range opts {
		opt(r)
	}
	return r
}

func openIdentityStore(ctx context.Context, dsn string) (identity.Store, func(), error) {
	store, err := identity.Open(ctx, dsn)
	if err != nil {
		return nil, nil, err
	}
	return store, store.Close, nil
}

// WithStore подменяет хранилище (используется тестами без реальной БД).
func WithStore(store identity.Store) Option {
	return func(r *runner) {
		r.connect = func(context.Context, string) (identity.Store, func(), error) {
			return store, func() {}, nil
		}
	}
}

// Run поднимает gateway и блокирует до сигнала завершения.
func Run(ctx context.Context, cfg *configs.Config, opts ...Option) error {
	ctx, stop := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()

	log := newLogger(cfg.Service.GatewayDebug)

	tokens, err := auth.NewTokenManager(cfg.Service.JWTSecret, cfg.Service.JWTTTL)
	if err != nil {
		return fmt.Errorf("init token manager: %w", err)
	}

	r := newRunner(opts...)
	store, closeStore, err := r.connect(ctx, cfg.Service.IdentityDSN)
	if err != nil {
		return fmt.Errorf("connect identity db: %w", err)
	}
	defer closeStore()

	handler, err := server.New(server.Deps{
		Log:    log,
		Config: cfg,
		Store:  store,
		Tokens: tokens,
	})
	if err != nil {
		return fmt.Errorf("init server: %w", err)
	}

	log.Info("gateway starting", "port", cfg.Service.GatewayPort)
	return server.ListenAndServe(ctx, log, handler, cfg.Service.GatewayPort)
}

// Seed создаёт первого администратора по переменным окружения
// ADMIN_LOGIN и ADMIN_PASSWORD (задача mise seed-admin).
func Seed(ctx context.Context, cfg *configs.Config, opts ...Option) error {
	login := os.Getenv("ADMIN_LOGIN")
	password := os.Getenv("ADMIN_PASSWORD")
	if login == "" || password == "" {
		return errors.New("set ADMIN_LOGIN and ADMIN_PASSWORD, e.g. ADMIN_LOGIN=admin ADMIN_PASSWORD=secret mise run seed-admin")
	}

	r := newRunner(opts...)
	store, closeStore, err := r.connect(ctx, cfg.Service.IdentityDSN)
	if err != nil {
		return fmt.Errorf("connect identity db: %w", err)
	}
	defer closeStore()

	user, err := identity.SeedAdmin(ctx, store, login, password)
	if err != nil {
		return fmt.Errorf("seed admin: %w", err)
	}
	slog.Info("admin user created", "login", user.Login, "id", user.ID.String())
	return nil
}

// newLogger строит структурный JSON-логгер в stdout.
func newLogger(debug bool) *slog.Logger {
	level := slog.LevelInfo
	if debug {
		level = slog.LevelDebug
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
}
