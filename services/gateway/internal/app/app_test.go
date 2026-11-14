package app_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/configs"
	"github.com/slipynil/kontur-kcr/services/identity/internal/app"
	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

// stubStore — минимальный Store для тестов wiring-слоя.
type stubStore struct {
	created []string
}

func (s *stubStore) GetByLogin(_ context.Context, _ string) (identity.User, error) {
	return identity.User{}, identity.ErrNotFound
}

func (s *stubStore) GetByID(_ context.Context, _ uuid.UUID) (identity.User, error) {
	return identity.User{}, identity.ErrNotFound
}

func (s *stubStore) GetByIDs(_ context.Context, _ []uuid.UUID) ([]identity.User, error) {
	return nil, nil
}

func (s *stubStore) List(_ context.Context, _, _ int) ([]identity.User, int, error) {
	return nil, 0, nil
}

func (s *stubStore) Create(_ context.Context, login, passwordHash string, role auth.Role) (identity.User, error) {
	s.created = append(s.created, login+"|"+string(role)+"|"+passwordHash)
	return identity.User{ID: uuid.New(), Login: login, Role: role, IsActive: true}, nil
}

func testConfig(t *testing.T) *configs.Config {
	t.Helper()
	configs.Initialize(".env", t.TempDir(), "env")
	t.Setenv("JWT_SECRET", "0123456789abcdef0123456789abcdef")
	t.Setenv("GATEWAY_PORT", "0")
	return configs.GetConfig()
}

func TestRunFailsWithoutJWTSecret(t *testing.T) {
	configs.Initialize(".env", t.TempDir(), "env")
	t.Setenv("JWT_SECRET", "")

	err := app.Run(context.Background(), configs.GetConfig())
	if err == nil {
		t.Fatal("Run = nil, want error for missing JWT secret")
	}
	if !strings.Contains(err.Error(), "token manager") {
		t.Errorf("error = %v, want token manager failure", err)
	}
}

func TestRunFailsOnUnreachableDB(t *testing.T) {
	configs.Initialize(".env", t.TempDir(), "env")
	t.Setenv("JWT_SECRET", "0123456789abcdef0123456789abcdef")
	// Порт 1 закрыт почти везде — пинг падает быстро.
	t.Setenv("IDENTITY_DSN", "postgres://identity:identity@127.0.0.1:1/identity?sslmode=disable")

	err := app.Run(context.Background(), configs.GetConfig())
	if err == nil {
		t.Fatal("Run = nil, want error for unreachable identity db")
	}
	if !strings.Contains(err.Error(), "identity db") {
		t.Errorf("error = %v, want identity db failure", err)
	}
}

func TestRunStopsOnContextCancel(t *testing.T) {
	cfg := testConfig(t)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- app.Run(ctx, cfg, app.WithStore(&stubStore{}))
	}()

	time.Sleep(150 * time.Millisecond) // даём серверу подняться
	cancel()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Run returned error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Run did not stop after context cancel")
	}
}

func TestSeedRequiresEnv(t *testing.T) {
	configs.Initialize(".env", t.TempDir(), "env")

	err := app.Seed(context.Background(), configs.GetConfig(), app.WithStore(&stubStore{}))
	if err == nil {
		t.Fatal("Seed = nil, want error for missing env")
	}
	if !strings.Contains(err.Error(), "ADMIN_LOGIN") {
		t.Errorf("error = %v, want usage hint", err)
	}
}

func TestSeedCreatesAdmin(t *testing.T) {
	cfg := testConfig(t)
	t.Setenv("ADMIN_LOGIN", "admin")
	t.Setenv("ADMIN_PASSWORD", "password123")
	store := &stubStore{}

	if err := app.Seed(context.Background(), cfg, app.WithStore(store)); err != nil {
		t.Fatal(err)
	}
	if len(store.created) != 1 {
		t.Fatalf("created = %v, want one user", store.created)
	}
	if !strings.HasPrefix(store.created[0], "admin|ADMIN|") {
		t.Errorf("created record = %q", store.created[0])
	}
}

func TestSeedLoginTaken(t *testing.T) {
	cfg := testConfig(t)
	t.Setenv("ADMIN_LOGIN", "admin")
	t.Setenv("ADMIN_PASSWORD", "password123")

	taken := &takenStore{}
	err := app.Seed(context.Background(), cfg, app.WithStore(taken))
	if err == nil {
		t.Fatal("Seed = nil, want ErrLoginTaken")
	}
}

func TestSeedValidation(t *testing.T) {
	cfg := testConfig(t)
	t.Setenv("ADMIN_LOGIN", "admin")
	t.Setenv("ADMIN_PASSWORD", "short")

	store := &stubStore{}
	if err := app.Seed(context.Background(), cfg, app.WithStore(store)); err == nil {
		t.Fatal("Seed = nil, want validation error")
	}
	if len(store.created) != 0 {
		t.Fatalf("created = %v, want none", store.created)
	}
}

// takenStore всегда отвечает занятым логином.
type takenStore struct{ stubStore }

func (s *takenStore) Create(_ context.Context, _, _ string, _ auth.Role) (identity.User, error) {
	return identity.User{}, identity.ErrLoginTaken
}
