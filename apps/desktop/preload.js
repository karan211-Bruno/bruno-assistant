const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("brunoNative", {
  isNative: true,
  platform: process.platform,
  defaultServerUrl: "https://bruno-sony.tail617e7a.ts.net",
  notify: (title, body) => {
    ipcRenderer.send("bruno:notify", {
      title: String(title || "Bruno AI"),
      body: String(body || ""),
    });
  },
  httpRequest: (opts) => ipcRenderer.invoke("bruno:http", opts),
});
