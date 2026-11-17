// Package proxy перенаправляет публичные маршруты /api/v1 во внутренние сервисы
// как /internal/v1, проставляя проверенные заголовки пользователя (spec.md §1.1, §3.1;
// docs/api/internal/*.yaml).
package proxy

import (
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"github.com/slipynil/kontur-kcr/services/identity/internal/auth"
	"github.com/slipynil/kontur-kcr/services/identity/internal/httpx"
)

// Имена внутренних сервисов.
const (
	ServiceClients      = "clients"
	ServiceImport       = "import"
	ServiceOperatorWork = "operator-work"
	ServiceAssistant    = "assistant"
)

// Заголовки идентичности для внутренних сервисов; Gateway проставляет их сам
// и затирает присланные клиентом значения (docs/api/internal/*.yaml: X-User-Id, X-User-Role).
const (
	HeaderUserID   = "X-User-Id"
	HeaderUserRole = "X-User-Role"
)

const (
	publicPrefix   = "/api/v1"
	internalPrefix = "/internal/v1"
)

// Таймауты транспорта к внутренним сервисам.
const (
	dialTimeout           = 5 * time.Second
	keepAliveTimeout      = 30 * time.Second
	idleConnTimeout       = 90 * time.Second
	tlsHandshakeTimeout   = 5 * time.Second
	expectContinueTimeout = 1 * time.Second
	maxIdleConns          = 100
	maxIdleConnsPerHost   = 16
)

// Upstreams держит по одному reverse proxy на внутренний сервис.
type Upstreams struct {
	handlers map[string]http.Handler
}

// New создаёт прокси для всех внутренних сервисов; некорректный URL — ошибка запуска.
func New(
	log *slog.Logger,
	timeout time.Duration,
	clientsURL, importURL, operatorWorkURL, assistantURL string,
) (*Upstreams, error) {
	services := map[string]string{
		ServiceClients:      clientsURL,
		ServiceImport:       importURL,
		ServiceOperatorWork: operatorWorkURL,
		ServiceAssistant:    assistantURL,
	}
	transport := &http.Transport{
		Proxy: nil,
		DialContext: (&net.Dialer{
			Timeout:   dialTimeout,
			KeepAlive: keepAliveTimeout,
		}).DialContext,
		MaxIdleConns:          maxIdleConns,
		MaxIdleConnsPerHost:   maxIdleConnsPerHost,
		IdleConnTimeout:       idleConnTimeout,
		TLSHandshakeTimeout:   tlsHandshakeTimeout,
		ExpectContinueTimeout: expectContinueTimeout,
		ResponseHeaderTimeout: timeout,
	}

	handlers := make(map[string]http.Handler, len(services))
	for name, rawURL := range services {
		target, err := url.Parse(rawURL)
		if err != nil {
			return nil, fmt.Errorf("parse %s upstream url %q: %w", name, rawURL, err)
		}
		if target.Scheme == "" || target.Host == "" {
			return nil, fmt.Errorf("invalid %s upstream url %q: scheme and host are required", name, rawURL)
		}
		handlers[name] = newReverseProxy(log, name, target, transport)
	}
	return &Upstreams{handlers: handlers}, nil
}

// Handler возвращает прокси-обработчик сервиса; неизвестное имя — ошибка сборки роутера.
func (u *Upstreams) Handler(service string) http.Handler {
	handler, ok := u.handlers[service]
	if !ok {
		panic(fmt.Sprintf("proxy: unknown upstream service %q", service))
	}
	return handler
}

// HandlerFunc возвращает прокси-обработчик как http.HandlerFunc для chi-роутера.
func (u *Upstreams) HandlerFunc(service string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		u.Handler(service).ServeHTTP(w, r)
	}
}

func newReverseProxy(log *slog.Logger, name string, target *url.URL, transport http.RoundTripper) http.Handler {
	rp := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetXForwarded()
			pr.Out.Host = target.Host
			pr.Out.URL.Scheme = target.Scheme
			pr.Out.URL.Host = target.Host
			pr.Out.URL.Path = rewritePath(pr.In.URL.Path)
			setIdentityHeaders(pr.Out, pr.In)
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			// Технические детали наружу не отдаются, только в лог gateway.
			log.Error("upstream request failed",
				"upstream", name,
				"method", r.Method,
				"path", r.URL.Path,
				"error", err,
			)
			httpx.WriteError(w, http.StatusServiceUnavailable, httpx.CodeServiceUnavailable,
				"Сервис временно недоступен, повторите позже")
		},
		Transport: transport,
	}
	return rp
}

// rewritePath переводит публичный путь /api/v1/... во внутренний /internal/v1/...
func rewritePath(path string) string {
	if path == publicPrefix {
		return internalPrefix
	}
	if rest, ok := strings.CutPrefix(path, publicPrefix+"/"); ok {
		return internalPrefix + "/" + rest
	}
	return path
}

// setIdentityHeaders переносит проверенную идентичность в заголовки внутреннего запроса.
// Set перекрывает значения, присланные клиентом, — подделать X-User-Id снаружи нельзя.
func setIdentityHeaders(out, in *http.Request) {
	identity, ok := auth.FromContext(in.Context())
	if !ok {
		out.Header.Del(HeaderUserID)
		out.Header.Del(HeaderUserRole)
		return
	}
	out.Header.Set(HeaderUserID, identity.UserID.String())
	out.Header.Set(HeaderUserRole, string(identity.Role))
}
