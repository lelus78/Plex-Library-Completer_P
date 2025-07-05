#!/usr/bin/env python3

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "state_data", "sync_database.db")

def get_selected_playlist_ids_debug(user_type: str, service: str):
    """Test version of get_selected_playlist_ids function without dependencies."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Filtra contenuti non sincronizzabili per Deezer
            if service == 'deezer':
                res = cur.execute("""
                    SELECT playlist_id FROM user_playlist_selections 
                    WHERE user_type = ? AND service = ? AND is_selected = 1
                    AND playlist_type NOT IN ('genres', 'radios')
                    AND playlist_id NOT LIKE 'genre_%'
                    AND playlist_id NOT LIKE 'radio_%'
                    AND playlist_id NOT LIKE 'chart_tracks_%'
                    AND playlist_id NOT LIKE 'chart_albums_%'
                    ORDER BY playlist_name
                """, (user_type, service))
            else:
                # Per Spotify, sincronizza tutto normalmente
                res = cur.execute("""
                    SELECT playlist_id FROM user_playlist_selections 
                    WHERE user_type = ? AND service = ? AND is_selected = 1
                    ORDER BY playlist_name
                """, (user_type, service))
            
            playlist_ids = [row[0] for row in res.fetchall()]
            
            # Conta quanti sono stati filtrati per Deezer
            if service == 'deezer':
                res_total = cur.execute("""
                    SELECT COUNT(*) FROM user_playlist_selections 
                    WHERE user_type = ? AND service = ? AND is_selected = 1
                """, (user_type, service))
                total_selected = res_total.fetchone()[0]
                filtered_count = total_selected - len(playlist_ids)
                
                if filtered_count > 0:
                    print(f"🚫 Filtrati {filtered_count} contenuti non sincronizzabili Deezer (generi/radio)")
            
            print(f"📋 Trovate {len(playlist_ids)} playlist sincronizzabili per {user_type}/{service}")
            return playlist_ids
            
    except Exception as e:
        print(f"❌ Errore recuperando playlist ID selezionati: {e}")
        return []

def debug_sync_behavior():
    """Simulate what happens during sync based on current selections."""
    print("🔍 SIMULATING SYNC BEHAVIOR")
    print("="*60)
    
    # Simulate the sync process for each user
    user_configs = [
        {"name": "main user", "type": "main"},
        {"name": "secondary user", "type": "secondary"}
    ]
    
    total_playlists_to_sync = 0
    
    for user_config in user_configs:
        user_type = user_config["type"]
        user_name = user_config["name"]
        
        print(f"\n👤 Processing user: {user_name} ({user_type})")
        print("-" * 40)
        
        # Check Spotify
        spotify_ids = get_selected_playlist_ids_debug(user_type, 'spotify')
        if spotify_ids:
            print(f"🎵 Spotify: {len(spotify_ids)} playlist(s) to sync")
            for pid in spotify_ids:
                print(f"   • {pid}")
            total_playlists_to_sync += len(spotify_ids)
        else:
            print("🎵 Spotify: No playlists selected")
        
        # Check Deezer
        deezer_ids = get_selected_playlist_ids_debug(user_type, 'deezer')
        if deezer_ids:
            print(f"🎶 Deezer: {len(deezer_ids)} playlist(s) to sync")
            for pid in deezer_ids[:5]:  # Show first 5
                print(f"   • {pid}")
            if len(deezer_ids) > 5:
                print(f"   ... and {len(deezer_ids) - 5} more")
            total_playlists_to_sync += len(deezer_ids)
        else:
            print("🎶 Deezer: No playlists selected")
    
    print(f"\n📊 TOTAL PLAYLISTS TO SYNC: {total_playlists_to_sync}")
    
    if total_playlists_to_sync > 1:
        print("⚠️  WARNING: Multiple playlists will be synced!")
        print("   This explains why the user sees 'all playlists' being synced")
        print("   even though they only selected one in the interface.")

if __name__ == "__main__":
    debug_sync_behavior()