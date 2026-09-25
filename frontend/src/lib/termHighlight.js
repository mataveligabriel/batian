// Realce de sintaxe estilo MobaXterm para a saída dos equipamentos (xterm.js).
// Recebe a saída em pedaços (stream), injeta cores ANSI em IPs, MACs, interfaces, estados etc.
// Não mexe em sequências de escape do equipamento, nem em texto que o próprio equipamento
// já coloriu, nem em programas de tela cheia (vim, top…).

const rgb = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return `\x1b[38;2;${(n >> 16) & 255};${(n >> 8) & 255};${n & 255}m`;
};
const RESET_FG = "\x1b[39;22m"; // volta cor padrão e tira negrito, preservando fundo
const BOLD = "\x1b[1m";

export const HL_COLORS = {
  ipPublic:  "#FFB454", // laranja — IP público
  ipPrivate: "#5CCFE6", // ciano — RFC1918 / CGNAT / loopback
  mask:      "#7C8799", // cinza — máscaras 255.x
  ipv6:      "#C3A6FF", // lilás
  mac:       "#F28FAD", // rosa
  iface:     "#73B8FF", // azul
  good:      "#87D96C", // verde — up / established / online
  bad:       "#FF6B6B", // vermelho — down / erro / offline
  warn:      "#FFD173", // amarelo — warning / idle
  dbm:       "#FFD173", // amarelo — níveis ópticos
  prompt:    "#87D96C", // verde negrito — prompt
};

const OCT = "(?:25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)";
const IPV4 = `${OCT}(?:\\.${OCT}){3}`;
const H = "[0-9A-Fa-f]";

const RULES = [
  // ordem = prioridade
  ["mac", `(?<![\\w:.-])(?:${H}{2}(?::${H}{2}){5}|${H}{2}(?:-${H}{2}){5}|${H}{4}\\.${H}{4}\\.${H}{4}|${H}{4}-${H}{4}-${H}{4})(?![\\w:.-])`],
  ["ipv4", `(?<![\\w.])${IPV4}(?:\\/\\d{1,2})?(?![\\w]|\\.\\d)`],
  ["ipv6", `(?<![\\w:])(?:(?:${H}{1,4}:){7}${H}{1,4}|(?:${H}{1,4}:){1,6}:(?:${H}{1,4}(?::${H}{1,4}){0,5})?|::(?:${H}{1,4}(?::${H}{1,4}){0,6})?|(?:${H}{1,4}:){3,6}${H}{1,4})(?:\\/\\d{1,3})?(?![\\w:])`],
  ["dbm", `-?\\d+(?:\\.\\d+)?\\s?dBm\\b`],
  ["iface", "(?<![\\w-])(?:" + [
    "(?:Hundred|Forty|Twenty[Ff]ive|Ten|Fifty|FourHundred)?Gig(?:abit)?E(?:thernet)?\\d+(?:[/:.]\\d+)*",
    "(?:100|400|40|25|50|10)?GE\\d+(?:[/:.]\\d+)+", "XGE\\d+(?:[/:.]\\d+)+", "Eth-Trunk\\d+(?:\\.\\d+)?",
    "Vlanif\\d+", "Vlan-?\\d+", "LoopBack\\d+", "Loopback\\d+", "NULL0", "Tunnel\\d+(?:[/:.]\\d+)*",
    "MEth\\d+(?:[/:.]\\d+)*", "Virtual-(?:Template|Ethernet)\\d+(?:[/:.]\\d+)*",
    "(?:Gi|Te|Fa|Fo|Hu|Tw|Eth?)\\d+(?:[/:.]\\d+)+", "Ethernet\\d+(?:[/:.]\\d+)*", "FastEthernet\\d+(?:[/:.]\\d+)*",
    "Port-channel\\d+(?:\\.\\d+)?", "Bundle-Ether\\d+(?:\\.\\d+)?", "BDI\\d+",
    "(?:ge|xe|et|fe|mge)-\\d+\\/\\d+\\/\\d+(?:[:.]\\d+)*", "ae\\d+(?:\\.\\d+)?", "irb(?:\\.\\d+)?", "lo0(?:\\.\\d+)?", "em\\d+", "fxp0",
    "gpon[-_](?:olt|onu)[-_]\\d+\\/\\d+\\/\\d+(?::\\d+)?", "gpon_(?:olt|onu)-\\d+\\/\\d+\\/\\d+(?::\\d+)?",
    "epon[-_](?:olt|onu)[-_]\\d+\\/\\d+\\/\\d+(?::\\d+)?", "xgei?[-_]\\d+\\/\\d+\\/\\d+", "gei_\\d+\\/\\d+\\/\\d+",
    "ten-gigabit-ethernet-\\d+\\/\\d+\\/\\d+", "gigabit-ethernet-\\d+\\/\\d+\\/\\d+", "forty-gigabit-ethernet-\\d+\\/\\d+\\/\\d+",
    "hundred-gigabit-ethernet-\\d+\\/\\d+\\/\\d+", "(?:twenty-five-g|twentyfive-gigabit)-ethernet-\\d+\\/\\d+\\/\\d+",
    "sfp-sfpplus\\d+", "sfp\\d+", "ether\\d+", "bridge\\d*", "wlan\\d+", "pppoe-[\\w-]+",
  ].join("|") + ")(?![\\w/])"],
  ["good", "(?<!/)\\b(?:up|UP|Up|online|ONLINE|OnLine|Online|established|Established|ESTABLISHED|Estab|connected|Connected|working|Working|enabled|Enabled|normal|Normal|forwarding|FORWARDING|ready|Ready|success|Success|OK)\\b(?!/)"],
  ["bad", "(?<!/)\\*?\\b(?:down|DOWN|Down|offline|OFFLINE|OffLine|Offline|error|errors|Error|ERROR|failed|Failed|FAILED|failure|fail|FAIL|disabled|Disabled|LOS|LOSi|LOFi|DyingGasp|dying-gasp|dyinggasp|unreachable|Unreachable|timeout|Timeout|denied|Denied|invalid|Invalid|Unrecognized|Incomplete|incomplete|blocking|BLOCKING|err-disabled|shutdown)\\b(?!/)"],
  ["warn", "\\b(?:warning|Warning|WARNING|idle|Idle|IDLE|Connect|OpenSent|OpenConfirm|Active(?=\\s+\\d)|testing|dormant|degraded)\\b"],
];

