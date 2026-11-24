/* ==========================================================================
   Acesso restrito · Creche Carolina Guilhermina Schnoor Bohianza
   JavaScript puro (sem dependências)
   ========================================================================== */

(() => {
  "use strict";

  const body = document.body;
  const mode = body?.dataset.mode === "setup" ? "setup" : "login";

  const form = document.getElementById("login-form");
  const errorBox = document.getElementById("login-error");
  const submitButton = document.getElementById("login-submit");
  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
  const confirmInput = document.getElementById("password-confirm");
  const displayNameInput = document.getElementById("display-name");
  const toggleButton = document.getElementById("toggle-password");
  const yearElement = document.getElementById("year");

  if (yearElement) {
    yearElement.textContent = String(new Date().getFullYear());
  }

  if (displayNameInput) {
    usernameInput.removeAttribute("autofocus");
    displayNameInput.setAttribute("autofocus", "");
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    if (!errorBox || errorBox.hidden) return;
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  function setBusy(busy) {
    if (!submitButton) return;
    const label = submitButton.querySelector("span");
    submitButton.disabled = busy;
    submitButton.classList.toggle("is-busy", busy);
    if (label) {
      if (busy) {
        if (!label.dataset.idle) label.dataset.idle = label.textContent;
        label.textContent = "Verificando...";
      } else if (label.dataset.idle) {
        label.textContent = label.dataset.idle;
      }
    }
  }

  function validate(payload) {
    if (mode === "setup") {
      if (!payload.display_name || payload.display_name.length < 2) {
        return "Informe o nome do responsável (de 2 a 80 caracteres).";
      }
      if (payload.display_name.length > 80) {
        return "O nome do responsável deve ter no máximo 80 caracteres.";
      }
    }

    if (!payload.username) return "Informe o usuário.";

    if (mode === "setup" && !/^[A-Za-z0-9._-]{3,32}$/.test(payload.username)) {
      return "O usuário deve ter de 3 a 32 caracteres, usando letras, números, ponto, hífen ou sublinhado.";
    }

    if (!payload.password) return "Informe a senha.";
    if (payload.password.length < 8) return "A senha deve ter pelo menos 8 caracteres.";

    if (mode === "setup" && payload.password !== payload.password_confirm) {
      return "As senhas não conferem.";
    }

    return null;
  }

  toggleButton?.addEventListener("click", () => {
    const isHidden = passwordInput.type === "password";
    passwordInput.type = isHidden ? "text" : "password";
    toggleButton.setAttribute("aria-label", isHidden ? "Ocultar senha" : "Mostrar senha");
    toggleButton.setAttribute("aria-pressed", String(isHidden));
    const use = toggleButton.querySelector("use");
    if (use) use.setAttribute("href", isHidden ? "#i-eye-off" : "#i-eye");
    passwordInput.focus();
  });

  [usernameInput, passwordInput, confirmInput, displayNameInput].forEach((input) => {
    input?.addEventListener("input", clearError);
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!submitButton || submitButton.disabled) return;

    clearError();

    const payload = {
      username: (usernameInput?.value || "").trim(),
      password: passwordInput?.value || "",
    };

    if (mode === "setup") {
      payload.display_name = (displayNameInput?.value || "").trim();
      payload.password_confirm = confirmInput?.value || "";
    }

    const problem = validate(payload);
    if (problem) {
      showError(problem);
      return;
    }

    setBusy(true);

    let response;
    try {
      response = await fetch(mode === "setup" ? "/api/setup" : "/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });
    } catch {
      setBusy(false);
      showError("Não foi possível conectar ao servidor. Verifique se o sistema está em execução.");
      return;
    }

    let data = null;
    if ((response.headers.get("content-type") || "").includes("application/json")) {
      data = await response.json().catch(() => null);
    }

    if (!response.ok) {
      setBusy(false);
      showError(
        data?.error ||
          (response.status === 429
            ? "Muitas tentativas de acesso. Aguarde e tente novamente."
            : `Não foi possível entrar (erro ${response.status}). Tente novamente.`)
      );
      passwordInput?.focus();
      passwordInput?.select();
      return;
    }

    window.location.href = "/";
  });
})();
