#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Lloyds CTF — Linux Pwn Machine
# Starts a full XFCE4 desktop, VNC, and noVNC web interface.
# Firefox auto-opens to the vBank target on boot.
# ─────────────────────────────────────────────────────────────────────────────
KALI_USER="${KALI_USER:-kali}"
SAFE_USER="$(printf '%s' "$KALI_USER" | tr -cd 'a-zA-Z0-9_-' | cut -c1-32)"
[ -z "$SAFE_USER" ] && SAFE_USER="kali"

TARGET_URL="${TARGET_URL:-http://vbank-app}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1600x900}"
VNC_PORT=5901
NOVNC_PORT=6901

echo "================================================================"
echo "  Lloyds CTF — Kali Linux Pwn Machine"
echo "  User   : $SAFE_USER"
echo "  Target : $TARGET_URL"
echo "  Access : http://HOST:$NOVNC_PORT/vnc.html?autoconnect=true&resize=scale"
echo "================================================================"

# ── Create user ───────────────────────────────────────────────────────────────
if ! id "$SAFE_USER" &>/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo "$SAFE_USER" 2>/dev/null || \
    useradd -m -s /bin/bash "$SAFE_USER"
fi
echo "$SAFE_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/"$SAFE_USER"
chmod 440 /etc/sudoers.d/"$SAFE_USER"

HOME_DIR="/home/$SAFE_USER"
mkdir -p "$HOME_DIR"
chown -R "$SAFE_USER:$SAFE_USER" "$HOME_DIR"

# Make venv python3 the default for the ctf user
if [ -d /opt/ctf-venv ]; then
    ln -sf /opt/ctf-venv/bin/python3 /usr/local/bin/python3 2>/dev/null || true
fi

