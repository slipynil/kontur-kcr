package identity

import (
	"fmt"

	"golang.org/x/crypto/bcrypt"
)

// HashPassword вычисляет bcrypt-хэш пароля.
func HashPassword(password string) (string, error) {
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return "", fmt.Errorf("hash password: %w", err)
	}
	return string(hash), nil
}

// ComparePassword сверяет пароль с хэшем.
func ComparePassword(hash, password string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(password)) == nil
}

// dummyHash используется для выравнивания времени ответа при неизвестном логине,
// чтобы нельзя было отличить существующий логин от несуществующего по таймингу.
var dummyHash = func() []byte {
	hash, err := bcrypt.GenerateFromPassword([]byte("timing-equalization-placeholder"), bcrypt.DefaultCost)
	if err != nil {
		// bcrypt с фиксированным входом не ошибается; паника на старте лучше тихой деградации.
		panic(fmt.Sprintf("generate dummy bcrypt hash: %v", err))
	}
	return hash
}()
