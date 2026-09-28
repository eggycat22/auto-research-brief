(() => {
  const form = document.getElementById("chat-form");
  if (!form) return;

  const talk = document.querySelector(".talk");
  const box = form.querySelector("textarea");
  const send = form.querySelector("button");
  const thread = document.getElementById("thread");
  const itemId = form.dataset.item;
  let busy = false;

  const fitTalk = () => {
    if (!talk) return;
    if (window.matchMedia("(max-width: 640px)").matches) {
      talk.style.height = "";
      return;
    }
    const top = talk.getBoundingClientRect().top;
    talk.style.height = `${Math.max(280, window.innerHeight - top - 16)}px`;
  };
  fitTalk();
  window.addEventListener("resize", fitTalk);
  window.addEventListener("scroll", fitTalk, { passive: true });

  const autosize = () => {
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, 128)}px`;
  };

  const scrollThread = () => {
    thread.scrollTop = thread.scrollHeight;
  };

  const escapeHtml = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const mdLite = (text) => {
    const raw = (text || "").trim();
    if (!raw) return "";
    const parts = [];
    const chunks = raw.split(/```/);
    chunks.forEach((chunk, i) => {
      if (i % 2 === 1) {
        const nl = chunk.indexOf("\n");
        const code = nl === -1 ? chunk : chunk.slice(nl + 1);
        parts.push(`<pre>${escapeHtml(code.replace(/\n$/, ""))}</pre>`);
        return;
      }
      let html = escapeHtml(chunk);
      html = html.replace(/\*\*\s*([\s\S]+?)\s*\*\*/g, "<strong>$1</strong>");
      html = html.replace(/(^|[^\*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
      html = html.replace(/\*/g, "");
      html.split(/\n{2,}/).forEach((block) => {
        const lines = block.split("\n").filter((line) => line.length);
        if (!lines.length) return;
        if (lines.every((line) => /^[-–—]\s/.test(line))) {
          parts.push(`<ul>${lines.map((line) => `<li>${line.replace(/^[-–—]\s/, "")}</li>`).join("")}</ul>`);
          return;
        }
        parts.push(`<p>${lines.join("<br>")}</p>`);
      });
    });
    return parts.join("");
  };

  const showReply = (node, text, html, follow) => {
    node.classList.remove("thinking");
    const body = node.querySelector(".msg-body");
    body.classList.add("prose-lite");
    body.innerHTML = html || mdLite(text);
    if (follow) scrollThread();
    else thread.scrollTop = Math.max(0, node.offsetTop - 10);
  };

  const addMsg = (role, { text = "", thinking = false } = {}) => {
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}${thinking ? " thinking" : ""}`;
    const who = document.createElement("span");
    who.className = "msg-who";
    who.textContent = role === "user" ? "你" : "研读";
    const body = document.createElement("div");
    body.className = "msg-body";
    if (thinking) {
      body.innerHTML =
        '<span class="think-dots" aria-hidden="true"><i></i><i></i><i></i></span>' +
        '<span class="think-copy">正在思考</span>';
    } else {
      body.textContent = text;
    }
    wrap.append(who, body);
    thread.appendChild(wrap);
    scrollThread();
    return wrap;
  };

  const setBusy = (on) => {
    busy = on;
    form.classList.toggle("is-busy", on);
    box.disabled = on;
    send.disabled = on;
    thread.setAttribute("aria-busy", on ? "true" : "false");
  };

  const ask = async (text) => {
    const q = (text || "").trim();
    if (!q || busy) return;
    document.getElementById("talk-empty")?.remove();
    addMsg("user", { text: q });
    box.value = "";
    autosize();
    const pending = addMsg("assistant", { thinking: true });
    setBusy(true);
    let fullText = "";
    try {
      const res = await fetch(`/api/chat/${itemId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: q }),
      });
      if (!res.ok) {
        showReply(pending, "这次没答上来，再问一次。");
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let acc = "";
      let started = false;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        acc += decoder.decode(value, { stream: true });
        const parts = acc.split("\n\n");
        acc = parts.pop() || "";
        for (const part of parts) {
          const line = part.replace(/^data: /, "");
          try {
            const data = JSON.parse(line);
            if (data.delta || data.html) {
              started = true;
              if (data.delta) fullText += data.delta;
              showReply(pending, fullText, data.html, true);
            }
            if (data.error) {
              started = true;
              showReply(
                pending,
                fullText ? `${fullText}\n[${data.error}]` : "这次没答上来，再问一次。"
              );
            }
          } catch (e) {
            /* ignore incomplete json */
          }
        }
      }
      if (!started) showReply(pending, "这次没答上来，再问一次。");
    } catch (e) {
      showReply(pending, "这次没答上来，再问一次。");
    } finally {
      setBusy(false);
      box.focus();
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(box.value);
  });

  box.addEventListener("input", autosize);
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask(box.value);
    }
  });

  thread.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-ask]");
    if (!btn) return;
    ask(btn.dataset.ask);
  });

  autosize();
  scrollThread();
})();
