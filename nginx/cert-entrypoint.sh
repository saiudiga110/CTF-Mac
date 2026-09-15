#!/bin/bash
# Certificate auto-generation entrypoint for nginx-proxy
# Ensures certificates exist before nginx starts
# This runs on container startup - completely automated

set -e

CERTS_DIR="/etc/nginx/certs"
CA_KEY="$CERTS_DIR/ca.key"
CA_CRT="$CERTS_DIR/ca.crt"
SRV_KEY="$CERTS_DIR/default.key"
SRV_CSR="$CERTS_DIR/default.csr"
SRV_CRT="$CERTS_DIR/default.crt"
EXT_FILE="$CERTS_DIR/san.ext"

echo "[nginx-proxy] Checking TLS certificates..."

cert_key_matches() {
    [[ -f "$SRV_CRT" && -f "$SRV_KEY" ]] || return 1

    cert_mod="$(openssl x509 -noout -modulus -in "$SRV_CRT" 2>/dev/null | openssl md5 2>/dev/null || true)"
    key_mod="$(openssl rsa -noout -modulus -in "$SRV_KEY" 2>/dev/null | openssl md5 2>/dev/null || true)"

    [[ -n "$cert_mod" && "$cert_mod" == "$key_mod" ]]
}

# Function to generate certificates
generate_certs() {
    echo "[nginx-proxy] Generating TLS certificates for *.lab (first-time setup)..."
    
    # Ensure certs directory exists with proper permissions
    mkdir -p "$CERTS_DIR"
    chmod 755 "$CERTS_DIR"
    
    # CA Key
    if [[ ! -f "$CA_KEY" ]]; then
        echo "[nginx-proxy] → Generating CA key..."
        openssl genrsa -out "$CA_KEY" 4096 2>/dev/null || return 1
    fi
    
    # CA Certificate
    if [[ ! -f "$CA_CRT" ]]; then
        echo "[nginx-proxy] → Creating CA certificate..."
        openssl req -new -x509 -days 3650 -key "$CA_KEY" -out "$CA_CRT" \
          -subj "/C=GB/O=Lloyds CTF Local CA/CN=Lloyds CTF Root CA" 2>/dev/null || return 1
    fi
    
    # Server Key
    if [[ ! -f "$SRV_KEY" ]]; then
        echo "[nginx-proxy] → Generating server key..."
        openssl genrsa -out "$SRV_KEY" 2048 2>/dev/null || return 1
    fi
    
    # CSR
    if [[ ! -f "$SRV_CSR" ]]; then
        echo "[nginx-proxy] → Creating certificate signing request..."
        openssl req -new -key "$SRV_KEY" -out "$SRV_CSR" \
          -subj "/C=GB/O=Lloyds CTF/CN=*.lab" 2>/dev/null || return 1
    fi
    
    # SAN Extension
    cat > "$EXT_FILE" <<'EOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names

[alt_names]
DNS.1=*.lab
DNS.2=ctfd.lab
DNS.3=lloydsctf.lab
DNS.4=localhost
IP.1=127.0.0.1
EOF
    
    # Sign Certificate
    if [[ ! -f "$SRV_CRT" ]]; then
        echo "[nginx-proxy] → Signing server certificate..."
        openssl x509 -req -days 730 \
          -in "$SRV_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
          -out "$SRV_CRT" -extfile "$EXT_FILE" 2>/dev/null || return 1
    fi
    
    # Cleanup temp files
    rm -f "$SRV_CSR" "$EXT_FILE" "$CERTS_DIR/ca.srl" 2>/dev/null || true
    
    echo "[nginx-proxy] ✓ Certificates generated successfully"
    return 0
}

# Check if certs exist
if [[ -f "$SRV_CRT" && -f "$SRV_KEY" && -f "$CA_CRT" ]]; then
    if cert_key_matches; then
        echo "[nginx-proxy] ✓ Using existing certificates"
    else
        echo "[nginx-proxy] ⚠ Server certificate/key mismatch, regenerating server certificate..."
        rm -f "$SRV_KEY" "$SRV_CSR" "$SRV_CRT" "$EXT_FILE" "$CERTS_DIR/ca.srl" 2>/dev/null || true
        if ! generate_certs; then
            echo "[nginx-proxy] ✗ Certificate regeneration failed"
            exit 1
        fi
    fi
else
    echo "[nginx-proxy] ⚠ Certificates not found, generating..."
    if ! generate_certs; then
        echo "[nginx-proxy] ✗ Certificate generation failed"
        exit 1
    fi
fi

# Set permissions
chmod 644 "$SRV_CRT" "$SRV_KEY" "$CA_CRT" 2>/dev/null || true
chmod 755 "$CERTS_DIR" 2>/dev/null || true

echo "[nginx-proxy] Starting nginx-proxy with TLS support..."

# Continue with normal nginx-proxy entrypoint
exec /app/docker-entrypoint.sh "$@"
