package identity

import (
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

// Handlers — HTTP-обработчики авторизации и пользователей
// (public-api.yaml: Auth, Users; internal/gateway-identity.yaml: Users).
type Handlers struct {
	Store  Store
	Tokens *auth.TokenManager
}

// Параметры пагинации реестра пользователей (public-api.yaml: Page, PageSize).
const (
	defaultPage     = 1
	defaultPageSize = 20
	maxPageSize     = 100
)

// userResponse — публичное представление пользователя (schema User).
type userResponse struct {
	ID        uuid.UUID `json:"id"`
	Login     string    `json:"login"`
	Role      auth.Role `json:"role"`
	IsActive  bool      `json:"is_active"`
	CreatedAt string    `json:"created_at"`
}

func toUserResponse(user User) userResponse {
	return userResponse{
		ID:        user.ID,
		Login:     user.Login,
		Role:      user.Role,
		IsActive:  user.IsActive,
		CreatedAt: user.CreatedAt.UTC().Format(time.RFC3339),
	}
}

type loginRequest struct {
	Login    string `json:"login"`
	Password string `json:"password"`
}

type loginResponse struct {
	Token     string       `json:"token"`
	TokenType string       `json:"token_type"`
	ExpiresIn int64        `json:"expires_in"`
	User      userResponse `json:"user"`
}

// Login обрабатывает POST /api/v1/auth/login (operationId login).
func (h *Handlers) Login(w http.ResponseWriter, r *http.Request) {
	var req loginRequest
	if err := httpx.DecodeJSON(r, &req); err != nil {
		httpx.WriteValidationError(w)
		return
	}
	login := strings.TrimSpace(req.Login)
	if login == "" || req.Password == "" {
		httpx.WriteValidationError(w)
		return
	}

	user, err := h.Store.GetByLogin(r.Context(), login)
	switch {
	case errors.Is(err, ErrNotFound):
		// Сверка с фиктивным хэшем выравнивает время ответа.
		_ = bcrypt.CompareHashAndPassword(dummyHash, []byte(req.Password))
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "Неверный логин или пароль")
		return
	case err != nil:
		httpx.WriteInternalError(w)
		return
	}

	// Неактивный пользователь и неверный пароль отвечают одинаково.
	if !user.IsActive || !ComparePassword(user.PasswordHash, req.Password) {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "Неверный логин или пароль")
		return
	}

	token, _, err := h.Tokens.Issue(user.ID, user.Role)
	if err != nil {
		httpx.WriteInternalError(w)
		return
	}

	httpx.WriteJSON(w, http.StatusOK, loginResponse{
		Token:     token,
		TokenType: "Bearer",
		ExpiresIn: int64(h.Tokens.TTL().Seconds()),
		User:      toUserResponse(user),
	})
}

type userListResponse struct {
	Items    []userResponse `json:"items"`
	Page     int            `json:"page"`
	PageSize int            `json:"page_size"`
	Total    int            `json:"total"`
}

// ListUsers обрабатывает GET /api/v1/users (operationId listUsers). Только ADMIN.
func (h *Handlers) ListUsers(w http.ResponseWriter, r *http.Request) {
	page, err := queryInt(r, "page", defaultPage)
	if err != nil || page < 1 {
		httpx.WriteValidationError(w)
		return
	}
	pageSize, err := queryInt(r, "page_size", defaultPageSize)
	if err != nil || pageSize < 1 || pageSize > maxPageSize {
		httpx.WriteValidationError(w)
		return
	}

	users, total, err := h.Store.List(r.Context(), pageSize, (page-1)*pageSize)
	if err != nil {
		httpx.WriteInternalError(w)
		return
	}

	items := make([]userResponse, 0, len(users))
	for _, user := range users {
		items = append(items, toUserResponse(user))
	}
	httpx.WriteJSON(w, http.StatusOK, userListResponse{
		Items:    items,
		Page:     page,
		PageSize: pageSize,
		Total:    total,
	})
}

type createUserRequest struct {
	Login    string    `json:"login"`
	Password string    `json:"password"`
	Role     auth.Role `json:"role"`
}

