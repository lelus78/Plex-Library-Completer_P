#!/usr/bin/env python3
"""Debug specifico per verificare l'indicizzazione di 'Molly Grace'"""

import sys
import os
import sqlite3
sys.path.append('.')

# Trova il path del database
DB_PATH = "/mnt/e/Docker image/Plex-Library-Completer/state_data/sync_database.db"

def debug_molly_grace():
    """Debug per verificare se 'Molly Grace' è nel database"""
    
    print("=== DEBUG MOLLY GRACE IN DATABASE ===")
    print(f"Database path: {DB_PATH}")
    print(f"Database exists: {os.path.exists(DB_PATH)}")
    
    if not os.path.exists(DB_PATH):
        print("❌ Database non esiste!")
        return
    
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Verifica schema tabella
            cur.execute("PRAGMA table_info(plex_library_index)")
            columns = cur.fetchall()
            print(f"\n📋 Schema tabella plex_library_index:")
            for col in columns:
                print(f"   {col}")
            
            # Conta totale tracce
            cur.execute("SELECT COUNT(*) FROM plex_library_index")
            total_tracks = cur.fetchone()[0]
            print(f"\n📊 Totale tracce nel database: {total_tracks}")
            
            # Cerca tracce con "molly" nel nome artista
            print("\n🔍 Cercando tracce con 'molly' nell'artista...")
            cur.execute("SELECT title_clean, artist_clean, album_clean FROM plex_library_index WHERE artist_clean LIKE '%molly%'")
            molly_artists = cur.fetchall()
            
            if molly_artists:
                print(f"✅ Trovate {len(molly_artists)} tracce con 'molly' nell'artista:")
                for i, (title, artist, album) in enumerate(molly_artists[:10]):  # Mostra prime 10
                    print(f"   {i+1}. Title: '{title}' | Artist: '{artist}' | Album: '{album}'")
                if len(molly_artists) > 10:
                    print(f"   ... e altre {len(molly_artists) - 10} tracce")
            else:
                print("❌ Nessuna traccia trovata con 'molly' nell'artista")
            
            # Cerca tracce con "grace" nel nome artista
            print("\n🔍 Cercando tracce con 'grace' nell'artista...")
            cur.execute("SELECT title_clean, artist_clean, album_clean FROM plex_library_index WHERE artist_clean LIKE '%grace%'")
            grace_artists = cur.fetchall()
            
            if grace_artists:
                print(f"✅ Trovate {len(grace_artists)} tracce con 'grace' nell'artista:")
                for i, (title, artist, album) in enumerate(grace_artists[:10]):  # Mostra prime 10
                    print(f"   {i+1}. Title: '{title}' | Artist: '{artist}' | Album: '{album}'")
                if len(grace_artists) > 10:
                    print(f"   ... e altre {len(grace_artists) - 10} tracce")
            else:
                print("❌ Nessuna traccia trovata con 'grace' nell'artista")
            
            # Cerca specificamente "molly grace"
            print("\n🎯 Cercando specificamente 'molly grace'...")
            cur.execute("SELECT title_clean, artist_clean, album_clean FROM plex_library_index WHERE artist_clean = 'molly grace'")
            molly_grace_exact = cur.fetchall()
            
            if molly_grace_exact:
                print(f"✅ Trovate {len(molly_grace_exact)} tracce con artista esatto 'molly grace':")
                for i, (title, artist, album) in enumerate(molly_grace_exact):
                    print(f"   {i+1}. Title: '{title}' | Artist: '{artist}' | Album: '{album}'")
            else:
                print("❌ Nessuna traccia trovata con artista esatto 'molly grace'")
            
            # Cerca tracce con "molly" e "grace" nell'artista
            print("\n🔍 Cercando tracce con 'molly' E 'grace' nell'artista...")
            cur.execute("SELECT title_clean, artist_clean, album_clean FROM plex_library_index WHERE artist_clean LIKE '%molly%' AND artist_clean LIKE '%grace%'")
            molly_grace_both = cur.fetchall()
            
            if molly_grace_both:
                print(f"✅ Trovate {len(molly_grace_both)} tracce con 'molly' E 'grace' nell'artista:")
                for i, (title, artist, album) in enumerate(molly_grace_both):
                    print(f"   {i+1}. Title: '{title}' | Artist: '{artist}' | Album: '{album}'")
            else:
                print("❌ Nessuna traccia trovata con 'molly' E 'grace' nell'artista")
            
            # Cerca tracce con "molly" o "grace" nel titolo
            print("\n🔍 Cercando tracce con 'molly' o 'grace' nel titolo...")
            cur.execute("SELECT title_clean, artist_clean, album_clean FROM plex_library_index WHERE title_clean LIKE '%molly%' OR title_clean LIKE '%grace%'")
            molly_grace_title = cur.fetchall()
            
            if molly_grace_title:
                print(f"✅ Trovate {len(molly_grace_title)} tracce con 'molly' o 'grace' nel titolo:")
                for i, (title, artist, album) in enumerate(molly_grace_title[:10]):  # Mostra prime 10
                    print(f"   {i+1}. Title: '{title}' | Artist: '{artist}' | Album: '{album}'")
                if len(molly_grace_title) > 10:
                    print(f"   ... e altre {len(molly_grace_title) - 10} tracce")
            else:
                print("❌ Nessuna traccia trovata con 'molly' o 'grace' nel titolo")
            
            # Cerca artisti simili (case-insensitive)
            print("\n🔍 Cercando artisti che contengono 'grace' (case-insensitive)...")
            cur.execute("SELECT DISTINCT artist_clean FROM plex_library_index WHERE LOWER(artist_clean) LIKE '%grace%' ORDER BY artist_clean")
            grace_artists_distinct = cur.fetchall()
            
            if grace_artists_distinct:
                print(f"✅ Trovati {len(grace_artists_distinct)} artisti con 'grace' nel nome:")
                for i, (artist,) in enumerate(grace_artists_distinct[:20]):  # Mostra primi 20
                    print(f"   {i+1}. '{artist}'")
                if len(grace_artists_distinct) > 20:
                    print(f"   ... e altri {len(grace_artists_distinct) - 20} artisti")
            else:
                print("❌ Nessun artista trovato con 'grace' nel nome")
            
            # Cerca artisti che contengono "molly"
            print("\n🔍 Cercando artisti che contengono 'molly' (case-insensitive)...")
            cur.execute("SELECT DISTINCT artist_clean FROM plex_library_index WHERE LOWER(artist_clean) LIKE '%molly%' ORDER BY artist_clean")
            molly_artists_distinct = cur.fetchall()
            
            if molly_artists_distinct:
                print(f"✅ Trovati {len(molly_artists_distinct)} artisti con 'molly' nel nome:")
                for i, (artist,) in enumerate(molly_artists_distinct[:20]):  # Mostra primi 20
                    print(f"   {i+1}. '{artist}'")
                if len(molly_artists_distinct) > 20:
                    print(f"   ... e altri {len(molly_artists_distinct) - 20} artisti")
            else:
                print("❌ Nessun artista trovato con 'molly' nel nome")
            
            # Cerca tracce con problemi di indicizzazione (campi vuoti)
            print("\n🔍 Cercando tracce con problemi di indicizzazione...")
            cur.execute("SELECT COUNT(*) FROM plex_library_index WHERE artist_clean = '' OR artist_clean IS NULL")
            empty_artists = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM plex_library_index WHERE title_clean = '' OR title_clean IS NULL")
            empty_titles = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM plex_library_index WHERE (artist_clean = '' OR artist_clean IS NULL) AND (title_clean = '' OR title_clean IS NULL)")
            empty_both = cur.fetchone()[0]
            
            print(f"📊 Statistiche campi vuoti:")
            print(f"   - Artisti vuoti: {empty_artists}")
            print(f"   - Titoli vuoti: {empty_titles}")
            print(f"   - Entrambi vuoti: {empty_both}")
            
            # Esempi di tracce problematiche
            if empty_artists > 0:
                print(f"\n🔍 Esempi di tracce con artista vuoto:")
                cur.execute("SELECT title_clean, artist_clean, album_clean FROM plex_library_index WHERE artist_clean = '' OR artist_clean IS NULL LIMIT 5")
                empty_artist_examples = cur.fetchall()
                for i, (title, artist, album) in enumerate(empty_artist_examples):
                    print(f"   {i+1}. Title: '{title}' | Artist: '{artist}' | Album: '{album}'")
                    
    except Exception as e:
        print(f"❌ Errore durante il debug: {e}")
        import traceback
        traceback.print_exc()

def test_case_sensitivity():
    """Test per verificare la case sensitivity"""
    print("\n=== TEST CASE SENSITIVITY ===")
    
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Test con diverse varianti di case
            test_cases = [
                "molly grace",
                "Molly Grace", 
                "MOLLY GRACE",
                "molly GRACE",
                "MOLLY grace"
            ]
            
            for test_case in test_cases:
                cur.execute("SELECT COUNT(*) FROM plex_library_index WHERE artist_clean = ?", (test_case,))
                count = cur.fetchone()[0]
                print(f"   '{test_case}' -> {count} tracce")
                
    except Exception as e:
        print(f"❌ Errore durante il test case sensitivity: {e}")

if __name__ == "__main__":
    debug_molly_grace()
    test_case_sensitivity()