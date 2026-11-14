package auth

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

const testSecret = "0123456789abcdef0123456789abcdef"

func TestNewTokenManagerValidation(t *testing.T) {
	tests := []struct {
		name    string
		secret  string
		ttl     time.Duration
		wantErr bool
	}{
		{name: "valid", secret: testSecret, ttl: time.Hour},
		{name: "short secret", secret: "short", ttl: time.Hour, wantErr: true},
		{name: "empty secret", secret: "", ttl: time.Hour, wantErr: true},
		{name: "zero ttl", secret: testSecret, ttl: 0, wantErr: true},
		{name: "negative ttl", secret: testSecret, ttl: -time.Minute, wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := NewTokenManager(tt.secret, tt.ttl)
			if (err != nil) != tt.wantErr {
				t.Fatalf("NewTokenManager(%q, %v) error = %v, wantErr %v", tt.secret, tt.ttl, err, tt.wantErr)
			}
		})
	}
}

func TestIssueVerifyRoundtrip(t *testing.T) {
	manager, err := NewTokenManager(testSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	userID := uuid.MustParse("11111111-1111-1111-1111-111111111111")

	token, expiresAt, err := manager.Issue(userID, RoleOperator)
	if err != nil {
		t.Fatal(err)
	}
	if time.Until(expiresAt) <= 0 {
		t.Fatalf("expiresAt must be in the future, got %v", expiresAt)
	}

	identity, err := manager.Verify(token)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if identity.UserID != userID {
		t.Errorf("UserID = %v, want %v", identity.UserID, userID)
	}
	if identity.Role != RoleOperator {
		t.Errorf("Role = %q, want %q", identity.Role, RoleOperator)
	}
}

func TestVerifyExpired(t *testing.T) {
	manager, err := NewTokenManager(testSecret, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	// Время выдачи смещено в прошлое: токен уже истёк.
	manager.now = func() time.Time { return time.Now().Add(-2 * time.Hour) }

	token, _, err := manager.Issue(uuid.MustParse("22222222-2222-2222-2222-222222222222"), RoleAdmin)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Verify(token); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("Verify(expired) error = %v, want ErrInvalidToken", err)
	}
}

func TestVerifyWrongSecret(t *testing.T) {
	issuer, err := NewTokenManager(testSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	verifier, err := NewTokenManager("ffffffffffffffffffffffffffffffff", time.Hour)
	if err != nil {
		t.Fatal(err)
	}

	token, _, err := issuer.Issue(uuid.MustParse("33333333-3333-3333-3333-333333333333"), RoleAdmin)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := verifier.Verify(token); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("Verify(other secret) error = %v, want ErrInvalidToken", err)
	}
}

func TestVerifyGarbageAndTampered(t *testing.T) {
	manager, err := NewTokenManager(testSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	garbage := []string{"", "not-a-token", "eyJhbGciOiJIUzI1NiJ9.e30.", "abc.def.ghi"}
	for _, token := range garbage {
		if _, err := manager.Verify(token); !errors.Is(err, ErrInvalidToken) {
			t.Fatalf("Verify(%q) error = %v, want ErrInvalidToken", token, err)
		}
	}
}

func TestVerifyRejectsAlgNone(t *testing.T) {
	manager, err := NewTokenManager(testSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	// Токен с alg=none не должен проходить, даже если подпись пустая.
	token, err := jwt.NewWithClaims(jwt.SigningMethodNone, tokenClaims{
		Role: RoleAdmin,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   "44444444-4444-4444-4444-444444444444",
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
		},
	}).SignedString(jwt.UnsafeAllowNoneSignatureType)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Verify(token); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("Verify(alg=none) error = %v, want ErrInvalidToken", err)
	}
}

func TestVerifyRejectsInvalidClaims(t *testing.T) {
	manager, err := NewTokenManager(testSecret, time.Hour)
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name   string
		claims jwt.Claims
	}{
		{
			name: "no role",
			claims: jwt.RegisteredClaims{
				Subject:   "55555555-5555-5555-5555-555555555555",
				ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
			},
		},
		{
			name: "unknown role",
			claims: tokenClaims{
				Role: "SUPERUSER",
				RegisteredClaims: jwt.RegisteredClaims{
					Subject:   "55555555-5555-5555-5555-555555555555",
					ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
				},
			},
		},
		{
			name: "subject is not uuid",
			claims: tokenClaims{
				Role: RoleAdmin,
				RegisteredClaims: jwt.RegisteredClaims{
					Subject:   "not-an-uuid",
					ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
				},
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, tt.claims).SignedString([]byte(testSecret))
			if err != nil {
				t.Fatal(err)
			}
			if _, err := manager.Verify(token); !errors.Is(err, ErrInvalidToken) {
				t.Fatalf("Verify(%s) error = %v, want ErrInvalidToken", tt.name, err)
			}
		})
	}
}

func TestIdentityContext(t *testing.T) {
	identity := Identity{UserID: uuid.MustParse("66666666-6666-6666-6666-666666666666"), Role: RoleAdmin}

	ctx := WithIdentity(context.Background(), identity)
	got, ok := FromContext(ctx)
	if !ok || got != identity {
		t.Fatalf("FromContext = %+v, %v; want %+v, true", got, ok, identity)
	}

	if _, ok := FromContext(context.Background()); ok {
		t.Fatal("FromContext(empty) = true, want false")
	}
}

func TestRoleValid(t *testing.T) {
	for _, role := range []Role{RoleAdmin, RoleOperator} {
		if !role.Valid() {
			t.Errorf("Role(%q).Valid() = false, want true", role)
		}
	}
	for _, role := range []Role{"", "ADMIN ", "admin", "SUPERUSER"} {
		if role.Valid() {
			t.Errorf("Role(%q).Valid() = true, want false", role)
		}
	}
}
