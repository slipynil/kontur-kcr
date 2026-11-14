// Package identity хранит учётные записи пользователей Identity DB (spec.md §2.1),
// публичные эндпоинты авторизации и пользователей и внутренний API для сервисов.
package identity

import (
	"time"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

// User — учётная запись пользователя (таблица users, docs/db/ddl/identity.sql).
type User struct {
	ID           uuid.UUID
	Login        string
	PasswordHash string
	Role         auth.Role
	IsActive     bool
	CreatedAt    time.Time
}