// CreateUser обрабатывает POST /api/v1/users (operationId createUser). Только ADMIN.
func (h *Handlers) CreateUser(w http.ResponseWriter, r *http.Request) {
	var req createUserRequest
	if err := httpx.DecodeJSON(r, &req); err != nil {
		httpx.WriteValidationError(w)
		return
	}
	req.Login = strings.TrimSpace(req.Login)
	switch {
	case req.Login == "" || len(req.Login) > MaxLoginLength:
		httpx.WriteValidationError(w)
		return
	case len(req.Password) < MinPasswordLength || len(req.Password) > MaxPasswordLength:
		httpx.WriteValidationError(w)
		return
	case !req.Role.Valid():
		httpx.WriteValidationError(w)
		return
	}

	passwordHash, err := HashPassword(req.Password)
	if err != nil {
		httpx.WriteInternalError(w)
		return
	}
	user, err := h.Store.Create(r.Context(), req.Login, passwordHash, req.Role)
	switch {
	case errors.Is(err, ErrLoginTaken):
		httpx.WriteError(w, http.StatusConflict, httpx.CodeLoginAlreadyExists, "Логин уже занят")
		return
	case err != nil:
		httpx.WriteInternalError(w)
		return
	}
	httpx.WriteJSON(w, http.StatusCreated, toUserResponse(user))
}

// internalUserResponse — данные пользователя для внутренних сервисов
// (internal/gateway-identity.yaml: InternalUser).
type internalUserResponse struct {
	ID          uuid.UUID `json:"id"`
	Login       string    `json:"login"`
	DisplayName string    `json:"display_name"`
	Role        auth.Role `json:"role"`
	IsActive    bool      `json:"is_active"`
}

func toInternalUserResponse(user User) internalUserResponse {
	return internalUserResponse{
		ID:          user.ID,
		Login:       user.Login,
		DisplayName: user.Login,
		Role:        user.Role,
		IsActive:    user.IsActive,
	}
}

// GetInternalUser обрабатывает GET /internal/v1/users/{user_id} (operationId getUser).
func (h *Handlers) GetInternalUser(w http.ResponseWriter, r *http.Request) {
	id, err := uuid.Parse(chi.URLParam(r, "user_id"))
	if err != nil {
		httpx.WriteValidationError(w)
		return
	}
	user, err := h.Store.GetByID(r.Context(), id)
	switch {
	case errors.Is(err, ErrNotFound):
		httpx.WriteError(w, http.StatusNotFound, httpx.CodeNotFound, "Пользователь не найден")
		return
	case err != nil:
		httpx.WriteInternalError(w)
		return
	}
	httpx.WriteJSON(w, http.StatusOK, toInternalUserResponse(user))
}

// maxInternalUserIDs — лимит пакетного поиска (internal/gateway-identity.yaml: ids).
const maxInternalUserIDs = 100

type internalUserListResponse struct {
	Items []internalUserResponse `json:"items"`
}

// ListInternalUsers обрабатывает GET /internal/v1/users?ids=... (operationId listUsersByIds).
func (h *Handlers) ListInternalUsers(w http.ResponseWriter, r *http.Request) {
	rawIDs := strings.Split(r.URL.Query().Get("ids"), ",")
	if len(rawIDs) < 1 || len(rawIDs) > maxInternalUserIDs {
		httpx.WriteValidationError(w)
		return
	}
	ids := make([]uuid.UUID, 0, len(rawIDs))
	for _, rawID := range rawIDs {
		id, err := uuid.Parse(strings.TrimSpace(rawID))
		if err != nil {
			httpx.WriteValidationError(w)
			return
		}
		ids = append(ids, id)
	}

	users, err := h.Store.GetByIDs(r.Context(), ids)
	if err != nil {
		httpx.WriteInternalError(w)
		return
	}
	items := make([]internalUserResponse, 0, len(users))
	for _, user := range users {
		items = append(items, toInternalUserResponse(user))
	}
	httpx.WriteJSON(w, http.StatusOK, internalUserListResponse{Items: items})
}

// queryInt читает целочисленный query-параметр; отсутствующий параметр принимает default.
func queryInt(r *http.Request, name string, defaultValue int) (int, error) {
	raw := strings.TrimSpace(r.URL.Query().Get(name))
	if raw == "" {
		return defaultValue, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, err
	}
	return value, nil
}
