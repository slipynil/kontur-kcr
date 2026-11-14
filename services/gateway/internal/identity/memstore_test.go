package identity_test

import (
	"context"
	"sort"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

// memStore — эксплуатационная реализация Store для тестов обработчиков.
type memStore struct {
	mu    sync.Mutex
	users []identity.User
	// fail, если задан, возвращается любым методом как техническая ошибка.
	fail error
	// lastListLimit/lastListOffset запоминают аргументы последнего List.
	lastListLimit  int
	lastListOffset int
}

func (s *memStore) GetByLogin(_ context.Context, login string) (identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fail != nil {
		return identity.User{}, s.fail
	}
	for _, user := range s.users {
		if user.Login == login {
			return user, nil
		}
	}
	return identity.User{}, identity.ErrNotFound
}

func (s *memStore) GetByID(_ context.Context, id uuid.UUID) (identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fail != nil {
		return identity.User{}, s.fail
	}
	for _, user := range s.users {
		if user.ID == id {
			return user, nil
		}
	}
	return identity.User{}, identity.ErrNotFound
}

func (s *memStore) GetByIDs(_ context.Context, ids []uuid.UUID) ([]identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fail != nil {
		return nil, s.fail
	}
	wanted := make(map[uuid.UUID]struct{}, len(ids))
	for _, id := range ids {
		wanted[id] = struct{}{}
	}
	users := make([]identity.User, 0, len(ids))
	for _, user := range s.users {
		if _, ok := wanted[user.ID]; ok {
			users = append(users, user)
		}
	}
	sort.Slice(users, func(i, j int) bool { return users[i].CreatedAt.Before(users[j].CreatedAt) })
	return users, nil
}

func (s *memStore) List(_ context.Context, limit, offset int) ([]identity.User, int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fail != nil {
		return nil, 0, s.fail
	}
	s.lastListLimit = limit
	s.lastListOffset = offset

	sorted := make([]identity.User, len(s.users))
	copy(sorted, s.users)
	sort.Slice(sorted, func(i, j int) bool {
		if sorted[i].CreatedAt.Equal(sorted[j].CreatedAt) {
			return sorted[i].ID.String() > sorted[j].ID.String()
		}
		return sorted[i].CreatedAt.After(sorted[j].CreatedAt)
	})

	start := offset
	if start > len(sorted) {
		start = len(sorted)
	}
	end := start + limit
	if end > len(sorted) {
		end = len(sorted)
	}
	return sorted[start:end], len(sorted), nil
}

func (s *memStore) Create(_ context.Context, login, passwordHash string, role auth.Role) (identity.User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fail != nil {
		return identity.User{}, s.fail
	}
	for _, user := range s.users {
		if user.Login == login {
			return identity.User{}, identity.ErrLoginTaken
		}
	}
	user := identity.User{
		ID:           uuid.New(),
		Login:        login,
		PasswordHash: passwordHash,
		Role:         role,
		IsActive:     true,
		CreatedAt:    time.Now().UTC(),
	}
	s.users = append(s.users, user)
	return user, nil
}
