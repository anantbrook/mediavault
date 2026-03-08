#!/bin/bash
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║        MEDIA VAULT — Universal Downloader    ║"
echo "║         Powered by yt-dlp + Flask            ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 not found!"
    echo "Install it: https://python.org  or  brew install python3"
    exit 1
fi

echo "[OK] Python found: $(python3 --version)"
echo ""
echo "Installing/checking dependencies..."
pip3 install -r requirements.txt --quiet --upgrade
echo "[OK] Dependencies ready"
echo ""
echo "[OK] Starting server at http://localhost:5050"
echo "[OK] Browser will open automatically"
echo ""
echo "Downloads saved to: $(pwd)/downloads/"
echo ""
echo "Press Ctrl+C to stop"
echo "══════════════════════════════════════════════"
echo ""

python3 app.py
