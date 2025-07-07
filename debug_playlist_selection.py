#!/usr/bin/env python3

import sqlite3
import os
import json

# Path to the database
DB_PATH = os.path.join(os.path.dirname(__file__), "state_data", "sync_database.db")

def check_playlist_selections():
    """Debug the playlist selection database to understand what's actually selected."""
    
    print(f"🔍 Checking database at: {DB_PATH}")
    print(f"📁 Database exists: {os.path.exists(DB_PATH)}")
    
    if not os.path.exists(DB_PATH):
        print("❌ Database not found!")
        return
    
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            
            # Check if the table exists
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_playlist_selections'")
            if not cur.fetchone():
                print("❌ Table 'user_playlist_selections' not found!")
                return
            
            print("\n" + "="*80)
            print("📋 ALL PLAYLIST SELECTIONS IN DATABASE")
            print("="*80)
            
            # Get all playlist selections
            cur.execute("""
                SELECT user_type, service, playlist_id, playlist_name, playlist_type, 
                       is_selected, track_count, last_updated 
                FROM user_playlist_selections 
                ORDER BY user_type, service, playlist_name
            """)
            
            all_selections = cur.fetchall()
            
            if not all_selections:
                print("❌ No playlist selections found in database!")
                return
                
            print(f"📊 Total playlist records: {len(all_selections)}")
            
            # Group by user/service
            for user_type in ['main', 'secondary']:
                for service in ['spotify', 'deezer']:
                    user_service_playlists = [
                        row for row in all_selections 
                        if row['user_type'] == user_type and row['service'] == service
                    ]
                    
                    if user_service_playlists:
                        print(f"\n🎵 {user_type.upper()} / {service.upper()}")
                        print("-" * 40)
                        
                        selected_count = sum(1 for row in user_service_playlists if row['is_selected'])
                        total_count = len(user_service_playlists)
                        
                        print(f"📈 Selected: {selected_count} / {total_count}")
                        
                        # Show selected playlists
                        selected_playlists = [row for row in user_service_playlists if row['is_selected']]
                        if selected_playlists:
                            print("✅ SELECTED PLAYLISTS:")
                            for row in selected_playlists:
                                print(f"   • {row['playlist_id']}: {row['playlist_name']} ({row['playlist_type']}, {row['track_count']} tracks)")
                        
                        # Show first few unselected for context
                        unselected_playlists = [row for row in user_service_playlists if not row['is_selected']]
                        if unselected_playlists:
                            print(f"❌ UNSELECTED PLAYLISTS (showing first 3 of {len(unselected_playlists)}):")
                            for row in unselected_playlists[:3]:
                                print(f"   • {row['playlist_id']}: {row['playlist_name']} ({row['playlist_type']}, {row['track_count']} tracks)")
            
            # Test the get_selected_playlist_ids function directly
            print("\n" + "="*80)
            print("🔍 TESTING get_selected_playlist_ids() FUNCTION")
            print("="*80)
            
            # Import and test the function
            import sys
            sys.path.append(os.path.dirname(__file__))
            from plex_playlist_sync.utils.database import get_selected_playlist_ids
            
            for user_type in ['main', 'secondary']:
                for service in ['spotify', 'deezer']:
                    selected_ids = get_selected_playlist_ids(user_type, service)
                    print(f"{user_type}/{service}: {len(selected_ids)} selected IDs")
                    if selected_ids:
                        print(f"   IDs: {selected_ids}")
                    else:
                        print("   No IDs selected")
            
    except Exception as e:
        print(f"❌ Error checking database: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_playlist_selections()