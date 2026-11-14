// seed-admin создаёт первого администратора Identity DB.
// Учётные данные передаются переменными окружения ADMIN_LOGIN и ADMIN_PASSWORD.
package main

import (
	"context"
	"log/slog"
	"os"

	"github.com/slipynil/kontur-kcr/services/identity/configs"
	"github.com/slipynil/kontur-kcr/services/identity/internal/app"
)

func main() {
	configs.Initialize(".env", ".", "env")
	if err := app.Seed(context.Background(), configs.GetConfig()); err != nil {
		slog.Error("seed-admin failed", "error", err)
		os.Exit(1)
	}
}
