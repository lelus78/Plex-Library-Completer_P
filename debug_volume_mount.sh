#!/bin/bash

echo "🔧 DEBUG: Controllo mount dei volumi Docker"
echo "=========================================="

# Test if downloads directory exists and is writable
if [ -d "/downloads" ]; then
    echo "✅ Directory /downloads esiste"
    
    # Check permissions
    PERMS=$(stat -c "%a" /downloads 2>/dev/null || echo "unknown")
    OWNER=$(stat -c "%U:%G" /downloads 2>/dev/null || echo "unknown")
    echo "📂 Permessi: $PERMS, Owner: $OWNER"
    
    # Test write permissions
    TEST_FILE="/downloads/test_docker_mount_$(date +%s).txt"
    if echo "Test Docker mount" > "$TEST_FILE" 2>/dev/null; then
        echo "✅ Test scrittura: SUCCESSO"
        rm -f "$TEST_FILE" 2>/dev/null
    else
        echo "❌ Test scrittura: FALLITO"
    fi
    
    # List contents
    echo "📋 Contenuti directory /downloads:"
    ls -la /downloads/ | head -20
    
else
    echo "❌ Directory /downloads NON esiste"
fi

# Check mount points
echo ""
echo "🔗 Mount points Docker:"
mount | grep downloads || echo "Nessun mount per /downloads trovato"

# Check environment variables
echo ""
echo "🌍 Variabili d'ambiente:"
echo "PUID=$PUID"
echo "PGID=$PGID"  
echo "DOCKER_HOST_OS=$DOCKER_HOST_OS"
echo "MUSIC_DOWNLOAD_PATH=$MUSIC_DOWNLOAD_PATH"

# Check if running as correct user
echo ""
echo "👤 Utente corrente: $(whoami) (UID: $(id -u), GID: $(id -g))"