# ── Serve loading page on port 6901 immediately (avoids ERR_CONNECTION_RESET) ─
# Killed just before websockify takes over the same port.
mkdir -p /tmp/kali_loading
cat > /tmp/kali_loading/index.html << 'LOADHTML'
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Kali Linux — Starting…</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{background:#0a0a0a;color:#39FF14;font-family:'Courier New',monospace;
         display:flex;flex-direction:column;align-items:center;
         justify-content:center;height:100vh;overflow:hidden}
    .logo{font-size:12px;white-space:pre;color:#39FF14;
          text-shadow:0 0 8px #39FF14;margin-bottom:28px;line-height:1.2}
    .title{font-size:22px;font-weight:bold;color:#fff;margin-bottom:6px}
    .sub{font-size:13px;color:#666;margin-bottom:28px}
    #status{font-size:14px;color:#39FF14;margin-bottom:18px;min-height:20px}
    .bar-wrap{width:320px;height:3px;background:#1a1a1a;border-radius:2px;overflow:hidden;margin-bottom:22px}
    .bar{height:100%;background:#39FF14;box-shadow:0 0 8px #39FF14;
         animation:slide 1.8s ease-in-out infinite;width:35%}
    @keyframes slide{0%{transform:translateX(-150%)}100%{transform:translateX(1000%)}}
    .hint{font-size:11px;color:#333}
  </style>
</head>
<body>
  <pre class="logo">
  ██╗  ██╗ █████╗ ██╗      ██╗
  ██║ ██╔╝██╔══██╗██║      ██║
  █████╔╝ ███████║██║      ██║
  ██╔═██╗ ██╔══██║██║      ██║
  ██║  ██╗██║  ██║███████╗ ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═╝</pre>
  <div class="title">Linux Pwn Machine</div>
  <div class="sub">Lloyds vBank CTF — starting up…</div>
  <div id="status">Initialising desktop environment…</div>
  <div class="bar-wrap"><div class="bar"></div></div>
  <div class="hint">This page will connect automatically when ready.</div>
  <script>
    var attempt = 0;
    var msgs = [
      'Initialising desktop environment…',
      'Starting Xvfb display server…',
      'Loading XFCE4 desktop…',
      'Starting VNC server…',
      'Configuring security tools…',
      'Almost ready…'
    ];
    var el = document.getElementById('status');
    function check() {
      attempt++;
      el.textContent = msgs[Math.min(Math.floor(attempt / 2), msgs.length - 1)];
      var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
      var ws = new WebSocket(proto + location.host + '/websockify');
      ws.onopen = function() {
        ws.close();
        el.textContent = 'Desktop ready — connecting…';
        setTimeout(function(){ location.reload(); }, 400);
      };
      ws.onerror = function() { setTimeout(check, 2000); };
    }
    setTimeout(check, 1500);
  </script>
</body>
</html>
LOADHTML
python3 -m http.server $NOVNC_PORT --directory /tmp/kali_loading >/dev/null 2>&1 &
LOADING_SERVER_PID=$!

# ── XFCE config — Kali Linux theme ───────────────────────────────────────────
mkdir -p "$HOME_DIR/.config/xfce4/xfconf/xfce-perchannel-xml"

# Pick best available window manager theme (prefer Kali-Dark, fallback to Kali)
WM_THEME="Kali-Dark"
[ -d "/usr/share/themes/$WM_THEME" ] || WM_THEME="Kali"
[ -d "/usr/share/themes/$WM_THEME" ] || WM_THEME="Adwaita-dark"

# Pick best available GTK theme
GTK_THEME="Kali-Dark"
[ -d "/usr/share/themes/$GTK_THEME" ] || GTK_THEME="Kali"
[ -d "/usr/share/themes/$GTK_THEME" ] || GTK_THEME="Adwaita-dark"

# Pick icon theme
ICON_THEME="Flat-Remix-Blue-Dark"
[ -d "/usr/share/icons/$ICON_THEME" ] || ICON_THEME="Kali-Everything-Flat"
[ -d "/usr/share/icons/$ICON_THEME" ] || ICON_THEME="hicolor"

# Pick Kali wallpaper — prefer dark variants
WALLPAPER=""
for p in \
    /usr/share/backgrounds/kali/kali-default-16x9.png \
    /usr/share/backgrounds/kali/kali-neon.png \
    /usr/share/backgrounds/kali/kali-metal-dark-16x9.png \
    /usr/share/backgrounds/kali/default.png \
    $(find /usr/share/backgrounds/ -name '*dark*16x9*.png' -path '*/kali*' 2>/dev/null | sort | head -1) \
    $(find /usr/share/backgrounds/ -name '*dark*.png' -path '*/kali*' 2>/dev/null | sort | head -1) \
    $(find /usr/share/backgrounds/ -name '*16x9*.png' -path '*/kali*' 2>/dev/null | sort | head -1) \
    $(find /usr/share/backgrounds/ -name 'kali*.png' 2>/dev/null | sort | head -1); do
    [ -f "$p" ] && WALLPAPER="$p" && break
done

cat > "$HOME_DIR/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="theme" type="string" value="${WM_THEME}"/>
    <property name="use_compositing" type="bool" value="false"/>
    <property name="show_frame_shadow" type="bool" value="false"/>
  </property>
</channel>
EOF

cat > "$HOME_DIR/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
  <property name="Net" type="empty">
    <property name="ThemeName" type="string" value="${GTK_THEME}"/>
    <property name="IconThemeName" type="string" value="${ICON_THEME}"/>
  </property>
  <property name="Gtk" type="empty">
    <property name="FontName" type="string" value="Hack 10"/>
    <property name="MonospaceFontName" type="string" value="Hack Mono 10"/>
  </property>
</channel>
EOF

# Pre-write desktop wallpaper config covering common VNC monitor names
if [ -n "$WALLPAPER" ]; then
cat > "$HOME_DIR/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-desktop" version="1.0">
  <property name="backdrop" type="empty">
    <property name="screen0" type="empty">
      <property name="monitor0" type="empty">
        <property name="workspace0" type="empty">
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="${WALLPAPER}"/>
        </property>
      </property>
      <property name="monitorVNC-0" type="empty">
        <property name="workspace0" type="empty">
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="${WALLPAPER}"/>
        </property>
      </property>
      <property name="monitorscreen" type="empty">
        <property name="workspace0" type="empty">
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="${WALLPAPER}"/>
        </property>
      </property>
    </property>
  </property>
</channel>
EOF
fi

# ── XFCE4 panel — bottom taskbar with window switcher ────────────────────────
cat > "$HOME_DIR/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="uint" value="1">
    <property name="panel-0" type="empty">
      <property name="position" type="string" value="p=8;x=0;y=0"/>
      <property name="length" type="int" value="100"/>
      <property name="length-adjust" type="bool" value="true"/>
      <property name="size" type="int" value="36"/>
      <property name="position-locked" type="bool" value="true"/>
      <property name="mode" type="int" value="0"/>
      <property name="nrows" type="int" value="1"/>
      <property name="autohide-behavior" type="int" value="0"/>
      <property name="enable-struts" type="bool" value="true"/>
      <property name="plugin-ids" type="array">
        <value type="int" value="1"/>
        <value type="int" value="2"/>
        <value type="int" value="3"/>
      </property>
    </property>
  </property>
  <property name="plugins" type="empty">
    <property name="plugin-1" type="string" value="applicationsmenu"/>
    <property name="plugin-2" type="string" value="tasklist">
      <property name="grouping" type="int" value="0"/>
      <property name="show-labels" type="bool" value="true"/>
      <property name="show-only-minimized" type="bool" value="false"/>
      <property name="show-wireframes" type="bool" value="false"/>
      <property name="sort-order" type="int" value="4"/>
      <property name="include-all-workspaces" type="bool" value="true"/>
    </property>
    <property name="plugin-3" type="string" value="clock">
      <property name="mode" type="int" value="2"/>
      <property name="digital-format" type="string" value="%H:%M"/>
    </property>
  </property>
</channel>
EOF

# ── XFCE Terminal — dark theme ─────────────────────────────────────────────
mkdir -p "$HOME_DIR/.config/xfce4/terminal"
cat > "$HOME_DIR/.config/xfce4/terminal/terminalrc" << 'EOF'
[Configuration]
MiscDefaultGeometry=200x50
MiscShowUnsafePasteDialog=FALSE
ColorForeground=#00ff41
ColorBackground=#0d1117
ColorCursor=#00ff41
ColorPalette=#073642;#dc322f;#859900;#b58900;#268bd2;#d33682;#2aa198;#eee8d5;#002b36;#cb4b16;#586e75;#657b83;#839496;#6c71c4;#93a1a1;#fdf6e3
FontName=Hack Mono 11
ScrollingLines=50000
EOF

# ── Firefox profile — homepage + security settings ─────────────────────────
FF_DIR="$HOME_DIR/.mozilla/firefox"
FF_PROFILE="$FF_DIR/ctf.default"
mkdir -p "$FF_PROFILE"

cat > "$FF_PROFILE/prefs.js" << EOF
user_pref("browser.startup.homepage", "${TARGET_URL}");
user_pref("browser.startup.page", 1);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("app.shield.optoutstudies.enabled", false);
user_pref("browser.newtabpage.enabled", false);
user_pref("browser.download.useDownloadDir", true);
user_pref("browser.download.dir", "${HOME_DIR}/Downloads");
user_pref("security.tls.version.min", 1);
user_pref("network.proxy.type", 0);
user_pref("devtools.chrome.enabled", true);
user_pref("devtools.debugger.remote-enabled", true);
user_pref("extensions.pocket.enabled", false);
user_pref("browser.urlbar.suggest.searches", false);
user_pref("general.smoothScroll", false);
user_pref("layers.acceleration.disabled", true);
user_pref("gfx.webrender.software", true);
user_pref("browser.cache.disk.enable", false);
user_pref("browser.cache.memory.enable", true);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.tabs.remote.autostart", true);
EOF

cat > "$FF_DIR/profiles.ini" << 'EOF'
[Profile0]
Name=ctf
IsRelative=1
Path=ctf.default
Default=1

[General]
StartWithLastProfile=1
Version=2
EOF

cp "$FF_DIR/profiles.ini" "$FF_PROFILE/profiles.ini" 2>/dev/null || true
chown -R "$SAFE_USER:$SAFE_USER" "$FF_DIR"

# ── Desktop shortcuts ──────────────────────────────────────────────────────────
mkdir -p "$HOME_DIR/Desktop"

cat > "$HOME_DIR/Desktop/vBank-Target.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=vBank Target
Comment=Open vBank CTF target in Firefox
Exec=firefox-esr --profile ${FF_PROFILE} ${TARGET_URL}
Icon=firefox-esr
Terminal=false
Categories=Network;WebBrowser;
EOF

cat > "$HOME_DIR/Desktop/Terminal.desktop" << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Terminal
Comment=Linux Terminal
Exec=xfce4-terminal
Icon=utilities-terminal
Terminal=false
Categories=System;TerminalEmulator;
EOF

if command -v burpsuite >/dev/null 2>&1; then
cat > "$HOME_DIR/Desktop/BurpSuite.desktop" << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Burp Suite
Comment=Web security testing platform
Exec=burpsuite
Icon=burpsuite
Terminal=false
Categories=Network;Security;
EOF
fi

if command -v mitmproxy >/dev/null 2>&1; then
cat > "$HOME_DIR/Desktop/mitmproxy.desktop" << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=mitmproxy (CLI Proxy)
Comment=Interactive HTTP proxy
Exec=xfce4-terminal -e "mitmproxy"
Icon=network-workgroup
Terminal=false
Categories=Network;Security;
EOF
fi

cat > "$HOME_DIR/Desktop/Notes.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Notes
Comment=CTF notes and writeups
Exec=mousepad ${HOME_DIR}/Desktop/notes.txt
Icon=text-editor
Terminal=false
Categories=Utility;TextEditor;
EOF

# Pre-create the notes file with helpful header
cat > "$HOME_DIR/Desktop/notes.txt" << EOF
==============================
  CTF Notes — ${SAFE_USER}
  Target: ${TARGET_URL}
==============================

[Challenge]
[Flags Found]

[Notes]

EOF

# Clean up any potential cheatsheet or race scripts from user desktop / home
rm -f "$HOME_DIR/Desktop/CHEATSHEET"* "$HOME_DIR/race.py" 2>/dev/null || true

chmod 755 "$HOME_DIR/Desktop/"*.desktop 2>/dev/null || true
chown -R "$SAFE_USER:$SAFE_USER" "$HOME_DIR"

# Trust desktop launchers so XFCE skips the "Untrusted application" dialog
for f in "$HOME_DIR/Desktop/"*.desktop; do
    gio set "$f" metadata::trusted true 2>/dev/null || true
done

# ── Burp Suite — pre-configure to allow browser without sandbox (Docker/VNC) ──
mkdir -p "$HOME_DIR/.BurpSuite"
cat > "$HOME_DIR/.BurpSuite/UserConfigCommunity.json" << 'EOF'
{
  "user_options": {
    "burps_browser": {
      "allow_without_sandbox": true,
      "enable_browser": true
    },
    "display": {
      "look_and_feel": "Dark"
    },
    "proxy": {
      "http_history": {
        "sort_column": "time",
        "sort_order": "descending"
      },
      "intercept_client_requests": {
        "enabled": false
      }
    }
  }
}
EOF
chown -R "$SAFE_USER:$SAFE_USER" "$HOME_DIR/.BurpSuite"

# ── Virtual framebuffer ───────────────────────────────────────────────────────
rm -f /tmp/.X{0,1,2}-lock /tmp/.X11-unix/X{0,1,2} 2>/dev/null || true
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
Xvfb :1 -screen 0 "${VNC_RESOLUTION}x24" -ac +extension GLX +render -nolisten tcp &
XVFB_PID=$!
# Poll up to 30s — Docker Desktop on macOS (Rosetta) can be slow to start Xvfb
echo "[PwnMachine] Waiting for Xvfb..."
XVFB_OK=false
for _i in $(seq 1 30); do
    sleep 1
    if xdpyinfo -display :1 >/dev/null 2>&1; then
        XVFB_OK=true
        break
    fi
done
$XVFB_OK || { echo "[ERROR] Xvfb failed to start within 30s"; exit 1; }

# ── Start XFCE4 desktop ───────────────────────────────────────────────────────
su -s /bin/bash "$SAFE_USER" -c "
    export DISPLAY=:1
    export HOME=$HOME_DIR
    export USER=$SAFE_USER
    export LOGNAME=$SAFE_USER
    export TARGET_URL=$TARGET_URL
    export XDG_RUNTIME_DIR=/tmp/xdg_${SAFE_USER}
    export XDG_CONFIG_HOME=$HOME_DIR/.config
    export XDG_DATA_HOME=$HOME_DIR/.local/share
    export XDG_CACHE_HOME=$HOME_DIR/.cache
    mkdir -p \$XDG_RUNTIME_DIR && chmod 700 \$XDG_RUNTIME_DIR
    # Persist dbus address to file so scripts can source it later
    eval \$(dbus-launch --sh-syntax)
    echo \"DBUS_SESSION_BUS_ADDRESS=\$DBUS_SESSION_BUS_ADDRESS\" > /tmp/xdg_${SAFE_USER}/dbus-env
    export DBUS_SESSION_BUS_ADDRESS
    startxfce4 2>/dev/null &
" &
# Poll up to 20s for XFCE to bring up a window manager
echo "[PwnMachine] Waiting for XFCE4..."
for _i in $(seq 1 20); do
    sleep 1
    DISPLAY=:1 xdpyinfo >/dev/null 2>&1 && break
done

# ── Apply Kali wallpaper via xfconf-query after XFCE is running ──────────────
# This covers any monitor name XFCE actually reports (VNC-0, screen, etc.)
if [ -n "$WALLPAPER" ]; then
su -s /bin/bash "$SAFE_USER" -c "
    export DISPLAY=:1
    export HOME=$HOME_DIR
    export XDG_RUNTIME_DIR=/tmp/xdg_${SAFE_USER}
    sleep 2
    for PROP in \$(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep 'last-image'); do
        xfconf-query -c xfce4-desktop -p \"\$PROP\" -s '$WALLPAPER' 2>/dev/null || true
    done
    # Also set via all known monitor name patterns
    for MON in VNC-0 screen screen0 monitor0; do
        xfconf-query -c xfce4-desktop \
            -p /backdrop/screen0/monitor\${MON}/workspace0/last-image \
            -s '$WALLPAPER' 2>/dev/null || true
        xfconf-query -c xfce4-desktop \
            -p /backdrop/screen0/monitor\${MON}/workspace0/image-style \
            -s 5 -t int 2>/dev/null || true
    done
" &
fi

# ── x11vnc (no password, persistent, optimised for smoothness) ───────────────
# Poll until XFCE window manager is actually running (up to 30s extra)
for _i in $(seq 1 30); do
    DISPLAY=:1 xdpyinfo >/dev/null 2>&1 && break
    sleep 1
done

# Set up VNC password if provided (for SSO from CTFd)
if [ -n "$VNC_PASSWORD" ]; then
    x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vncpass 2>/dev/null
    VNC_AUTH="-rfbauth /tmp/vncpass"
else
    VNC_AUTH="-nopw"
fi

x11vnc \
    -display :1 \
    $VNC_AUTH \
    -forever \
    -shared \
    -rfbport $VNC_PORT \
    -xkb \
    -clip both \
    -threads \
    -noxrecord \
    -nap \
    -wait 10 \
    -defer 2 \
    -logfile /tmp/x11vnc.log \
    -bg

# Wait until VNC port is actually listening before continuing
for _i in $(seq 1 20); do
    ss -tlnp 2>/dev/null | grep -q ":${VNC_PORT}" && break
    sleep 0.5
done

# ── Serve a local retry page via Python HTTP so JS can poll the target ────────
# The page retries http://vbank-app every 2s and auto-redirects when it's up.
WAIT_DIR="/tmp/ctf_wait"
mkdir -p "$WAIT_DIR"
cat > "$WAIT_DIR/index.html" << HTMLEOF
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Connecting to vBank…</title>
  <style>
    body { background:#0d1117; color:#c9d1d9; font-family:monospace;
           display:flex; flex-direction:column; align-items:center;
           justify-content:center; height:100vh; margin:0; }
    h2  { color:#39FF14; margin-bottom:.5rem; }
    p   { color:#8b949e; margin:.25rem 0; }
    .dot { animation:blink 1s infinite; }
    @keyframes blink { 50% { opacity:0; } }
  </style>
</head>
<body>
  <h2>Connecting to vBank target<span class="dot">…</span></h2>
  <p>Target URL: <strong>TARGET_URL_PLACEHOLDER</strong></p>
  <p id="st">Waiting for target to start…</p>
  <script>
    var t = "TARGET_URL_PLACEHOLDER", n = 0;
    function go() {
      n++;
      document.getElementById("st").textContent = "Attempt " + n + " — retrying in 2s";
      fetch(t + "/?_probe=" + n, {mode:"no-cors"})
        .then(function() { window.location.href = t; })
        .catch(function() { setTimeout(go, 2000); });
    }
    go();
  </script>
</body>
</html>
HTMLEOF
# Replace placeholder with actual target URL
sed -i "s|TARGET_URL_PLACEHOLDER|${TARGET_URL}|g" "$WAIT_DIR/index.html"

# Start a lightweight HTTP server on port 8765 to serve the wait page
python3 -m http.server 8765 --directory "$WAIT_DIR" &>/dev/null &
WAIT_SERVER_PID=$!

# Auto-launch Firefox → vBank target (directly if up, else local retry page)
if curl -sf --max-time 5 "${TARGET_URL}" > /dev/null 2>&1; then
    OPEN_URL="${TARGET_URL}"
    echo "[PwnMachine] Target is up — opening ${TARGET_URL}"
else
    OPEN_URL="http://127.0.0.1:8765/"
    echo "[PwnMachine] Target not ready — showing retry page at ${OPEN_URL}"
fi

su -s /bin/bash "$SAFE_USER" -c "
    export DISPLAY=:1
    export HOME=$HOME_DIR
    export XDG_RUNTIME_DIR=/tmp/xdg_${SAFE_USER}
    firefox-esr --profile ${FF_PROFILE} --new-window '${OPEN_URL}' 2>/dev/null &
" &
sleep 2

# ── noVNC websockify ──────────────────────────────────────────────────────────
# Find noVNC web root (location differs by distro/install method)
if   [ -d /usr/share/novnc ];  then NOVNC_DIR="/usr/share/novnc"
elif [ -d /opt/novnc ];        then NOVNC_DIR="/opt/novnc"
else
    echo "[ERROR] noVNC web root not found"; exit 1
fi

# ── Custom fullscreen index — auto-connects with best quality settings ────────
# Write a fullscreen noVNC page — no top bar, auto-connect, scales to window.
# We write directly to index.html (symlink already removed above).
rm -f "$NOVNC_DIR/index.html"
cat > "$NOVNC_DIR/index.html" << 'NOVNCEOF'
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kali Pwn Machine</title>
  <style>
    *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 100%; height: 100%; overflow: hidden; background: #0d1117; }
    #screen { width: 100%; height: 100%; }

    /* Status overlay */
    #status-overlay {
      position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      background: #0d1117; font-family: monospace; z-index: 900;
      gap: 14px; transition: opacity .5s;
    }
    #status-overlay.hidden { opacity: 0; pointer-events: none; }
    #status-title { color: #39FF14; font-size: 18px; font-weight: bold; }
    #status-msg   { color: #8b949e; font-size: 14px; }

    /* Clipboard toggle button */
    #cb-toggle {
      position: fixed; top: 10px; right: 10px; z-index: 1000;
      background: rgba(22,27,34,0.92); border: 1px solid #30363d;
      color: #8b949e; border-radius: 8px; padding: 6px 12px;
      font-family: monospace; font-size: 13px; cursor: pointer;
      display: flex; align-items: center; gap: 6px;
      backdrop-filter: blur(6px);
      transition: background .2s, border-color .2s, color .2s;
    }
    #cb-toggle:hover { background: rgba(48,54,61,.95); border-color: #39FF14; color: #e6edf3; }
    #cb-toggle.flash { border-color: #39FF14 !important; color: #39FF14 !important; }

    /* Clipboard panel */
    #cb-panel {
      position: fixed; top: 46px; right: 10px; z-index: 1000;
      width: 320px;
      background: rgba(13,17,23,.97); border: 1px solid #30363d; border-radius: 10px;
      padding: 12px; font-family: monospace; font-size: 13px;
      display: none; flex-direction: column; gap: 8px;
      box-shadow: 0 8px 32px rgba(0,0,0,.6); backdrop-filter: blur(8px);
    }
    #cb-panel.open { display: flex; }
    #cb-label { color: #6e7681; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }
    #cb-text {
      width: 100%; height: 90px; resize: vertical;
      background: #161b22; border: 1px solid #30363d; border-radius: 6px;
      color: #e6edf3; font-family: monospace; font-size: 13px; padding: 8px;
      outline: none; line-height: 1.5;
    }
    #cb-text:focus { border-color: #39FF14; box-shadow: 0 0 0 2px rgba(57,255,20,.12); }
    #cb-row { display: flex; gap: 8px; }
    .cb-btn {
      flex: 1; padding: 6px 0; border-radius: 6px; border: 1px solid #30363d;
      font-family: monospace; font-size: 12px; cursor: pointer;
      background: #161b22; color: #8b949e;
      transition: background .15s, border-color .15s, color .15s;
    }
    .cb-btn:hover { background: #21262d; color: #e6edf3; }
    #cb-send { border-color: #39FF14; color: #39FF14; }
    #cb-send:hover { background: rgba(57,255,20,.12); }
    #cb-hint { color: #6e7681; font-size: 11px; line-height: 1.6; }
    #cb-hint b { color: #8b949e; }
  </style>
</head>
<body>
  <div id="status-overlay">
    <div id="status-title">&#128187; Kali Pwn Machine</div>
    <div id="status-msg">Connecting&hellip;</div>
  </div>

  <button id="cb-toggle" title="Open clipboard bridge (Ctrl+Shift+V)">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>
      <rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>
    </svg>
    Clipboard
  </button>

  <div id="cb-panel">
    <div id="cb-label">Clipboard Bridge</div>
    <textarea id="cb-text" placeholder="&#x2022; Browser&#x2192;Kali: paste your text here, click Send to Kali&#10;&#x2022; Kali&#x2192;Browser: copy in terminal, text appears here automatically"></textarea>
    <div id="cb-row">
      <button class="cb-btn" id="cb-clear">Clear</button>
      <button class="cb-btn" id="cb-copy">Copy</button>
      <button class="cb-btn" id="cb-send">Send to Kali</button>
    </div>
    <div id="cb-hint">
      <b>Shortcut:</b> Ctrl+Shift+V toggles this panel.<br>
      After sending, middle-click or Ctrl+Shift+V in terminal to paste.
    </div>
  </div>

  <div id="screen"></div>
  <script type="module">
    import RFB from './core/rfb.js';

    let rfb = null;
    const overlay  = document.getElementById('status-overlay');
    const cbToggle = document.getElementById('cb-toggle');
    const cbPanel  = document.getElementById('cb-panel');
    const cbText   = document.getElementById('cb-text');

    /* Panel toggle */
    function openPanel()  { cbPanel.classList.add('open');    cbText.focus(); }
    function closePanel() { cbPanel.classList.remove('open'); }
    function togglePanel(){ cbPanel.classList.contains('open') ? closePanel() : openPanel(); }

    cbToggle.addEventListener('click', togglePanel);
    document.addEventListener('mousedown', (e) => {
      if (!cbPanel.contains(e.target) && e.target !== cbToggle) closePanel();
    });
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.shiftKey && e.key === 'V') { e.preventDefault(); togglePanel(); }
    });

    /* Send text to Kali */
    document.getElementById('cb-send').addEventListener('click', () => {
      const text = cbText.value;
      if (!text || !rfb) return;
      try { rfb.clipboardPasteFrom(text); } catch(err) {}
      cbToggle.classList.add('flash');
      setTimeout(() => cbToggle.classList.remove('flash'), 900);
    });

    /* Copy panel content to browser clipboard */
    document.getElementById('cb-copy').addEventListener('click', async () => {
      const text = cbText.value;
      if (!text) return;
      const btn = document.getElementById('cb-copy');
      try { await navigator.clipboard.writeText(text); }
      catch(e) { cbText.select(); document.execCommand('copy'); }
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    });
    document.getElementById('cb-clear').addEventListener('click', () => {
      cbText.value = ''; cbText.focus();
    });

    /* Ctrl+V when canvas has focus and panel is closed */
    document.addEventListener('paste', (e) => {
      if (cbPanel.classList.contains('open')) return;
      const text = (e.clipboardData || window.clipboardData).getData('text');
      if (text && rfb) { try { rfb.clipboardPasteFrom(text); } catch(err) {} }
    });

    /* Connect */
    const url = (location.protocol === 'https:' ? 'wss' : 'ws')
              + '://' + location.hostname
              + (location.port ? ':' + location.port : '')
              + '/websockify';

    rfb = new RFB(document.getElementById('screen'), url);
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.clipViewport  = false;
    rfb.qualityLevel  = 6;
    rfb.compressionLevel = 2;

    rfb.addEventListener('connect', () => {
      overlay.classList.add('hidden');
      try { rfb.focus(); } catch(e) {}
    });
    rfb.addEventListener('disconnect', (e) => {
      overlay.classList.remove('hidden');
      const msg = document.getElementById('status-msg');
      if (e.detail.clean) {
        msg.textContent = 'Disconnected — reload to reconnect';
      } else {
        msg.textContent = 'Connection lost — retrying…';
        setTimeout(() => location.reload(), 3000);
      }
    });
    rfb.addEventListener('credentialsrequired', () => {
      const pwd = new URLSearchParams(location.search).get('autoauth')
               || new URLSearchParams(location.search).get('password') || '';
      rfb.sendCredentials({ password: pwd });
    });

    /* Receive clipboard FROM Kali */
    rfb.addEventListener('clipboard', (e) => {
      const text = e.detail.text;
      if (!text) return;
      cbText.value = text;
      cbToggle.classList.add('flash');
      setTimeout(() => cbToggle.classList.remove('flash'), 1200);
      try { navigator.clipboard.writeText(text).catch(() => {}); } catch(e) {}
    });
  </script>
</body>
</html>
NOVNCEOF

echo "[PwnMachine] Ready — http://HOST:${NOVNC_PORT}/ (auto fullscreen)"

# Hand off port 6901 from loading page server to websockify
kill $LOADING_SERVER_PID 2>/dev/null
sleep 0.5   # let the port fully release before websockify binds

exec websockify \
    --web="$NOVNC_DIR" \
    --heartbeat=30 \
    $NOVNC_PORT \
    localhost:$VNC_PORT