const COMBINED = new RegExp(RULES.map(([name, re]) => `(?<${name}>${re})`).join("|"), "g");
// prompt: <HUAWEI>  [~HUAWEI-GE0/0/1]  ZXAN#  RP/0/RSP0/CPU0:ASR9k#  admin@mx204>  root@host:~$
const PROMPT_BODY = "<[^<>\\r\\n\\x1b]{1,64}>|\\[[~*]?[^[\\]\\r\\n\\x1b]{1,64}\\]|[A-Za-z][\\w.@\\-:~/]{0,63}(?:\\([\\w\\-/.:]{1,40}\\))?[#>$]";
const PROMPT_G = new RegExp(`(^|[\\r\\n])(${PROMPT_BODY})`, "g");
const ESC_RE = /\x1b(?:\[[0-9;?<=>!]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()*+][0-9A-Za-z]|[@-Z\\-_=>78])/g;
const PARTIAL_ESC_RE = /\x1b(?:\[[0-9;?<=>!]*[ -/]*|\][^\x07\x1b]*|[()*+])?$/;

// Em saída volumosa, segura a linha incompleta do fim (até a continuação chegar ou 40ms),
// para não partir IP/interface/prompt entre dois pedaços.
function trailingHold(s) {
  const tail = s.slice(Math.max(s.lastIndexOf("\n"), s.lastIndexOf("\r")) + 1);
  if (!tail) return "";
  if (tail.length <= 160) return tail;
  const sp = tail.lastIndexOf(" ");
  const part = sp >= 0 ? tail.slice(sp + 1) : tail;
  return part.length <= 64 ? part : "";
}

function isPrivateV4(ip) {
  const [a, b] = ip.split("/")[0].split(".").map(Number);
  return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)
    || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254);
}
const MASK_OCT = new Set([0, 128, 192, 224, 240, 248, 252, 254, 255]);
const WILD_OCT = new Set([0, 1, 3, 7, 15, 31, 63, 127, 255]);
function isMask(ip) {
  if (ip.includes("/")) return false;
  const o = ip.split(".").map(Number);
  if (o[0] === 255 && o.every(x => MASK_OCT.has(x))) return true;            // 255.255.255.0
  if (o[0] === 0 && o.every(x => WILD_OCT.has(x))) return true;             // 0.0.0.0 e wildcard 0.0.0.255
  return false;
}

