package configs

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestConfigDefaultsAndOverrides(t *testing.T) {
	dir := t.TempDir()

	// 1. No config file: defaults apply.
	Initialize(".env", dir, "env")
	cfg := GetConfig()
	if cfg.Service.GatewayPort != 8080 {
		t.Fatalf("default GATEWAY_PORT = %d, want 8080", cfg.Service.GatewayPort)
	}
	if cfg.Service.ClientsEndpoint != "http://127.0.0.1:8001" {
		t.Fatalf("default CLIENTS_ENDPOINT = %q", cfg.Service.ClientsEndpoint)
	}

	// 2. Config file value wins over default.
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("GATEWAY_PORT=9999\nCLIENTS_ENDPOINT=http://file:1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	Initialize(".env", dir, "env")
	cfg = GetConfig()
	if cfg.Service.GatewayPort != 9999 || cfg.Service.ClientsEndpoint != "http://file:1" {
		t.Fatalf("file values not applied: %+v", cfg.Service)
	}

	// 3. Real env overrides file.
	t.Setenv("GATEWAY_PORT", "7777")
	Initialize(".env", dir, "env")
	cfg = GetConfig()
	if cfg.Service.GatewayPort != 7777 {
		t.Fatalf("env override not applied: %d", cfg.Service.GatewayPort)
	}
}

func TestConfigDefaultsForNewFields(t *testing.T) {
	Initialize(".env", t.TempDir(), "env")
	cfg := GetConfig()

	if cfg.Service.IdentityDSN == "" {
		t.Fatal("default IDENTITY_DSN is empty")
	}
	if cfg.Service.JWTSecret != "" {
		// Секрет без значения по умолчанию: отсутствие конфигурации должно быть явным.
		t.Fatalf("default JWT_SECRET = %q, want empty", cfg.Service.JWTSecret)
	}
	if cfg.Service.JWTTTL != 8*time.Hour {
		t.Fatalf("default JWT_TTL = %v, want 8h", cfg.Service.JWTTTL)
	}
	if cfg.Service.UpstreamTimeout != 30*time.Second {
		t.Fatalf("default UPSTREAM_TIMEOUT = %v, want 30s", cfg.Service.UpstreamTimeout)
	}
	want := []string{"http://localhost:3000", "http://127.0.0.1:3000"}
	if len(cfg.Service.CORSAllowedOrigins) != len(want) {
		t.Fatalf("default CORS_ALLOWED_ORIGINS = %v, want %v", cfg.Service.CORSAllowedOrigins, want)
	}
	for i, origin := range want {
		if cfg.Service.CORSAllowedOrigins[i] != origin {
			t.Fatalf("default CORS_ALLOWED_ORIGINS = %v, want %v", cfg.Service.CORSAllowedOrigins, want)
		}
	}
}

func TestConfigEnvOverridesForNewFields(t *testing.T) {
	Initialize(".env", t.TempDir(), "env")

	t.Setenv("JWT_SECRET", "0123456789abcdef0123456789abcdef")
	t.Setenv("JWT_TTL", "1h30m")
	t.Setenv("UPSTREAM_TIMEOUT", "5s")
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://kcr.example.local, https://alt.example.local")
	t.Setenv("IDENTITY_DSN", "postgres://dbhost.example:5432/identity")

	cfg := GetConfig()
	if cfg.Service.JWTSecret != "0123456789abcdef0123456789abcdef" {
		t.Fatalf("JWT_SECRET = %q", cfg.Service.JWTSecret)
	}
	if cfg.Service.JWTTTL != 90*time.Minute {
		t.Fatalf("JWT_TTL = %v, want 1h30m", cfg.Service.JWTTTL)
	}
	if cfg.Service.UpstreamTimeout != 5*time.Second {
		t.Fatalf("UPSTREAM_TIMEOUT = %v, want 5s", cfg.Service.UpstreamTimeout)
	}
	want := []string{"https://kcr.example.local", "https://alt.example.local"}
	if len(cfg.Service.CORSAllowedOrigins) != 2 ||
		cfg.Service.CORSAllowedOrigins[0] != want[0] || cfg.Service.CORSAllowedOrigins[1] != want[1] {
		t.Fatalf("CORS_ALLOWED_ORIGINS = %v, want %v", cfg.Service.CORSAllowedOrigins, want)
	}
	if cfg.Service.IdentityDSN != "postgres://dbhost.example:5432/identity" {
		t.Fatalf("IDENTITY_DSN = %q", cfg.Service.IdentityDSN)
	}
}
