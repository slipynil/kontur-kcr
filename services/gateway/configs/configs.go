package configs

import (
	"errors"

	"github.com/spf13/viper"
)

type Config struct {
	Service serviceConfig
}

type serviceConfig struct {
	GatewayPort           int
	GatewayDebug          bool
	ClientsEndpoint       string
	ImportAPIEndpoint     string
	OperatorWorkEndpoint  string
	AssistantEndpoint     string
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
		GatewayDebug:          viper.GetBool("GATEWAY_DEBUG"),
		GatewayPort:           viper.GetInt("GATEWAY_PORT"),
		ClientsEndpoint:       viper.GetString("CLIENTS_ENDPOINT"),
		ImportAPIEndpoint:     viper.GetString("IMPORT_API_ENDPOINT"),
		OperatorWorkEndpoint:  viper.GetString("OPERATOR_WORK_ENDPOINT"),
		AssistantEndpoint:     viper.GetString("ASSISTANT_ENDPOINT"),
	}

	return &Config{
		Service: cfg,
	}
}