function colorFor(groups, text) {
  if (groups.mac) return HL_COLORS.mac;
  if (groups.ipv4) return isMask(text) ? HL_COLORS.mask : isPrivateV4(text) ? HL_COLORS.ipPrivate : HL_COLORS.ipPublic;
  if (groups.ipv6) return /^[0-9]+:[0-9]+(:[0-9]+)?$/.test(text) ? null : HL_COLORS.ipv6; // evita horários 12:30:45
  if (groups.dbm) return HL_COLORS.dbm;
  if (groups.iface) return HL_COLORS.iface;
  if (groups.good) return HL_COLORS.good;
  if (groups.bad) return HL_COLORS.bad;
  if (groups.warn) return HL_COLORS.warn;
  return null;
}

function colorize(text) {
  return text.replace(COMBINED, (...args) => {
    const m = args[0];
    const groups = args[args.length - 1];
    const c = colorFor(groups, m);
    return c ? `${rgb(c)}${m}${RESET_FG}` : m;
  });
}

export function highlightText(text, atLineStart = false) {
  if (!text) return text;
  let out = "", last = 0, m;
  PROMPT_G.lastIndex = 0;
  while ((m = PROMPT_G.exec(text))) {
    const pStart = m.index + m[1].length, pEnd = pStart + m[2].length;
    // no começo do pedaço só vale se for o fim dele (pedaço pode começar no meio de uma linha)
    if (m.index === 0 && !m[1] && !atLineStart && text.slice(pEnd).trim() !== "") continue;
    out += colorize(text.slice(last, pStart)) + BOLD + rgb(HL_COLORS.prompt) + m[2] + RESET_FG;
    last = pEnd;
  }
  return out + colorize(text.slice(last));
}

/**
 * Cria um realçador de stream. process(chunk) devolve o texto pronto para term.write;
 * pedaços finais que podem ser metade de um IP/MAC ficam retidos até o próximo chunk
 * (ou até onFlush ser chamado pelo timer, para não travar o eco da digitação).
 */
export function createHighlighter(onFlush) {
  let carry = "";
  let deviceColor = false; // equipamento já está colorindo o texto
  let altScreen = false;   // vim/top/less em tela cheia
  let timer = null;
  let lineStart = true;    // o último texto emitido terminou em quebra de linha

  const processPlain = (t) => {
    if (!t) return t;
    const out = deviceColor || altScreen ? t : highlightText(t, lineStart);
    lineStart = /[\r\n]$/.test(t);
    return out;
  };

  function run(data, final) {
    let s = carry + data;
    carry = "";
    if (!final) {
      const pe = s.match(PARTIAL_ESC_RE);
      if (pe && pe[0]) { carry = pe[0]; s = s.slice(0, -pe[0].length); }
      // só retém em saídas maiores (eco de tecla digitada passa direto, sem atraso)
      if (data.length > 8 && !carry) {
        const hold = trailingHold(s);
        if (hold) { carry = hold; s = s.slice(0, -hold.length); }
      }
    }
    let out = "", last = 0;
    ESC_RE.lastIndex = 0;
    let m;
    while ((m = ESC_RE.exec(s))) {
      out += processPlain(s.slice(last, m.index)) + m[0];
      const esc = m[0];
      if (/^\x1b\[\?(?:1049|47|1047)h$/.test(esc)) altScreen = true;
      else if (/^\x1b\[\?(?:1049|47|1047)l$/.test(esc)) altScreen = false;
      else if (/^\x1b\[[0-9;]*m$/.test(esc)) {
        const params = esc.slice(2, -1).split(";").filter(Boolean).map(Number);
        if (!params.length || params.every(p => p === 0 || p === 39 || p === 22)) deviceColor = false;
        else if (params.some(p => (p >= 30 && p <= 38) || (p >= 90 && p <= 97))) deviceColor = true;
      }
      last = m.index + esc.length;
    }
    out += processPlain(s.slice(last));
    return out;
  }

  return {
    process(data) {
      if (timer) { clearTimeout(timer); timer = null; }
      const out = run(data, false);
      if (carry && onFlush) {
        timer = setTimeout(() => { timer = null; const rest = run("", true); if (rest) onFlush(rest); }, 40);
      }
      return out;
    },
    flush() {
      if (timer) { clearTimeout(timer); timer = null; }
      return run("", true);
    },
    reset() { carry = ""; deviceColor = false; altScreen = false; lineStart = true; },
  };
}
