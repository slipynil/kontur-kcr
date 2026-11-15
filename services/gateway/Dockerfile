FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY configs ./configs
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/seed-admin ./cmd/seed

FROM alpine:3.21
RUN adduser -D -H -u 10001 gateway
COPY --from=build /out/gateway /usr/local/bin/gateway
COPY --from=build /out/seed-admin /usr/local/bin/seed-admin
USER gateway
# Listen port, overridable at runtime; EXPOSE documents the default (docs/local-development.md).
ENV GATEWAY_PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec /usr/local/bin/gateway"]
