package identity_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

func TestSeedAdminCreatesAdmin(t *testing.T) {
	store := &memStore{}

	user, err := identity.SeedAdmin(context.Background(), store, "  admin  ", "password123")
	if err != nil {
		t.Fatal(err)
	}
	if user.Login != "admin" {
		t.Errorf("login = %q, want trimmed admin", user.Login)
	}
	if user.Role != auth.RoleAdmin {
		t.Errorf("role = %q, want ADMIN", user.Role)
	}
	if !identity.ComparePassword(user.PasswordHash, "password123") {
		t.Error("stored hash does not verify the password")
	}
}

func TestSeedAdminValidation(t *testing.T) {
	store := &memStore{}

	tests := []struct {
		name     string
		login    string
		password string
	}{
		{name: "empty login", login: "  ", password: "password123"},
		{name: "login too long", login: strings.Repeat("a", 65), password: "password123"},
		{name: "short password", login: "admin", password: "1234567"},
		{name: "long password", login: "admin", password: strings.Repeat("a", 73)},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := identity.SeedAdmin(context.Background(), store, tt.login, tt.password); err == nil {
				t.Fatal("SeedAdmin = nil error, want validation error")
			}
		})
	}
	if len(store.users) != 0 {
		t.Fatalf("store must stay empty, got %d users", len(store.users))
	}
}

func TestSeedAdminLoginTaken(t *testing.T) {
	store := &memStore{}

	_, err := identity.SeedAdmin(context.Background(), store, "admin", "password123")
	if err != nil {
		t.Fatal(err)
	}
	_, err = identity.SeedAdmin(context.Background(), store, "admin", "password456")
	if !errors.Is(err, identity.ErrLoginTaken) {
		t.Fatalf("error = %v, want ErrLoginTaken", err)
	}
}
