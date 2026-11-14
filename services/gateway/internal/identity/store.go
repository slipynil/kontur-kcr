package identity

import (
	"context"
	"errors"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

// Доменные ошибки хранилища.
var (
	// ErrNotFound — пользователь не найден.
	ErrNotFound = errors.New("user not found")
	// ErrLoginTaken — логин уже занят (unique users.login).
	ErrLoginTaken = errors.New("login already taken")
)

// Store — хранилище учётных записей Identity DB.
type Store interface {
	// GetByLogin возвращает пользователя по логину или ErrNotFound.
	GetByLogin(ctx context.Context, login string) (User, error)
	// GetByID возвращает пользователя по id или ErrNotFound.
	GetByID(ctx context.Context, id uuid.UUID) (User, error)
	// GetByIDs возвращает найденных пользователей; отсутствующие id пропускаются.
	GetByIDs(ctx context.Context, ids []uuid.UUID) ([]User, error)
	// List возвращает страницу реестра и общее число пользователей.
	List(ctx context.Context, limit, offset int) (items []User, total int, err error)
	// Create добавляет пользователя; занятый логин возвращает ErrLoginTaken.
	Create(ctx context.Context, login, passwordHash string, role auth.Role) (User, error)
}

const (
	// MinPasswordLength — минимальная длина пароля при создании.
	MinPasswordLength = 8
	// MaxPasswordLength — максимальная длина пароля (bcrypt обрезает свыше 72 байт).
	MaxPasswordLength = 72
	// MaxLoginLength — максимальная длина логина.
	MaxLoginLength = 64
)
