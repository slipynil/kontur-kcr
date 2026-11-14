package identity

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
)

// pgUniqueViolation — код нарушения ограничения уникальности PostgreSQL.
const pgUniqueViolation = "23505"

// DB — минимальный интерфейс, общий для pgxpool.Pool и pgxmock (тесты).
type DB interface {
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

// PostgresStore — реализация Store на pgx.
type PostgresStore struct {
	db DB
}

// userColumns — список колонок таблицы users для SELECT/RETURNING.
const userColumns = "id, login, password_hash, role, is_active, created_at"

// Open создаёт пул соединений с Identity DB и проверяет доступность.
func Open(ctx context.Context, dsn string) (*PostgresStore, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("parse identity dsn: %w", err)
	}
	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping identity db: %w", err)
	}
	return &PostgresStore{db: pool}, nil
}

// NewPostgresStore оборачивает готовое соединение или пул (в тестах — pgxmock).
func NewPostgresStore(db DB) *PostgresStore {
	return &PostgresStore{db: db}
}

// Close освобождает пул соединений.
func (s *PostgresStore) Close() {
	if pool, ok := s.db.(*pgxpool.Pool); ok {
		pool.Close()
	}
}

// GetByLogin implements Store.
func (s *PostgresStore) GetByLogin(ctx context.Context, login string) (User, error) {
	row := s.db.QueryRow(
		ctx,
		"SELECT "+userColumns+" FROM users WHERE login = $1",
		login,
	)
	return scanUser(row)
}

// GetByID implements Store.
func (s *PostgresStore) GetByID(ctx context.Context, id uuid.UUID) (User, error) {
	row := s.db.QueryRow(
		ctx,
		"SELECT "+userColumns+" FROM users WHERE id = $1",
		id,
	)
	return scanUser(row)
}

// GetByIDs implements Store.
func (s *PostgresStore) GetByIDs(ctx context.Context, ids []uuid.UUID) ([]User, error) {
	rows, err := s.db.Query(
		ctx,
		"SELECT "+userColumns+" FROM users WHERE id = ANY($1) ORDER BY created_at, id",
		ids,
	)
	if err != nil {
		return nil, fmt.Errorf("query users by ids: %w", err)
	}
	defer rows.Close()

	users := make([]User, 0, len(ids))
	for rows.Next() {
		user, err := scanUser(rows)
		if err != nil {
			return nil, err
		}
		users = append(users, user)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate users by ids: %w", err)
	}
	return users, nil
}

// List implements Store.
func (s *PostgresStore) List(ctx context.Context, limit, offset int) ([]User, int, error) {
	rows, err := s.db.Query(
		ctx,
		"SELECT "+userColumns+", count(*) OVER () AS total "+
			"FROM users ORDER BY created_at DESC, id DESC LIMIT $1 OFFSET $2",
		limit, offset,
	)
	if err != nil {
		return nil, 0, fmt.Errorf("query users page: %w", err)
	}
	defer rows.Close()

	users := make([]User, 0, limit)
	total := 0
	for rows.Next() {
		var user User
		if err := rows.Scan(
			&user.ID, &user.Login, &user.PasswordHash, &user.Role, &user.IsActive, &user.CreatedAt, &total,
		); err != nil {
			return nil, 0, fmt.Errorf("scan user row: %w", err)
		}
		users = append(users, user)
	}
	if err := rows.Err(); err != nil {
		return nil, 0, fmt.Errorf("iterate users page: %w", err)
	}
	return users, total, nil
}

// Create implements Store.
func (s *PostgresStore) Create(ctx context.Context, login, passwordHash string, role auth.Role) (User, error) {
	row := s.db.QueryRow(
		ctx,
		"INSERT INTO users (id, login, password_hash, role) "+
			"VALUES (gen_random_uuid(), $1, $2, $3) "+
			"RETURNING "+userColumns,
		login, passwordHash, role,
	)
	user, err := scanUser(row)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == pgUniqueViolation {
			return User{}, ErrLoginTaken
		}
		return User{}, err
	}
	return user, nil
}

// rowScanner — общий интерфейс pgx.Row и pgx.Rows.
type rowScanner interface {
	Scan(dest ...any) error
}

func scanUser(row rowScanner) (User, error) {
	var user User
	err := row.Scan(
		&user.ID,
		&user.Login,
		&user.PasswordHash,
		&user.Role,
		&user.IsActive,
		&user.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return User{}, ErrNotFound
	}
	if err != nil {
		return User{}, fmt.Errorf("scan user: %w", err)
	}
	return user, nil
}
