package configs

import (
	"errors"
	"strings"
	"time"

	"github.com/spf13/viper"
)

type Config struct {
	Service serviceConfig
}

type serviceConfig struct {
	GatewayPort          int
	GatewayDebug         bool
	ClientsEndpoint      string
	ImportAPIEndpoint    string
	OperatorWorkEndpoint string
	OperatorWorkToken    string
	AssistantEndpoint    string
	IdentityDSN          string
	JWTSecret            string
	JWTTTL               time.Duration
	CORSAllowedOrigins   []string
	UpstreamTimeout      time.Duration
}

// Инициализирует переменные из .env файла.
// Переменные окружения имеют приоритет над файлом (AutomaticEnv);
// при отсутствии файла используются значения по умолчанию.
// Ключи — SCREAMING_SNAKE, как в .env; поля структуры — CamelCase.
func Initialize(filename, filepath, filetype string) {
	viper.SetConfigName(filename)
	viper.SetConfigType(filetype)
	viper.AddConfigPath(filepath)

	// Порты локального запуска сервисов: см. docs/local-development.md.
	viper.SetDefault("GATEWAY_PORT", 8080)
	viper.SetDefault("GATEWAY_DEBUG", false)
	viper.SetDefault("CLIENTS_ENDPOINT", "http://127.0.0.1:8001")
	viper.SetDefault("IMPORT_API_ENDPOINT", "http://127.0.0.1:8002")
	viper.SetDefault("OPERATOR_WORK_ENDPOINT", "http://127.0.0.1:8003")
	viper.SetDefault("ASSISTANT_ENDPOINT", "http://127.0.0.1:8004")

	// Identity DB и авторизация.
	viper.SetDefault("IDENTITY_DSN", "postgres://identity:identity@127.0.0.1:5432/identity?sslmode=disable")
	viper.SetDefault("JWT_SECRET", "")
	viper.SetDefault("JWT_TTL", "8h")
	viper.SetDefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
	viper.SetDefault("UPSTREAM_TIMEOUT", "30s")

	viper.AutomaticEnv()

	err := viper.ReadInConfig()
	if err != nil {
		var notFound viper.ConfigFileNotFoundError
		if !errors.As(err, &notFound) {
			panic(err)
		}
	}
}

func GetConfig() *Config {
	cfg := serviceConfig{
		GatewayDebug:         viper.GetBool("GATEWAY_DEBUG"),
		GatewayPort:          viper.GetInt("GATEWAY_PORT"),
		ClientsEndpoint:      viper.GetString("CLIENTS_ENDPOINT"),
		ImportAPIEndpoint:    viper.GetString("IMPORT_API_ENDPOINT"),
		OperatorWorkEndpoint: viper.GetString("OPERATOR_WORK_ENDPOINT"),
		OperatorWorkToken:    viper.GetString("OPERATOR_WORK_SERVICE_TOKEN"),
		AssistantEndpoint:    viper.GetString("ASSISTANT_ENDPOINT"),
		IdentityDSN:          viper.GetString("IDENTITY_DSN"),
		JWTSecret:            viper.GetString("JWT_SECRET"),
		JWTTTL:               viper.GetDuration("JWT_TTL"),
		CORSAllowedOrigins:   splitList(viper.GetString("CORS_ALLOWED_ORIGINS")),
		UpstreamTimeout:      viper.GetDuration("UPSTREAM_TIMEOUT"),
	}

	return &Config{
		Service: cfg,
	}
}

// splitList разбирает значения вида "a,b,c" из env-переменных:
// viper.GetStringSlice не делит строку окружения по запятой.
func splitList(raw string) []string {
	parts := strings.Split(raw, ",")
	values := make([]string, 0, len(parts))
	for _, part := range parts {
		if value := strings.TrimSpace(part); value != "" {
			values = append(values, value)
		}
	}
	return values
}
