#!/usr/bin/env python3
"""Script di debug per verificare il database sync_database.db"""

import os
import sqlite3
import sys

def debug_database():
    # Prova diversi path possibili
    possible_paths = [
        "./state_data/sync_database.db",
        "/app/state_data/sync_database.db",
        "/mnt/e/Docker image/Plex-Library-Completer/state_data/sync_database.db"
    ]
    
    print("=== DEBUG DATABASE SYNC ===")
    
    for db_path in possible_paths:
        print(f"\n🔍 Controllando path: {db_path}")
        
        if os.path.exists(db_path):
            print(f"✅ Database trovato: {db_path}")
            
            # Dimensione file
            size = os.path.getsize(db_path)
            print(f"📊 Dimensione: {size} bytes")
            
            # Connessione e query
            try:
                with sqlite3.connect(db_path) as con:
                    cur = con.cursor()
                    
                    # Lista tabelle
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [row[0] for row in cur.fetchall()]
                    print(f"📋 Tabelle trovate: {tables}")
                    
                    # Conta tracce nell'indice
                    if 'plex_library_index' in tables:
                        cur.execute("SELECT COUNT(*) FROM plex_library_index")
                        count = cur.fetchone()[0]
                        print(f"🎵 Tracce nell'indice: {count}")
                        
                        # Mostra prime 5 tracce se esistono
                        if count > 0:
                            cur.execute("SELECT title_clean, artist_clean FROM plex_library_index LIMIT 5")
                            samples = cur.fetchall()
                            print("🎶 Esempi tracce:")
                            for title, artist in samples:
                                print(f"   - '{title}' by '{artist}'")
                    
                    # Conta tracce mancanti
                    if 'missing_tracks' in tables:
                        cur.execute("SELECT COUNT(*) FROM missing_tracks")
                        missing_count = cur.fetchone()[0]
                        print(f"❌ Tracce mancanti: {missing_count}")
                        
            except Exception as e:
                print(f"❌ Errore database: {e}")
        else:
            print(f"❌ Database non trovato: {db_path}")
    
    print("\n=== VERIFICA VARIABILI PATH ===")
    print(f"CWD: {os.getcwd()}")
    print(f"__file__: {__file__}")
    print(f"dirname(__file__): {os.path.dirname(__file__)}")
    
    # Simula il calcolo del path come nel codice originale
    script_dir = os.path.dirname(__file__)
    calculated_path = os.path.join(script_dir, "state_data", "sync_database.db")
    print(f"Path calcolato (come nel codice): {calculated_path}")
    print(f"Esiste il path calcolato? {os.path.exists(calculated_path)}")

if __name__ == "__main__":
    debug_database()