/**
 * Safe DOM rendering helpers, markdown formatter, and accessible <dialog> helpers.
 */

const INLINE_TOKEN_RE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;

function appendInlineFormatted(parent, text) {
  let lastIdx = 0;
  for (const match of text.matchAll(INLINE_TOKEN_RE)) {
    const idx = match.index ?? 0;
    if (idx > lastIdx) {
      parent.appendChild(document.createTextNode(text.slice(lastIdx, idx)));
    }
    const token = match[0];
    if (token.startsWith("**") && token.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.appendChild(strong);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.appendChild(code);
    } else if (token.startsWith("[")) {
      const splitIdx = token.indexOf("](");
      const label = token.slice(1, splitIdx);
      const rawUrl = token.slice(splitIdx + 2, -1);
      try {
        const parsed = new URL(rawUrl);
        if (parsed.protocol === "http:" || parsed.protocol === "https:") {
          const a = document.createElement("a");
          a.href = parsed.href;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = label;
          parent.appendChild(a);
        } else {
          parent.appendChild(document.createTextNode(token));
        }
      } catch {
        parent.appendChild(document.createTextNode(token));
      }
    }
    lastIdx = idx + token.length;
  }
  if (lastIdx < text.length) {
    parent.appendChild(document.createTextNode(text.slice(lastIdx)));
  }
}

export function renderSafeMarkdown(text) {
  const container = document.createElement("div");
  container.className = "message-body";

  const lines = String(text || "").split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (line.trim().startsWith("```")) {
      const codeLines = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i += 1;
      }
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = codeLines.join("\n");
      pre.appendChild(code);
      container.appendChild(pre);
      i += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const ul = document.createElement("ul");
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        const li = document.createElement("li");
        appendInlineFormatted(li, lines[i].replace(/^\s*[-*]\s+/, ""));
        ul.appendChild(li);
        i += 1;
      }
      container.appendChild(ul);
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const p = document.createElement("p");
    appendInlineFormatted(p, line);
    container.appendChild(p);
    i += 1;
  }

  return container;
}

export function setupDialogs() {
  const supportsClosedBy = "closedBy" in HTMLDialogElement.prototype;
  const outside = (e, dialog) => {
    const r = dialog.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  };

  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("close", () => {
      dialog.returnValue = "";
    });

    if (!supportsClosedBy && dialog.getAttribute("closedby") === "any") {
      let pressedOutside = false;
      dialog.addEventListener("pointerdown", (e) => {
        pressedOutside = outside(e, dialog);
      });
      dialog.addEventListener("pointerup", (e) => {
        if (pressedOutside && outside(e, dialog)) {
          dialog.close();
        }
        pressedOutside = false;
      });
    }
  }
}

export function showToast(message) {
  const region = document.getElementById("toast-region");
  if (!region) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  region.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 5500);
}

export function formatShortTime(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return isoStr;
    return d.toLocaleString("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoStr;
  }
}
