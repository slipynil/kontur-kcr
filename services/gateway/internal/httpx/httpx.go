// Package httpx содержит общие HTTP-хелперы gateway: JSON-ответы,
// единый формат ошибок публичного API (docs/api/public-api.yaml) и декодирование запросов.
package httpx

import (
	"encoding/json"
	"fmt"
	"net/http"
)

// Машинночитаемые коды ошибок публичного API.
const (
	CodeValidationError = "VALIDATION_ERROR"
	// CodeInvalidCredentials — код ошибки, а не секрет; gosec G101 даёт
	// ложное срабатывание на слове credentials.
	CodeInvalidCredentials = "INVALID_CREDENTIALS" //nolint:gosec // error code, not a credential
	CodeUnauthorized       = "UNAUTHORIZED"
	CodeInvalidToken       = "INVALID_TOKEN"
	CodeForbidden          = "FORBIDDEN"
	CodeNotFound           = "NOT_FOUND"
	CodeLoginAlreadyExists = "LOGIN_ALREADY_EXISTS"
	CodeInternalError      = "INTERNAL_ERROR"
	CodeServiceUnavailable = "SERVICE_UNAVAILABLE"
	CodeBadGateway         = "BAD_GATEWAY"
)

// MaxRequestBodySize ограничивает размер JSON-тела запроса к самому gateway.
const MaxRequestBodySize = 1 << 20

// Error — единый формат ошибок для всех эндпоинтов.
type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// WriteJSON отправляет ответ с указанным статусом.
func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	// Ошибка записи означает, что клиент ушёл; обработка не требуется.
	_ = json.NewEncoder(w).Encode(v)
}

// WriteError отправляет ошибку в едином формате публичного API.
func WriteError(w http.ResponseWriter, status int, code, message string) {
	WriteJSON(w, status, Error{Code: code, Message: message})
}

// WriteValidationError отправляет 400 VALIDATION_ERROR.
func WriteValidationError(w http.ResponseWriter) {
	WriteError(w, http.StatusBadRequest, CodeValidationError, "Некорректные параметры запроса")
}

// WriteInternalError отправляет 500 INTERNAL_ERROR без технических деталей наружу.
func WriteInternalError(w http.ResponseWriter) {
	WriteError(w, http.StatusInternalServerError, CodeInternalError, "Внутренняя ошибка сервера")
}

// DecodeJSON читает JSON-тело запроса в dst. Тело ограничено MaxRequestBodySize,
// неизвестные поля отклоняются — опечатки контракта не проходят молча.
func DecodeJSON(r *http.Request, dst any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(nil, r.Body, MaxRequestBodySize))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(dst); err != nil {
		return fmt.Errorf("decode json body: %w", err)
	}
	return nil
}
