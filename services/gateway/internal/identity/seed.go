package identity

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

// SeedAdmin создаёт первого администратора; используется задачей seed-admin (cmd/seed).
// Учётные данные передаются переменными окружения и в БД не попадают в открытом виде.
func SeedAdmin(ctx context.Context, store Store, login, password string) (User, error) {
	login = strings.TrimSpace(login)
	if login == "" {
		return User{}, errors.New("admin login is required")
	}
	if len(login) > MaxLoginLength {
		return User{}, fmt.Errorf("admin login must be at most %d characters", MaxLoginLength)
	}
	if len(password) < MinPasswordLength {
		return User{}, fmt.Errorf("admin password must be at least %d characters", MinPasswordLength)
	}
	if len(password) > MaxPasswordLength {
		return User{}, fmt.Errorf("admin password must be at most %d characters", MaxPasswordLength)
	}

	passwordHash, err := HashPassword(password)
	if err != nil {
		return User{}, err
	}
	user, err := store.Create(ctx, login, passwordHash, auth.RoleAdmin)
	if err != nil {
		return User{}, fmt.Errorf("create admin user: %w", err)
	}
	return user, nil
}
