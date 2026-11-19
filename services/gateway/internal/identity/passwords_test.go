package identity_test

import (
	"strings"
	"testing"

	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

func TestHashPasswordRoundtrip(t *testing.T) {
	hash, err := identity.HashPassword("password123")
	if err != nil {
		t.Fatal(err)
	}
	if hash == "password123" {
		t.Fatal("password hash must not equal the plain password")
	}
	if !identity.ComparePassword(hash, "password123") {
		t.Error("ComparePassword(correct) = false, want true")
	}
	if identity.ComparePassword(hash, "wrong-password") {
		t.Error("ComparePassword(wrong) = true, want false")
	}
}

func TestHashPasswordTooLong(t *testing.T) {
	// bcrypt не принимает пароли длиннее 72 байт.
	if _, err := identity.HashPassword(strings.Repeat("a", 73)); err == nil {
		t.Fatal("HashPassword(73 bytes) = nil error, want error")
	}
}

func TestComparePasswordMalformedHash(t *testing.T) {
	if identity.ComparePassword("not-a-bcrypt-hash", "password123") {
		t.Fatal("ComparePassword(malformed hash) = true, want false")
	}
}
