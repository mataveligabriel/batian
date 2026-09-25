// Área de transferência com plano B: a API moderna só existe em HTTPS (ou no app desktop);
// acessando o Bastion por http://IP no navegador usamos execCommand, que funciona após um clique/seleção.
export async function copyText(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* cai no plano B */ }
  const active = document.activeElement;
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  try { active?.focus?.(); } catch { /* noop */ }
  return ok;
}

/** Lê a área de transferência. Retorna null quando o navegador não permite (aí a caixa de colar pede Ctrl+V). */
export async function readText() {
  try {
    if (navigator.clipboard?.readText && window.isSecureContext) return await navigator.clipboard.readText();
  } catch { /* permissão negada */ }
  return null;
}
