package configs

import (
	"os"
	"path/filepath"
	"testing"
)

func TestConfigDefaultsAndOverrides(t *testing.T) {
	dir := t.TempDir()

	// 1. No config file: defaults apply.
	Initialize(".env", dir, "env")
	cfg := GetConfig()
	if cfg.Service.GATEWAY_PORT != 8080 {
		t.Fatalf("default GATEWAY_PORT = %d, want 8080", cfg.Service.GATEWAY_PORT)
	}
	if cfg.Service.CLIENTS_ENDPOINT != "http://127.0.0.1:8001" {
		t.Fatalf("default CLIENTS_ENDPOINT = %q", cfg.Service.CLIENTS_ENDPOINT)
	}

	// 2. Config file value wins over default.
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("GATEWAY_PORT=9999\nCLIENTS_ENDPOINT=http://file:1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	Initialize(".env", dir, "env")
	cfg = GetConfig()
	if cfg.Service.GATEWAY_PORT != 9999 || cfg.Service.CLIENTS_ENDPOINT != "http://file:1" {
		t.Fatalf("file values not applied: %+v", cfg.Service)
	}

	// 3. Real env overrides file.
	t.Setenv("GATEWAY_PORT", "7777")
	Initialize(".env", dir, "env")
	cfg = GetConfig()
	if cfg.Service.GATEWAY_PORT != 7777 {
		t.Fatalf("env override not applied: %d", cfg.Service.GATEWAY_PORT)
	}
}
