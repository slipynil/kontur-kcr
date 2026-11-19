package identity_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/pashagolub/pgxmock/v5"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/identity"
)

const (
	selectByLoginSQL = "SELECT id, login, password_hash, role, is_active, created_at FROM users WHERE login = $1"
	selectByIDSQL    = "SELECT id, login, password_hash, role, is_active, created_at FROM users WHERE id = $1"
	selectByIDsSQL   = "SELECT id, login, password_hash, role, is_active, created_at FROM users WHERE id = ANY($1) ORDER BY created_at, id"
	listSQL          = "SELECT id, login, password_hash, role, is_active, created_at, count(*) OVER () AS total FROM users ORDER BY created_at DESC, id DESC LIMIT $1 OFFSET $2"
	insertSQL        = "INSERT INTO users (id, login, password_hash, role) VALUES (gen_random_uuid(), $1, $2, $3) RETURNING id, login, password_hash, role, is_active, created_at"
)

func userColumns() []string {
	return []string{"id", "login", "password_hash", "role", "is_active", "created_at"}
}

func newMockStore(t *testing.T) (pgxmock.PgxPoolIface, *identity.PostgresStore) {
	t.Helper()
	mock, err := pgxmock.NewPool(pgxmock.QueryMatcherOption(pgxmock.QueryMatcherEqual))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := mock.ExpectationsWereMet(); err != nil {
			t.Errorf("unmet pgxmock expectations: %v", err)
		}
		mock.Close()
	})
	return mock, identity.NewPostgresStore(mock)
}

var testCreatedAt = time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)

func TestOpenRejectsMalformedDSN(t *testing.T) {
	if _, err := identity.Open(context.Background(), "postgres://%zz"); err == nil {
		t.Fatal("Open(malformed dsn) = nil error, want parse error")
	}
}

func TestPostgresCloseIgnoresNonPoolDB(t *testing.T) {
	_, store := newMockStore(t)
	// pgxmock — не пул: Close ничего не делает и не паникует.
	store.Close()
}

func TestPostgresCloseClosesPool(t *testing.T) {
	// connect_timeout сокращает ожидание фонового подключения при Close.
	pool, err := pgxpool.New(context.Background(), "postgres://identity:identity@127.0.0.1:1/identity?sslmode=disable&connect_timeout=1")
	if err != nil {
		t.Fatal(err)
	}
	identity.NewPostgresStore(pool).Close()
}

func TestPostgresGetByLogin(t *testing.T) {
	mock, store := newMockStore(t)
	id := uuid.MustParse("11111111-1111-1111-1111-111111111111")

	rows := pgxmock.NewRows(userColumns()).
		AddRow(id, "ivanov", "$2a$10$hash", auth.RoleAdmin, true, testCreatedAt)
	mock.ExpectQuery(selectByLoginSQL).WithArgs("ivanov").WillReturnRows(rows)

	user, err := store.GetByLogin(context.Background(), "ivanov")
	if err != nil {
		t.Fatal(err)
	}
	if user.ID != id || user.Login != "ivanov" || user.Role != auth.RoleAdmin || !user.IsActive {
		t.Errorf("user = %+v", user)
	}
	if user.PasswordHash != "$2a$10$hash" || !user.CreatedAt.Equal(testCreatedAt) {
		t.Errorf("user hash/created = %q / %v", user.PasswordHash, user.CreatedAt)
	}
}

func TestPostgresGetByLoginNotFound(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(selectByLoginSQL).WithArgs("ghost").WillReturnError(pgx.ErrNoRows)

	_, err := store.GetByLogin(context.Background(), "ghost")
	if !errors.Is(err, identity.ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound", err)
	}
}

func TestPostgresGetByLoginFailure(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(selectByLoginSQL).WithArgs("ivanov").WillReturnError(errors.New("connection refused"))

	_, err := store.GetByLogin(context.Background(), "ivanov")
	if err == nil || errors.Is(err, identity.ErrNotFound) {
		t.Fatalf("error = %v, want technical error", err)
	}
}

