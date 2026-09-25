// Só as telas locais do app (setup/offline, carregadas de file://) recebem estas funções.
// As páginas do servidor não têm acesso a nada do Windows.
const { contextBridge, ipcRenderer } = require("electron");

if (location.protocol === "file:") {
  contextBridge.exposeInMainWorld("bastionDesktop", {
    getConfig: () => ipcRenderer.invoke("bastion:get-config"),
    saveServer: (url, insecure) => ipcRenderer.invoke("bastion:save-server", { url, insecure }),
    retry: () => ipcRenderer.invoke("bastion:retry"),
    changeServer: () => ipcRenderer.invoke("bastion:change-server"),
  });
}
