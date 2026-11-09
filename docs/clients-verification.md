# Проверка Clients Service

- [CI для PR](../.github/workflows/clients-service-pr-checks.yml) запускает Ruff и pytest с PostgreSQL 17. Порог покрытия — 90%. Результат смотрите в Checks у проверяемого коммита.
- [Локальные проверки](../services/clients/README.md#проверки) и [проверка HTTP](../services/clients/README.md#проверка-отдельного-http-процесса) запускаются из каталога сервиса. HTTP-сценарий проверяет закрытые маршруты и цикл от импорта до восстановления после удаления.
