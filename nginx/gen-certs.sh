#!/usr/bin/env bash
# Generate a local CA + wildcard *.lab TLS certificate for the CTF platform.
# Run once: bash nginx/gen-certs.sh
# Outputs:
#   nginx/certs/ca.crt          <- install this on every player's machine
#   nginx/certs/default.crt     <- nginx-proxy wildcard server cert
#   nginx/certs/default.key     <- nginx-proxy wildcard server key

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERTS_DIR="$SCRIPT_DIR/certs"
mkdir -p "$CERTS_DIR"

# Check if certs already exist
if [[ -f "$CERTS_DIR/default.crt" && -f "$CERTS_DIR/default.key" ]]; then
    echo "✓ Certificates already exist in $CERTS_DIR"
    ls -lh "$CERTS_DIR"
    exit 0
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Generating TLS Certificates for *.lab"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

CA_KEY="$CERTS_DIR/ca.key"
CA_CRT="$CERTS_DIR/ca.crt"
SRV_KEY="$CERTS_DIR/default.key"
SRV_CSR="$CERTS_DIR/default.csr"
SRV_CRT="$CERTS_DIR/default.crt"
EXT_FILE="$CERTS_DIR/san.ext"

# Function to clean up on error
cleanup_on_error() {
    echo "✗ Certificate generation failed!"
    rm -f "$SRV_CSR" "$EXT_FILE" "$CERTS_DIR/ca.srl"
    exit 1
}

trap cleanup_on_error ERR

# ── 1. Local CA ───────────────────────────────────────────────────────────────
if [[ ! -f "$CA_KEY" ]]; then
    echo "[1/6] Generating local CA key..."
    openssl genrsa -out "$CA_KEY" 4096 2>/dev/null || { echo "✗ Failed to generate CA key"; cleanup_on_error; }
    echo "     ✓ CA key generated"
else
    echo "[1/6] Using existing CA key"
fi

if [[ ! -f "$CA_CRT" ]]; then
    echo "[2/6] Creating self-signed CA certificate (valid 10 years)..."
    openssl req -new -x509 -days 3650 -key "$CA_KEY" -out "$CA_CRT" \
      -subj "/C=GB/O=Lloyds CTF Local CA/CN=Lloyds CTF Root CA" 2>/dev/null || \
      { echo "✗ Failed to create CA certificate"; cleanup_on_error; }
    echo "     ✓ CA certificate created"
else
    echo "[2/6] Using existing CA certificate"
fi

# ── 2. Server key + CSR ───────────────────────────────────────────────────────
echo "[3/6] Generating server private key..."
openssl genrsa -out "$SRV_KEY" 2048 2>/dev/null || \
    { echo "✗ Failed to generate server key"; cleanup_on_error; }
echo "     ✓ Server key generated"

echo "[4/6] Creating Certificate Signing Request (CSR) for *.lab..."
openssl req -new -key "$SRV_KEY" -out "$SRV_CSR" \
  -subj "/C=GB/O=Lloyds CTF/CN=*.lab" 2>/dev/null || \
  { echo "✗ Failed to create CSR"; cleanup_on_error; }
echo "     ✓ CSR created"

# ── 3. SAN extension file ─────────────────────────────────────────────────────
echo "[5/6] Creating Subject Alternative Names (SAN) extension..."
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
echo "     ✓ SAN extension created"

# ── 4. Sign with CA ───────────────────────────────────────────────────────────
echo "[6/6] Signing server certificate with local CA (valid 2 years)..."
openssl x509 -req -days 730 \
  -in "$SRV_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
  -out "$SRV_CRT" -extfile "$EXT_FILE" 2>/dev/null || \
  { echo "✗ Failed to sign certificate"; cleanup_on_error; }
echo "     ✓ Certificate signed successfully"

# ── 5. Cleanup temp files ─────────────────────────────────────────────────────
rm -f "$SRV_CSR" "$EXT_FILE" "$CERTS_DIR/ca.srl"

echo ""
echo "✓ Certificate generation complete!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Files in $CERTS_DIR:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ls -lh "$CERTS_DIR" | tail -n +2
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ⚠  IMPORTANT: Install CA certificate"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Players must install: $CERTS_DIR/ca.crt"
echo ""
echo "  Windows:"
echo "    1. Download ca.crt from the CTF server"
echo "    2. Double-click ca.crt"
echo "    3. Click \"Install Certificate\""
echo "    4. Select \"Local Machine\" → \"Trusted Root Certification Authorities\""
echo "    5. Click \"Finish\""
echo ""
echo "  macOS:"
echo "    sudo security add-trusted-cert -d -r trustRoot \\"
echo "      -k /Library/Keychains/System.keychain $CERTS_DIR/ca.crt"
echo ""
echo "  Linux (Ubuntu/Debian):"
echo "    sudo cp $CERTS_DIR/ca.crt /usr/local/share/ca-certificates/lloyds-ctf.crt"
echo "    sudo update-ca-certificates"
echo ""
echo "  Linux (Fedora/RHEL):"
echo "    sudo cp $CERTS_DIR/ca.crt /etc/pki/ca-trust/source/anchors/"
echo "    sudo update-ca-trust"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
