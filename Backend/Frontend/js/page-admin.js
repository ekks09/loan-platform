/** Staff identity-verification queue. */
(function () {
  "use strict";

  function getCookie(name) {
    const match = document.cookie.match("(^|;\\s*)" + name + "=([^;]*)");
    return match ? decodeURIComponent(match[2]) : "";
  }

  function errorMessage(data, fallback) {
    if (!data) return fallback;
    if (typeof data.error === "string") return data.error;
    if (data.error && typeof data.error.message === "string") return data.error.message;
    if (typeof data.message === "string") return data.message;
    return fallback;
  }

  document.querySelectorAll(".review-card").forEach(function (card) {
    const verifyUrl = card.getAttribute("data-verify-url");
    const notes = card.querySelector("textarea");
    const message = card.querySelector(".review-msg");
    if (!verifyUrl || !message) return;

    card.querySelectorAll("button[data-action]").forEach(function (button) {
      button.addEventListener("click", async function () {
        const action = button.getAttribute("data-action");

        if (action === "reject" && !notes.value.trim()) {
          message.textContent = "Add a short reason before rejecting.";
          message.className = "msg review-msg msg-error";
          notes.focus();
          return;
        }
        if (
          action === "approve" &&
          !window.confirm("Approve this identity? The user will be able to apply for a loan.")
        ) {
          return;
        }
        if (
          action === "reject" &&
          !window.confirm("Reject this identity? The user will be asked to upload new photos.")
        ) {
          return;
        }
        if (navigator.onLine === false) {
          message.textContent = "You are offline. Reconnect before recording a decision.";
          message.className = "msg review-msg msg-error";
          return;
        }

        card.querySelectorAll("button[data-action]").forEach(function (item) {
          item.disabled = true;
        });
        message.textContent = "Saving…";
        message.className = "msg review-msg";

        try {
          const response = await fetch(verifyUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
              "X-Requested-With": "XMLHttpRequest",
            },
            body: JSON.stringify({ action: action, notes: notes.value.trim() }),
          });
          const data = await response.json().catch(function () {
            return {};
          });
          if (!response.ok) {
            throw new Error(errorMessage(data, "Could not save the decision."));
          }
          message.textContent = data.message || "Saved.";
          message.className = "msg review-msg msg-success";
          card.classList.add("reviewed");
          window.setTimeout(function () {
            window.location.reload();
          }, 900);
        } catch (error) {
          message.textContent = error.message || "Could not save the decision.";
          message.className = "msg review-msg msg-error";
          card.querySelectorAll("button[data-action]").forEach(function (item) {
            item.disabled = false;
          });
        }
      });
    });
  });
})();
