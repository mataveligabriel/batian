// Bastion SSH — app desktop (Electron).
// Não roda nada localmente: abre o servidor Bastion (Linux) numa janela própria.
// O cache HTTP é limpo a cada abertura, então o app sempre mostra a versão que está no servidor.
const { app, BrowserWindow, Menu, shell, session, ipcMain, dialog } = require("electron");
const path = require("path");
const fs = require("fs");

const CONFIG_FILE = path.join(app.getPath("userData"), "config.json");
const ICON = path.join(__dirname, "build", "icon.png");

function readConfig() {
  try { return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8")); } catch { return {}; }
}
function writeConfig(patch) {
  const cfg = { ...readConfig(), ...patch };
  fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2));
  return cfg;
}
function normalizeUrl(raw) {
  let u = String(raw || "").trim();
  if (!u) return "";
  if (!/^https?:\/\//i.test(u)) u = "http://" + u;
  try { const p = new URL(u); return `${p.protocol}//${p.host}`; } catch { return ""; }
}

let config = readConfig();
const serverOrigin = () => normalizeUrl(config.serverUrl);

// ---- flags que precisam ser aplicadas antes do app ficar pronto ----
app.commandLine.appendSwitch("disable-http-cache");
// Servidor acessado por IP em http: o Chromium só libera a área de transferência
// (terminal: selecionar copia / botão direito cola) em "contexto seguro".
if (serverOrigin().startsWith("http://")) {
  app.commandLine.appendSwitch("unsafely-treat-insecure-origin-as-secure", serverOrigin());
}

if (!app.requestSingleInstanceLock()) app.quit();

let win = null;

function page(name) { return path.join(__dirname, "pages", name); }

function loadServer() {
  if (!win) return;
  const url = serverOrigin();
  if (!url) return win.loadFile(page("setup.html"));
  win.loadURL(url);
}

function createWindow() {
  const b = config.bounds || {};
  win = new BrowserWindow({
    width: b.width || 1440, height: b.height || 900, x: b.x, y: b.y,
    minWidth: 900, minHeight: 600,
    title: "Bastion SSH", icon: ICON, backgroundColor: "#05070A", autoHideMenuBar: true, show: false,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, spellcheck: false },
  });
  if (config.maximized !== false) win.maximize();
  win.once("ready-to-show", () => win.show());

  const saveBounds = () => {
    if (!win || win.isDestroyed()) return;
    config = writeConfig({ bounds: win.isMaximized() ? config.bounds : win.getBounds(), maximized: win.isMaximized() });
  };
  win.on("resize", saveBounds); win.on("move", saveBounds); win.on("close", saveBounds);

  // links para fora do servidor abrem no navegador padrão
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (serverOrigin() && url.startsWith(serverOrigin())) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("page-title-updated", (e) => { e.preventDefault(); win.setTitle("Bastion SSH"); });

  // servidor fora do ar / IP errado -> tela de erro com "tentar de novo" e "alterar servidor"
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return; // -3 = navegação cancelada
    win.loadFile(page("offline.html"), { query: { url: serverOrigin(), erro: `${desc} (${code})` } });
  });

  loadServer();
}

// certificado inválido (https por IP / autoassinado): só aceita se o usuário marcou e se for o servidor configurado
app.on("certificate-error", (event, _wc, url, _err, _cert, callback) => {
  const ok = config.ignoreCertErrors && serverOrigin() && url.startsWith(serverOrigin());
  if (ok) event.preventDefault();
  callback(!!ok);
});

async function changeServer() {
  win.loadFile(page("setup.html"), { query: { url: serverOrigin(), insecure: config.ignoreCertErrors ? "1" : "" } });
}

function buildMenu() {
  const tpl = [
    {
      label: "Bastion",
      submenu: [
        { label: "Recarregar", accelerator: "F5", click: () => win?.webContents.reload() },
        { label: "Recarregar (ignorar cache)", accelerator: "CmdOrCtrl+Shift+R", click: () => win?.webContents.reloadIgnoringCache() },
        { type: "separator" },
        { label: "Alterar servidor…", click: changeServer },
        { label: `Servidor atual: ${serverOrigin() || "não configurado"}`, enabled: false },
        { type: "separator" },
        { label: "Sair", accelerator: "Alt+F4", role: "quit" },
      ],
    },
    {
      label: "Exibir",
      submenu: [
        { role: "zoomIn", label: "Aumentar zoom", accelerator: "CmdOrCtrl+=" },
        { role: "zoomOut", label: "Diminuir zoom" },
        { role: "resetZoom", label: "Zoom normal" },
        { type: "separator" },
        { role: "togglefullscreen", label: "Tela cheia", accelerator: "F11" },
        { role: "toggleDevTools", label: "Ferramentas de desenvolvedor", accelerator: "CmdOrCtrl+Shift+I" },
      ],
    },
    {
      label: "Editar",
      submenu: [
        { role: "copy", label: "Copiar" }, { role: "paste", label: "Colar" }, { role: "selectAll", label: "Selecionar tudo" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(tpl));
}

// ---- chamadas das telas locais (setup / offline) ----
ipcMain.handle("bastion:get-config", () => ({ url: serverOrigin(), insecure: !!config.ignoreCertErrors }));
ipcMain.handle("bastion:save-server", async (_e, { url, insecure }) => {
  const origin = normalizeUrl(url);
  if (!origin) return { ok: false, error: "Endereço inválido. Ex.: 177.184.221.5 ou https://bastion.suaempresa.com" };
  const changed = origin !== serverOrigin();
  config = writeConfig({ serverUrl: origin, ignoreCertErrors: !!insecure });
  buildMenu();
  // a liberação da área de transferência para http é definida na inicialização -> reinicia se o endereço mudou
  if (changed && origin.startsWith("http://")) {
    app.relaunch();
    app.exit(0);
    return { ok: true };
  }
  loadServer();
  return { ok: true };
});
ipcMain.handle("bastion:retry", () => { loadServer(); return true; });
ipcMain.handle("bastion:change-server", () => { changeServer(); return true; });

app.whenReady().then(async () => {
  // limpa o cache HTTP a cada abertura (login, preferências e abas continuam salvos)
  try {
    await session.defaultSession.clearCache();
    await session.defaultSession.clearStorageData({ storages: ["serviceworkers", "cachestorage", "shadercache"] });
  } catch { /* noop */ }
  // área de transferência do terminal (copiar ao selecionar / colar com botão direito)
  session.defaultSession.setPermissionRequestHandler((wc, permission, cb) => {
    const fromServer = serverOrigin() && wc.getURL().startsWith(serverOrigin());
    cb(fromServer && ["clipboard-read", "clipboard-sanitized-write", "notifications", "fullscreen"].includes(permission));
  });
  session.defaultSession.setPermissionCheckHandler((wc, permission) => {
    const fromServer = serverOrigin() && wc && wc.getURL().startsWith(serverOrigin());
    return !!(fromServer && ["clipboard-read", "clipboard-sanitized-write"].includes(permission));
  });
  buildMenu();
  createWindow();
});

app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.on("window-all-closed", () => app.quit());
process.on("uncaughtException", (e) => { try { dialog.showErrorBox("Bastion SSH", String(e?.stack || e)); } catch { /* noop */ } });
