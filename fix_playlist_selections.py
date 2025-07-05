#!/usr/bin/env python3

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "state_data", "sync_database.db")

def fix_secondary_user_selections():
    """Deselect all playlists for secondary user to fix the multi-playlist sync issue."""
    
    print("🔧 FIXING PLAYLIST SELECTION ISSUE")
    print("="*60)
    
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            # Count currently selected playlists for secondary user
            cur.execute("""
                SELECT COUNT(*) FROM user_playlist_selections 
                WHERE user_type = 'secondary' AND is_selected = 1
            """)
            secondary_selected_count = cur.fetchone()[0]
            
            print(f"📊 Secondary user currently has {secondary_selected_count} playlists selected")
            
            if secondary_selected_count > 0:
                # Show what will be deselected
                cur.execute("""
                    SELECT service, playlist_id, playlist_name 
                    FROM user_playlist_selections 
                    WHERE user_type = 'secondary' AND is_selected = 1
                    ORDER BY service, playlist_name
                """)
                
                playlists_to_deselect = cur.fetchall()
                print(f"\n📋 Playlists that will be deselected:")
                for service, pid, name in playlists_to_deselect:
                    print(f"   • {service}: {name} ({pid})")
                
                # Ask for confirmation
                print(f"\n⚠️  This will deselect {len(playlists_to_deselect)} playlists for the secondary user.")
                print("   Only the main user's selections will remain active.")
                
                confirm = input("\n❓ Continue? (y/N): ").lower().strip()
                
                if confirm == 'y':
                    # Deselect all secondary user playlists
                    cur.execute("""
                        UPDATE user_playlist_selections 
                        SET is_selected = 0, last_updated = CURRENT_TIMESTAMP
                        WHERE user_type = 'secondary' AND is_selected = 1
                    """)
                    
                    updated_count = cur.rowcount
                    con.commit()
                    
                    print(f"✅ Successfully deselected {updated_count} playlists for secondary user")
                    
                    # Verify the fix
                    print("\n🔍 VERIFICATION - Playlists that will now sync:")
                    verify_selections()
                    
                else:
                    print("❌ Operation cancelled")
            else:
                print("✅ Secondary user has no playlists selected - no fix needed")
                
                # Still show current state
                print("\n🔍 Current selections:")
                verify_selections()
        
    except Exception as e:
        print(f"❌ Error fixing selections: {e}")

def verify_selections():
    """Show current playlist selections after fix."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            
            total_selected = 0
            
            for user_type in ['main', 'secondary']:
                for service in ['spotify', 'deezer']:
                    # Use the same filtering logic as the actual sync
                    if service == 'deezer':
                        res = cur.execute("""
                            SELECT COUNT(*) FROM user_playlist_selections 
                            WHERE user_type = ? AND service = ? AND is_selected = 1
                            AND playlist_type NOT IN ('genres', 'radios')
                            AND playlist_id NOT LIKE 'genre_%'
                            AND playlist_id NOT LIKE 'radio_%'
                            AND playlist_id NOT LIKE 'chart_tracks_%'
                            AND playlist_id NOT LIKE 'chart_albums_%'
                        """, (user_type, service))
                    else:
                        res = cur.execute("""
                            SELECT COUNT(*) FROM user_playlist_selections 
                            WHERE user_type = ? AND service = ? AND is_selected = 1
                        """, (user_type, service))
                    
                    count = res.fetchone()[0]
                    if count > 0:
                        print(f"   {user_type}/{service}: {count} playlist(s)")
                        total_selected += count
            
            print(f"\n📊 TOTAL PLAYLISTS TO SYNC: {total_selected}")
            
            if total_selected == 1:
                print("✅ Perfect! Only 1 playlist will be synced as expected.")
            elif total_selected == 0:
                print("⚠️  No playlists selected - sync will do nothing.")
            else:
                print(f"⚠️  Still {total_selected} playlists will be synced.")
                
    except Exception as e:
        print(f"❌ Error verifying selections: {e}")

def show_alternative_solutions():
    """Show other ways to solve the issue."""
    print("\n" + "="*60)
    print("🛠️  ALTERNATIVE SOLUTIONS")
    print("="*60)
    print("1. Use the web interface:")
    print("   • Go to http://localhost:5000/playlist_management")
    print("   • Switch to 'secondary' user tab")
    print("   • Deselect all unwanted playlists")
    print()
    print("2. Disable secondary user sync:")
    print("   • Set PLEX_TOKEN_USERS='' in .env file")
    print("   • Only main user will sync")
    print()
    print("3. Use selective sync:")
    print("   • In web interface, disable Deezer sync")
    print("   • Only Spotify will sync (1 playlist)")

if __name__ == "__main__":
    fix_secondary_user_selections()
    show_alternative_solutions()