package httpx

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestWriteError(t *testing.T) {
	rec := httptest.NewRecorder()

	WriteError(rec, http.StatusUnauthorized, CodeInvalidCredentials, "Неверный логин или пароль")

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusUnauthorized)
	}
	if contentType := rec.Header().Get("Content-Type"); contentType != "application/json; charset=utf-8" {
		t.Fatalf("content type = %q", contentType)
	}
	var body Error
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body.Code != CodeInvalidCredentials {
		t.Errorf("code = %q, want %q", body.Code, CodeInvalidCredentials)
	}
	if body.Message == "" {
		t.Error("message is empty")
	}
}

func TestWriteJSON(t *testing.T) {
	rec := httptest.NewRecorder()

	WriteJSON(rec, http.StatusCreated, map[string]string{"login": "ivanov"})

	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusCreated)
	}
	if !strings.Contains(rec.Body.String(), `"login":"ivanov"`) {
		t.Fatalf("body = %q", rec.Body.String())
	}
}

func TestWriteValidationErrorAndInternalError(t *testing.T) {
	rec := httptest.NewRecorder()
	WriteValidationError(rec)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("validation status = %d, want 400", rec.Code)
	}

	rec = httptest.NewRecorder()
	WriteInternalError(rec)
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("internal status = %d, want 500", rec.Code)
	}
	if strings.Contains(rec.Body.String(), "error") {
		// В ответе не должно быть технических деталей, только фиксированное сообщение.
		t.Fatalf("internal body leaks details: %q", rec.Body.String())
	}
}

func TestDecodeJSON(t *testing.T) {
	type payload struct {
		Login string `json:"login"`
	}

	t.Run("ok", func(t *testing.T) {
		r := httptest.NewRequest(http.MethodPost, "/", strings.NewReader(`{"login":"ivanov"}`))
		var got payload
		if err := DecodeJSON(r, &got); err != nil {
			t.Fatalf("DecodeJSON: %v", err)
		}
		if got.Login != "ivanov" {
			t.Errorf("Login = %q", got.Login)
		}
	})

	t.Run("malformed json", func(t *testing.T) {
		r := httptest.NewRequest(http.MethodPost, "/", strings.NewReader(`{not-json`))
		var got payload
		if err := DecodeJSON(r, &got); err == nil {
			t.Fatal("DecodeJSON(malformed) = nil, want error")
		}
	})

	t.Run("unknown field", func(t *testing.T) {
		r := httptest.NewRequest(http.MethodPost, "/", strings.NewReader(`{"login":"a","passwoord":"b"}`))
		var got payload
		if err := DecodeJSON(r, &got); err == nil {
			t.Fatal("DecodeJSON(unknown field) = nil, want error")
		}
	})

	t.Run("body too large", func(t *testing.T) {
		big := bytes.Repeat([]byte("a"), MaxRequestBodySize+1)
		r := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(big))
		var got payload
		if err := DecodeJSON(r, &got); err == nil {
			t.Fatal("DecodeJSON(oversized) = nil, want error")
		}
	})
}
