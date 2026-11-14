// Package auth содержит модель ролей, идентичность запроса и выпуск/проверку JWT
// Gateway / Identity Service (spec.md §1.1, §3.1).
package auth

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

// Роли пользователей (spec.md §2.1).
const (
	RoleAdmin    Role = "ADMIN"
	RoleOperator Role = "OPERATOR"
)

// Role — роль пользователя публичного API.
type Role string

// Valid проверяет, что роль входит в справочник.
func (r Role) Valid() bool {
	return r == RoleAdmin || r == RoleOperator
}

// Identity — проверенная идентичность запроса после JWT-аутентификации.
type Identity struct {
	UserID uuid.UUID
	Role   Role
}

// ErrInvalidToken — токен не прошёл проверку (подпись, алгоритм, срок, клеймы).
var ErrInvalidToken = errors.New("invalid token")

// MinSecretLength — минимальная длина секрета подписи.
const MinSecretLength = 32

type identityKey struct{}

// WithIdentity кладёт идентичность в контекст запроса.
func WithIdentity(ctx context.Context, identity Identity) context.Context {
	return context.WithValue(ctx, identityKey{}, identity)
}

// FromContext достаёт идентичность из контекста запроса.
func FromContext(ctx context.Context) (Identity, bool) {
	identity, ok := ctx.Value(identityKey{}).(Identity)
	return identity, ok
}

// TokenManager выпускает и проверяет JWT HS256.
type TokenManager struct {
	secret []byte
	ttl    time.Duration
	now    func() time.Time
}

// NewTokenManager создаёт менеджер токенов. Секрет обязан быть достаточно длинным,
// ttl — положительным; ошибки возвращаются до старта сервера.
func NewTokenManager(secret string, ttl time.Duration) (*TokenManager, error) {
	if len(secret) < MinSecretLength {
		return nil, fmt.Errorf("jwt secret must be at least %d characters", MinSecretLength)
	}
	if ttl <= 0 {
		return nil, errors.New("jwt ttl must be positive")
	}
	return &TokenManager{
		secret: []byte(secret),
		ttl:    ttl,
		now:    time.Now,
	}, nil
}

// TTL возвращает время жизни токена.
func (m *TokenManager) TTL() time.Duration {
	return m.ttl
}

// tokenClaims — клеймы токена: роль пользователя плюс стандартные поля.
type tokenClaims struct {
	Role Role `json:"role"`
	jwt.RegisteredClaims
}

// Issue выпускает токен для пользователя и возвращает момент истечения.
func (m *TokenManager) Issue(userID uuid.UUID, role Role) (string, time.Time, error) {
	now := m.now()
	expiresAt := now.Add(m.ttl)
	claims := tokenClaims{
		Role: role,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   userID.String(),
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(expiresAt),
		},
	}
	signed, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(m.secret)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("sign token: %w", err)
	}
	return signed, expiresAt, nil
}

// Verify проверяет подпись, алгоритм, срок и клеймы, возвращая идентичность.
func (m *TokenManager) Verify(tokenString string) (Identity, error) {
	claims := &tokenClaims{}
	// WithValidMethods закрывает атаки подмены алгоритма (включая "none").
	token, err := jwt.ParseWithClaims(tokenString, claims, func(*jwt.Token) (any, error) {
		return m.secret, nil
	}, jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}))
	if err != nil || !token.Valid {
		return Identity{}, ErrInvalidToken
	}
	userID, err := uuid.Parse(claims.Subject)
	if err != nil || !claims.Role.Valid() {
		return Identity{}, ErrInvalidToken
	}
	return Identity{UserID: userID, Role: claims.Role}, nil
}
