# Force x86_64 so all Kali tools (burpsuite, masscan, etc.) are available on
# Apple Silicon Macs via Rosetta 2. Docker Desktop handles the emulation.
FROM --platform=linux/amd64 kalilinux/kali-rolling:latest

ARG KALI_INCLUDE_HEAVY_TOOLS=0

ENV DEBIAN_FRONTEND=noninteractive \
    KALI_USER=ctf \
    VNC_RESOLUTION=1920x1080 \
    TARGET_URL=http://vbank-app \
    DISPLAY=:1

# ── Pin apt to kali.download mirror (avoids 404s and SSL issues on http.kali.org) ─
RUN rm -f /etc/apt/sources.list.d/kali.sources \
    && printf 'deb http://kali.download/kali kali-rolling main non-free contrib non-free-firmware\n' \
       > /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=3 update

# ── Base update ───────────────────────────────────────────────────────────────
RUN apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
    ca-certificates curl wget git vim nano less tmux jq sudo \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── XFCE4 desktop ─────────────────────────────────────────────────────────────
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
    xfce4 xfce4-terminal xfce4-taskmanager xfce4-screenshooter \
    xfce4-clipman-plugin xfce4-notifyd thunar mousepad \
    dbus-x11 x11-utils x11-xserver-utils xdg-utils \
    fonts-noto fonts-hack \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Kali look-and-feel (themes + wallpapers + icon theme) ─────────────────────
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
    kali-themes kali-themes-common \
    && apt-get clean && rm -rf /var/lib/apt/lists/* || true

# ── Kali wallpapers (try current year first, then legacy) ─────────────────────
RUN apt-get -o Acquire::Retries=3 update \
    && (apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends kali-wallpapers-2024 \
        || apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends kali-wallpapers-2025 \
        || apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends kali-wallpapers-legacy \
        || true) \
    && apt-get clean && rm -rf /var/lib/apt/lists/* || true

# ── VNC + noVNC ───────────────────────────────────────────────────────────────
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
    xvfb x11vnc novnc websockify \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Browser ───────────────────────────────────────────────────────────────────
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends firefox-esr \
    && apt-get clean && rm -rf /var/lib/apt/lists/* || true

# ── Kali security tools — split into groups so one failure won't break others ─
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
       nmap nikto dirb netcat-traditional ncat socat tcpdump proxychains4 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
       hydra john sqlmap wfuzz gobuster ffuf \
    && if [ "$KALI_INCLUDE_HEAVY_TOOLS" = "1" ]; then \
         apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends hashcat tshark masscan burpsuite mitmproxy; \
       fi \
    && apt-get clean && rm -rf /var/lib/apt/lists/* || true

# ── Clipboard support (required by x11vnc for VNC↔browser clipboard sync) ───
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
    xclip xdotool \
    && apt-get clean && rm -rf /var/lib/apt/lists/* || true

# ── Python security libraries ─────────────────────────────────────────────────
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
    python3-pip python3-venv \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/ctf-venv
RUN /opt/ctf-venv/bin/pip install --no-cache-dir \
        requests httpx pycryptodome scapy \
        PyJWT python-jose beautifulsoup4 lxml paramiko flask colorama \
        termcolor
RUN if [ "$KALI_INCLUDE_HEAVY_TOOLS" = "1" ]; then \
      /opt/ctf-venv/bin/pip install --no-cache-dir pwntools impacket || true; \
    fi

# ── jwt_tool ──────────────────────────────────────────────────────────────────
RUN git clone --depth=1 https://github.com/ticarpi/jwt_tool /opt/jwt_tool \
    && chmod +x /opt/jwt_tool/jwt_tool.py \
    && ln -sf /opt/jwt_tool/jwt_tool.py /usr/local/bin/jwt_tool || true

# ── rockyou wordlist ───────────────────────────────────────────────────────────
RUN mkdir -p /usr/share/wordlists \
    && if [ "$KALI_INCLUDE_HEAVY_TOOLS" = "1" ]; then \
         (test -f /usr/share/wordlists/rockyou.txt || \
          test -f /usr/share/wordlists/rockyou.txt.gz && gunzip /usr/share/wordlists/rockyou.txt.gz || \
          curl -sL "https://github.com/brannondorsey/naive-hashcat/releases/download/data/rockyou.txt" \
          -o /usr/share/wordlists/rockyou.txt) || true; \
       else \
         printf 'password\nadmin\nvbank2024\nkali123\n' > /usr/share/wordlists/ctf-small.txt; \
       fi

# ── Identity hooks ────────────────────────────────────────────────────────────
RUN printf '%s\n' \
    '[ -n "$KALI_USER" ] && export USER="$KALI_USER" LOGNAME="$KALI_USER"' \
    '[ -n "$TARGET_URL"  ] && export TARGET_URL="$TARGET_URL"' \
    > /etc/profile.d/ctf-identity.sh \
    && chmod +x /etc/profile.d/ctf-identity.sh \
    && echo '. /etc/profile.d/ctf-identity.sh' >> /etc/bash.bashrc

COPY motd.sh /etc/profile.d/motd.sh
RUN sed -i 's/\r$//' /etc/profile.d/motd.sh \
    && chmod +x /etc/profile.d/motd.sh \
    && echo '. /etc/profile.d/motd.sh' >> /etc/bash.bashrc

COPY startup.sh /opt/startup.sh
RUN sed -i 's/\r$//' /opt/startup.sh && chmod +x /opt/startup.sh

EXPOSE 6901

CMD ["/opt/startup.sh"]
