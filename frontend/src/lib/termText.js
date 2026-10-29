// Saída bruta do terminal (com códigos ANSI, \r, backspace, paginação "---- More ----" apagada) -> texto limpo,
// como aparece na tela. Usado no "Copiar tudo" e no "Salvar saída", sem o limite de linhas da tela.
export function toPlain(raw) {
  const lines = [];
  let cur = [];
  let col = 0;
  const put = (ch) => {
    while (cur.length < col) cur.push(" ");
    cur[col] = ch;
    col++;
  };
  const n = raw.length;
  for (let i = 0; i < n; i++) {
    const ch = raw[i];
    const c = raw.charCodeAt(i);
    if (ch === "\x1b") {
      const nx = raw[i + 1];
      if (nx === "[") {                      // CSI: parâmetros e letra final
        let j = i + 2, params = "";
        while (j < n && /[0-9;?]/.test(raw[j])) params += raw[j++];
        const fin = raw[j];
        const num = parseInt(params.replace("?", ""), 10);
        const k = Number.isNaN(num) ? 1 : num;
        if (fin === "D") col = Math.max(0, col - k);
        else if (fin === "C") col += k;
        else if (fin === "G") col = Math.max(0, k - 1);
        else if (fin === "K") {
          const mode = Number.isNaN(num) ? 0 : num;
          if (mode === 0) cur = cur.slice(0, col);
          else if (mode === 1) for (let x = 0; x < Math.min(col, cur.length); x++) cur[x] = " ";
          else cur = [];
        }
        i = j;
        continue;
      }
      if (nx === "]") {                      // OSC: até BEL ou ESC \
        let j = i + 2;
        while (j < n && raw[j] !== "\x07" && !(raw[j] === "\x1b" && raw[j + 1] === "\\")) j++;
        i = raw[j] === "\x07" ? j : j + 1;
        continue;
      }
      i += 1;                                // outros ESC x
      continue;
    }
    if (ch === "\n") { lines.push(cur.join("").replace(/\s+$/, "")); cur = []; col = 0; continue; }
    if (ch === "\r") { col = 0; continue; }
    if (ch === "\b") { col = Math.max(0, col - 1); continue; }
    if (ch === "\t") { const to = (Math.floor(col / 8) + 1) * 8; while (col < to) put(" "); continue; }
    if (c < 32 || c === 127) continue;
    put(ch);
  }
  if (cur.length) lines.push(cur.join("").replace(/\s+$/, ""));
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  return lines.join("\n");
}

export function downloadText(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
