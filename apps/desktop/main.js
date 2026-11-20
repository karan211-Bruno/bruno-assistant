const path = require("node:path");
const { pathToFileURL } = require("node:url");
const {
  app,
  BrowserWindow,
  Menu,
  Notification,
  Tray,
  globalShortcut,
  ipcMain,
  net,
  protocol,
  session,
  shell,
} = require("electron");

protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
]);

const WEB_ROOT = path.resolve(__dirname, "web");
let mainWindow = null;
let tray = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 390,
    minHeight: 600,
    title: "Bruno AI",
    backgroundColor: "#0c1110",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.loadURL("app://bruno/index.html");

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        shell.openExternal(parsed.href);
      }
    } catch {
      // ignore invalid URLs
    }
    return { action: "deny" };
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function toggleWindow() {
  if (!mainWindow) {
    createWindow();
    return;
  }
  if (mainWindow.isVisible() && mainWindow.isFocused()) {
    mainWindow.hide();
  } else {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }
}

function setupTray() {
  const iconPath = path.join(__dirname, "build", "tray.png");
  try {
    tray = new Tray(iconPath);
  } catch {
    return;
  }
  tray.setToolTip("Bruno AI — 개인 비서");
  const menu = Menu.buildFromTemplate([
    {
      label: "Bruno AI 열기",
      click: () => {
        if (!mainWindow) createWindow();
        else {
          mainWindow.show();
          mainWindow.focus();
        }
      },
    },
    { type: "separator" },
    {
      label: "종료",
      click: () => app.quit(),
    },
  ]);
  tray.setContextMenu(menu);
  tray.on("double-click", toggleWindow);
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    protocol.handle("app", (request) => {
      const parsed = new URL(request.url);
      let rel = decodeURIComponent(parsed.pathname || "/");
      if (rel === "/" || rel === "") rel = "/index.html";
      const target = path.resolve(WEB_ROOT, "." + rel);
      if (!target.startsWith(WEB_ROOT + path.sep) && target !== WEB_ROOT) {
        return new Response("Forbidden", { status: 403 });
      }
      return net.fetch(pathToFileURL(target).toString());
    });

    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
      if (permission === "media" || permission === "notifications") {
        callback(true);
      } else {
        callback(false);
      }
    });

    ipcMain.on("bruno:notify", (_evt, payload) => {
      if (Notification.isSupported()) {
        new Notification({
          title: payload?.title || "Bruno AI",
          body: payload?.body || "",
        }).show();
      }
    });

    createWindow();
    setupTray();

    try {
      globalShortcut.register("CommandOrControl+Shift+B", toggleWindow);
    } catch {
      // ignore shortcut registration conflicts
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      } else if (mainWindow) {
        mainWindow.show();
      }
    });
  });

  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
      app.quit();
    }
  });
}
