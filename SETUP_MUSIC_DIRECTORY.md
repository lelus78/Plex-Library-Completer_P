# 🎵 Music Directory Setup

## Quick Setup

To configure where your music downloads are saved, you only need to change **TWO THINGS**:

### 1. Set the download path in `.env`:
```bash
MUSIC_DOWNLOAD_PATH="/music"
```

### 2. Map your music library in `docker-compose.yml`:

#### Windows:
```yaml
volumes:
  - M:/organizzata:/music  # Replace M:/organizzata with your music folder
```

#### Linux:
```yaml
volumes:
  - /home/user/music:/music  # Replace with your music folder
```

#### macOS:
```yaml
volumes:
  - /Users/username/Music:/music  # Replace with your music folder
```

#### NAS (Synology/QNAP):
```yaml
volumes:
  - /volume1/music:/music  # Replace with your NAS music folder
```

## Examples

### Example 1: Windows User
- Music library: `D:\My Music\Library`
- `.env`: `MUSIC_DOWNLOAD_PATH="/music"`
- `docker-compose.yml`: `- D:/My Music/Library:/music`

### Example 2: Linux User  
- Music library: `/home/john/Music`
- `.env`: `MUSIC_DOWNLOAD_PATH="/music"`
- `docker-compose.yml`: `- /home/john/Music:/music`

### Example 3: Synology NAS User
- Music library: `/volume1/music`
- `.env`: `MUSIC_DOWNLOAD_PATH="/music"`
- `docker-compose.yml`: `- /volume1/music:/music`

## How It Works

1. **MUSIC_DOWNLOAD_PATH** tells the app where to save files inside the container
2. **Volume mount** maps that container path to your real music library
3. **Everything else is automatic** - no need to edit multiple config files!

## Advanced Users

If you want to customize the download structure or use a different internal path, you can:
- Change `MUSIC_DOWNLOAD_PATH` to any path you want (e.g., `/my/custom/path`)
- Update the volume mount accordingly (e.g., `- /your/library:/my/custom/path`)
- The app will automatically configure streamrip and all other components

That's it! 🎉