func TestPostgresGetByID(t *testing.T) {
	mock, store := newMockStore(t)
	id := uuid.MustParse("22222222-2222-2222-2222-222222222222")

	rows := pgxmock.NewRows(userColumns()).
		AddRow(id, "ivanov", "$2a$10$hash", auth.RoleOperator, true, testCreatedAt)
	mock.ExpectQuery(selectByIDSQL).WithArgs(id).WillReturnRows(rows)

	user, err := store.GetByID(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	if user.ID != id || user.Role != auth.RoleOperator {
		t.Errorf("user = %+v", user)
	}
}

func TestPostgresGetByIDNotFound(t *testing.T) {
	mock, store := newMockStore(t)
	id := uuid.MustParse("33333333-3333-3333-3333-333333333333")
	mock.ExpectQuery(selectByIDSQL).WithArgs(id).WillReturnError(pgx.ErrNoRows)

	_, err := store.GetByID(context.Background(), id)
	if !errors.Is(err, identity.ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound", err)
	}
}

func TestPostgresGetByIDs(t *testing.T) {
	mock, store := newMockStore(t)
	first := uuid.MustParse("44444444-4444-4444-4444-444444444444")
	second := uuid.MustParse("55555555-5555-5555-5555-555555555555")

	rows := pgxmock.NewRows(userColumns()).
		AddRow(first, "ivanov", "$2a$10$hash", auth.RoleAdmin, true, testCreatedAt).
		AddRow(second, "petrov", "$2a$10$hash", auth.RoleOperator, false, testCreatedAt)
	mock.ExpectQuery(selectByIDsSQL).WithArgs(pgxmock.AnyArg()).WillReturnRows(rows)

	users, err := store.GetByIDs(context.Background(), []uuid.UUID{first, second})
	if err != nil {
		t.Fatal(err)
	}
	if len(users) != 2 {
		t.Fatalf("users = %d, want 2", len(users))
	}
	if users[0].ID != first || users[1].ID != second {
		t.Errorf("order = %v, %v", users[0].ID, users[1].ID)
	}
}

func TestPostgresGetByIDsFailure(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(selectByIDsSQL).WithArgs(pgxmock.AnyArg()).WillReturnError(errors.New("boom"))

	_, err := store.GetByIDs(context.Background(), []uuid.UUID{uuid.New()})
	if err == nil {
		t.Fatal("error = nil, want technical error")
	}
}

func TestPostgresGetByIDsScanFailure(t *testing.T) {
	mock, store := newMockStore(t)
	// Колонок меньше, чем ожидает сканирование, — Scan падает.
	mock.ExpectQuery(selectByIDsSQL).WithArgs(pgxmock.AnyArg()).
		WillReturnRows(pgxmock.NewRows([]string{"id", "login"}).
			AddRow(uuid.New(), "ivanov"))

	_, err := store.GetByIDs(context.Background(), []uuid.UUID{uuid.New()})
	if err == nil || errors.Is(err, identity.ErrNotFound) {
		t.Fatalf("error = %v, want scan error", err)
	}
}

func TestPostgresList(t *testing.T) {
	mock, store := newMockStore(t)
	first := uuid.MustParse("66666666-6666-6666-6666-666666666666")
	second := uuid.MustParse("77777777-7777-7777-7777-777777777777")

	rows := pgxmock.NewRows(append(userColumns(), "total")).
		AddRow(first, "ivanov", "$2a$10$hash", auth.RoleAdmin, true, testCreatedAt, 2).
		AddRow(second, "petrov", "$2a$10$hash", auth.RoleOperator, true, testCreatedAt, 2)
	mock.ExpectQuery(listSQL).WithArgs(20, 0).WillReturnRows(rows)

	users, total, err := store.List(context.Background(), 20, 0)
	if err != nil {
		t.Fatal(err)
	}
	if total != 2 || len(users) != 2 {
		t.Fatalf("total = %d, users = %d, want 2, 2", total, len(users))
	}
}

func TestPostgresGetByIDsIterationFailure(t *testing.T) {
	mock, store := newMockStore(t)
	id := uuid.MustParse("99999999-9999-9999-9999-999999999999")
	rows := pgxmock.NewRows(userColumns()).
		AddRow(id, "ivanov", "$2a$10$hash", auth.RoleAdmin, true, testCreatedAt).
		RowError(1, errors.New("rows iteration failed"))
	mock.ExpectQuery(selectByIDsSQL).WithArgs(pgxmock.AnyArg()).WillReturnRows(rows)

	_, err := store.GetByIDs(context.Background(), []uuid.UUID{id})
	if err == nil {
		t.Fatal("error = nil, want rows iteration error")
	}
}

func TestPostgresListIterationFailure(t *testing.T) {
	mock, store := newMockStore(t)
	rows := pgxmock.NewRows(append(userColumns(), "total")).
		AddRow(uuid.New(), "ivanov", "$2a$10$hash", auth.RoleAdmin, true, testCreatedAt, 1).
		RowError(1, errors.New("rows iteration failed"))
	mock.ExpectQuery(listSQL).WithArgs(20, 0).WillReturnRows(rows)

	_, _, err := store.List(context.Background(), 20, 0)
	if err == nil {
		t.Fatal("error = nil, want rows iteration error")
	}
}

func TestPostgresListEmpty(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(listSQL).WithArgs(20, 40).WillReturnRows(
		pgxmock.NewRows(append(userColumns(), "total")))

	users, total, err := store.List(context.Background(), 20, 40)
	if err != nil {
		t.Fatal(err)
	}
	if total != 0 || len(users) != 0 {
		t.Fatalf("total = %d, users = %d, want 0, 0", total, len(users))
	}
}

func TestPostgresListFailure(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(listSQL).WithArgs(20, 0).WillReturnError(errors.New("boom"))

	_, _, err := store.List(context.Background(), 20, 0)
	if err == nil {
		t.Fatal("error = nil, want technical error")
	}
}

func TestPostgresListScanFailure(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(listSQL).WithArgs(20, 0).
		WillReturnRows(pgxmock.NewRows([]string{"id"}).AddRow(uuid.New()))

	_, _, err := store.List(context.Background(), 20, 0)
	if err == nil {
		t.Fatal("error = nil, want scan error")
	}
}

func TestPostgresCreate(t *testing.T) {
	mock, store := newMockStore(t)
	id := uuid.MustParse("88888888-8888-8888-8888-888888888888")

	rows := pgxmock.NewRows(userColumns()).
		AddRow(id, "sidorov", "$2a$10$hash", auth.RoleOperator, true, testCreatedAt)
	mock.ExpectQuery(insertSQL).
		WithArgs("sidorov", "$2a$10$hash", auth.RoleOperator).
		WillReturnRows(rows)

	user, err := store.Create(context.Background(), "sidorov", "$2a$10$hash", auth.RoleOperator)
	if err != nil {
		t.Fatal(err)
	}
	if user.ID != id || user.Login != "sidorov" || !user.IsActive {
		t.Errorf("user = %+v", user)
	}
}

func TestPostgresCreateLoginTaken(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(insertSQL).
		WithArgs("ivanov", "$2a$10$hash", auth.RoleOperator).
		WillReturnError(&pgconn.PgError{Code: "23505", ConstraintName: "users_login_key"})

	_, err := store.Create(context.Background(), "ivanov", "$2a$10$hash", auth.RoleOperator)
	if !errors.Is(err, identity.ErrLoginTaken) {
		t.Fatalf("error = %v, want ErrLoginTaken", err)
	}
}

func TestPostgresCreateFailure(t *testing.T) {
	mock, store := newMockStore(t)
	mock.ExpectQuery(insertSQL).
		WithArgs("sidorov", "$2a$10$hash", auth.RoleOperator).
		WillReturnError(errors.New("connection refused"))

	_, err := store.Create(context.Background(), "sidorov", "$2a$10$hash", auth.RoleOperator)
	if err == nil || errors.Is(err, identity.ErrLoginTaken) {
		t.Fatalf("error = %v, want technical error", err)
	}
}
