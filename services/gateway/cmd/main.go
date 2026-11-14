// Gateway / Identity Service — единая точка входа «Контур КЦР» (spec.md §1.1, §3.1).
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
	if err := app.Run(context.Background(), configs.GetConfig()); err != nil {
		slog.Error("gateway exited with error", "error", err)
		os.Exit(1)
	}
}
