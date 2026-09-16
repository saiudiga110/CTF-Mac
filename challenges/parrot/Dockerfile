# Use native platform (arm64 on Apple Silicon Macs, amd64 on x86) for best performance
FROM kalilinux/kali-rolling:latest

# Default event profile requested for the lab:
# browser + terminal + Burp + nmap + password tools + working apt installs.
# Optional args can add back heavier tools when a specific event needs them.
ARG KALI_INCLUDE_EXTRA_TOOLS=0
ARG KALI_INCLUDE_HASHCAT=0
ARG KALI_INCLUDE_WORDLISTS=0

ENV DEBIAN_FRONTEND=noninteractive \
    KALI_USER=ctf \
    VNC_RESOLUTION=1920x1080 \
    TARGET_URL=http://vbank-app \
    DISPLAY=:1

# Pin apt to kali.download mirror and keep apt usable inside running containers.
RUN rm -f /etc/apt/sources.list.d/kali.sources \
    && printf 'deb http://kali.download/kali kali-rolling main non-free contrib non-free-firmware\n' \
       > /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
       ca-certificates curl wget git vim nano less tmux jq sudo apt-utils \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Minimal real desktop. Keep XFCE/noVNC/browser/terminal; skip Kali themes and
# wallpapers because they add size but do not help the challenge workflow.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
       xfce4 xfce4-terminal thunar mousepad \
       dbus-x11 x11-utils x11-xserver-utils xdg-utils \
       fonts-noto fonts-hack \
       xvfb x11vnc novnc websockify \
       xclip xdotool \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Required CTF tools: Firefox, Burp Suite, nmap, and password/authentication tools.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
       firefox-esr burpsuite nmap hydra john netcat-traditional \
    && if [ "$KALI_INCLUDE_HASHCAT" = "1" ]; then \
         apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends hashcat; \
       fi \
    && if [ "$KALI_INCLUDE_EXTRA_TOOLS" = "1" ]; then \
         apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
           sqlmap ffuf gobuster wfuzz nikto dirb tcpdump proxychains4 mitmproxy; \
       fi \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* || true

# Python helper libraries used in common web/API tasks. Keep this small by default.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --fix-missing --no-install-recommends \
       python3-pip python3-venv \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && python3 -m venv /opt/ctf-venv \
    && /opt/ctf-venv/bin/pip install --no-cache-dir \
       requests httpx pycryptodome PyJWT beautifulsoup4 paramiko colorama termcolor \
    && if [ "$KALI_INCLUDE_EXTRA_TOOLS" = "1" ]; then \
         /opt/ctf-venv/bin/pip install --no-cache-dir scapy python-jose lxml flask pwntools impacket || true; \
       fi

# jwt_tool is small and useful for the vBank/API style challenges.
RUN git clone --depth=1 https://github.com/ticarpi/jwt_tool /opt/jwt_tool \
    && chmod +x /opt/jwt_tool/jwt_tool.py \
    && ln -sf /opt/jwt_tool/jwt_tool.py /usr/local/bin/jwt_tool \
    && rm -rf /opt/jwt_tool/.git || true

# Keep a small default wordlist. Full rockyou is optional because it increases
# sync/build size and is rarely needed for this lab's default challenges.
RUN mkdir -p /usr/share/wordlists \
    && printf 'password\nadmin\nvbank2024\nkali123\nPassword1\nWelcome1\nLloyds2025\n' > /usr/share/wordlists/ctf-small.txt \
    && if [ "$KALI_INCLUDE_WORDLISTS" = "1" ]; then \
         (test -f /usr/share/wordlists/rockyou.txt || \
          test -f /usr/share/wordlists/rockyou.txt.gz && gunzip /usr/share/wordlists/rockyou.txt.gz || \
          curl -sL "https://github.com/brannondorsey/naive-hashcat/releases/download/data/rockyou.txt" \
          -o /usr/share/wordlists/rockyou.txt) || true; \
       fi

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
