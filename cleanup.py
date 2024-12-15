import os
import hashlib
from pathlib import Path
from mutagen import File
from mutagen.easyid3 import EasyID3
from collections import defaultdict
import re
from tqdm import tqdm
from fuzzywuzzy import fuzz
from collections import defaultdict
import argparse

def normalize_string(s):
    """Normalize string for comparison by removing special characters and converting to lowercase"""
    return re.sub(r'[^\w\s]', '', s.lower())

def find_similar_name(name, existing_names, threshold=85):
    """Find the most similar existing name above threshold, or return None"""
    normalized_name = normalize_string(name)
    highest_ratio = 0
    best_match = None
    
    for existing in existing_names:
        normalized_existing = normalize_string(existing)
        ratio = fuzz.ratio(normalized_name, normalized_existing)
        if ratio > highest_ratio and ratio >= threshold:
            highest_ratio = ratio
            best_match = existing
    
    return best_match

def normalize_artist_name(artist):
    """
    Normalize artist name by:
    1. Remove featuring artists (after comma, feat., ft., &, with, etc.)
    2. Convert to lowercase
    3. Remove special characters
    4. Standardize common separators
    """
    # Convert to lowercase
    name = artist.lower()
    
    # List of patterns that indicate featuring artists
    featuring_patterns = [
        r',.*$',                    # Remove everything after a comma
        r'\sfeat\..*$',            # Remove 'feat.' and everything after
        r'\sft\..*$',              # Remove 'ft.' and everything after
        r'\swith\s.*$',            # Remove 'with' and everything after
        r'\sx\s.*$',               # Remove 'x' and everything after
        r'\svs\.?\s.*$',           # Remove 'vs' or 'vs.' and everything after
        r'\s&\s(?!the\s).*$',      # Remove '&' and everything after, except '& the'
    ]
    
    # Apply each pattern
    for pattern in featuring_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)
    
    # Standardize common separators and remove special characters
    name = re.sub(r'[^\w\s&]', '', name)  # Keep & symbol but remove other special chars
    name = re.sub(r'\s+', ' ', name)      # Standardize multiple spaces to single space
    
    # Standardize '& the' format
    name = re.sub(r'\sand\sthe\s', ' & the ', name)
    
    # Strip leading/trailing whitespace
    name = name.strip()
    
    return name

class DirectoryManager:
    def __init__(self):
        self.artist_mappings = {}
        self.album_mappings = defaultdict(dict)
        self.canonical_cases = {}  # Store proper capitalization
        
    def get_canonical_artist(self, artist):
        normalized = normalize_artist_name(artist)
        
        # Check if we already have a mapping
        if normalized in self.artist_mappings:
            return self.artist_mappings[normalized]
        
        # If this is a new normalized name, store the best-looking version
        if normalized not in self.canonical_cases:
            self.canonical_cases[normalized] = artist
            self.artist_mappings[normalized] = artist
        elif len(artist) > len(self.canonical_cases[normalized]):
            # Keep the longer version as it might be more complete
            self.canonical_cases[normalized] = artist
            self.artist_mappings[normalized] = artist
            
        return self.canonical_cases[normalized]
    
    def get_canonical_album(self, artist, album):
        canonical_artist = self.get_canonical_artist(artist)
        normalized_album = normalize_string(album)
        
        if normalized_album in self.album_mappings[canonical_artist]:
            return self.album_mappings[canonical_artist][normalized_album]
        
        similar_album = find_similar_name(album, self.album_mappings[canonical_artist].values())
        if similar_album:
            self.album_mappings[canonical_artist][normalized_album] = similar_album
            return similar_album
        
        self.album_mappings[canonical_artist][normalized_album] = album
        return album

def get_file_hash(filepath, block_size=65536):
    """Calculate SHA-256 hash of a file"""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            sha256_hash.update(block)
    return sha256_hash.hexdigest()

def sanitize_filename(filename):
    """Remove invalid characters from filename"""
    # Remove or replace invalid filename characters
    invalid_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid_chars, '', filename)
    # Remove leading/trailing spaces and periods
    sanitized = sanitized.strip('. ')
    return sanitized

def get_audio_metadata(filepath):
    """Extract metadata from audio file"""
    try:
        if filepath.lower().endswith('.mp3'):
            audio = EasyID3(filepath)
            # Only proceed if we have at least title and artist
            if 'title' not in audio or 'artist' not in audio:
                return None
            return {
                'artist': audio.get('artist', [''])[0],
                'album': audio.get('album', [''])[0],
                'title': audio.get('title', [''])[0],
                'tracknumber': audio.get('tracknumber', [''])[0].split('/')[0].zfill(2)
            }
        else:
            audio = File(filepath)
            if not audio or not audio.tags:
                return None
            
            tags = audio.tags
            # Only proceed if we have at least title and artist
            if 'title' not in tags or 'artist' not in tags:
                return None
                
            tracknumber = tags.get('tracknumber', [''])[0] if 'tracknumber' in tags else ''
            if isinstance(tracknumber, str):
                tracknumber = tracknumber.split('/')[0].zfill(2)
            
            return {
                'artist': tags.get('artist', [''])[0],
                'album': tags.get('album', [''])[0],
                'title': tags.get('title', [''])[0],
                'tracknumber': tracknumber
            }
    except Exception as e:
        return None

def generate_new_filename(metadata, original_path):
    """Generate new filename based on metadata"""
    # Get the file extension from the original file
    ext = os.path.splitext(original_path)[1].lower()
    
    # Get the track number and title
    track_num = metadata['tracknumber']
    title = sanitize_filename(metadata['title'])
    
    # Create the new filename
    if track_num:
        new_filename = f"{track_num} - {title}{ext}"
    else:
        new_filename = f"{title}{ext}"
    
    return new_filename

class MusicFile:
    def __init__(self, path):
        self.path = path
        self.hash = get_file_hash(path)
        self.metadata = get_audio_metadata(path)
        self.size = os.path.getsize(path)
        self.new_filename = generate_new_filename(self.metadata, path)

    def __eq__(self, other):
        return self.hash == other.hash

def count_dirs_and_files(root_dir):
    """Count total number of directories and music files"""
    MUSIC_EXTENSIONS = {'.mp3', '.m4a', '.flac', '.wav'}
    total_dirs = 0
    total_files = 0
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        total_dirs += 1
        total_files += sum(1 for f in filenames if Path(f).suffix.lower() in MUSIC_EXTENSIONS)
    
    return total_dirs, total_files

def create_directory(path):
    """Safely create directory if it doesn't exist"""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        print(f"Error creating directory {path}: {str(e)}")
        return False

def move_and_rename_file(src_path, dest_dir, new_filename):
    """Move and rename a file, handling existing files"""
    try:
        dest_path = os.path.join(dest_dir, new_filename)
        
        # Check if destination exists
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(new_filename)
            counter = 1
            while os.path.exists(dest_path):
                new_filename = f"{base}_{counter}{ext}"
                dest_path = os.path.join(dest_dir, new_filename)
                counter += 1
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        # Move and rename file
        os.rename(src_path, dest_path)
        return True, dest_path
    except Exception as e:
        return False, str(e)

def analyze_music_directory(root_dir):
    proposed_moves = defaultdict(list)
    unique_files = {}
    duplicates = defaultdict(list)
    skipped_files = []
    directory_manager = DirectoryManager()
    
    MUSIC_EXTENSIONS = {'.mp3', '.m4a', '.flac', '.wav'}
    
    print("\nCounting directories and files...")
    total_dirs, total_files = count_dirs_and_files(root_dir)
    print(f"Found {total_dirs} directories and {total_files} music files to process.")
    
    dir_pbar = tqdm(total=total_dirs, desc="Scanning directories", unit="dir")
    file_pbar = tqdm(total=total_files, desc="Processing files", unit="file")
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dir_pbar.update(1)
        
        music_files = [f for f in filenames if Path(f).suffix.lower() in MUSIC_EXTENSIONS]
        
        for filename in music_files:
            full_path = os.path.join(dirpath, filename)
            
            try:
                metadata = get_audio_metadata(full_path)
                
                if metadata is None:
                    skipped_files.append({
                        'path': full_path,
                        'reason': 'Missing required metadata (title/artist)'
                    })
                    file_pbar.update(1)
                    continue
                
                # Get canonical names for artist and album
                canonical_artist = directory_manager.get_canonical_artist(metadata['artist'])
                canonical_album = directory_manager.get_canonical_album(
                    canonical_artist, 
                    metadata['album']
                )
                
                # Update metadata with canonical names
                metadata['artist'] = canonical_artist
                metadata['album'] = canonical_album
                
                music_file = MusicFile(full_path)
                music_file.metadata = metadata  # Update with canonical names
                
                if music_file.hash in unique_files:
                    duplicates[music_file.hash].append(full_path)
                else:
                    unique_files[music_file.hash] = music_file
                    
                    new_path = os.path.join(
                        root_dir,
                        sanitize_filename(canonical_artist),
                        sanitize_filename(canonical_album)
                    )
                    
                    proposed_moves[new_path].append({
                        'original_path': full_path,
                        'current_filename': filename,
                        'new_filename': music_file.new_filename,
                        'metadata': metadata
                    })
                
            except Exception as e:
                skipped_files.append({
                    'path': full_path,
                    'reason': f'Error processing file: {str(e)}'
                })
            
            file_pbar.update(1)
    
    dir_pbar.close()
    file_pbar.close()
    
    return proposed_moves, duplicates, skipped_files

def print_proposed_changes(proposed_moves, duplicates, skipped_files):
    print("\nProposed Directory Structure and File Renaming:")
    print("============================================")
    
    for new_path, files in proposed_moves.items():
        print(f"\nDirectory: {new_path}")
        print("Files to be moved/renamed:")
        for file in files:
            print(f"\n  File: {file['current_filename']}")
            print(f"  → New name: {file['new_filename']}")
            print(f"  From: {file['original_path']}")
            print(f"  Metadata:")
            print(f"    Artist: {file['metadata']['artist']}")
            print(f"    Album: {file['metadata']['album']}")
            print(f"    Title: {file['metadata']['title']}")
            print(f"    Track #: {file['metadata']['tracknumber']}")
        print("-" * 50)
    
    if duplicates:
        print("\nDuplicate Files to be Removed:")
        print("==============================")
        total_duplicates = 0
        total_space = 0
        
        for file_hash, duplicate_paths in duplicates.items():
            total_duplicates += len(duplicate_paths)
            file_size = os.path.getsize(duplicate_paths[0])
            space_saved = file_size * len(duplicate_paths)
            total_space += space_saved
            
            print(f"\nDuplicate group (hash: {file_hash[:8]}...):")
            for path in duplicate_paths:
                print(f"  - {path}")
                print(f"    Size: {file_size / 1024 / 1024:.2f} MB")
            
        print(f"\nTotal duplicate files to be removed: {total_duplicates}")
        print(f"Total space to be freed: {total_space / 1024 / 1024:.2f} MB")
        print("-" * 50)

    if skipped_files:
        print("\nSkipped Files (Missing or Invalid Metadata):")
        print("=========================================")
        for file in skipped_files:
            print(f"\nFile: {file['path']}")
            print(f"Reason: {file['reason']}")
        print(f"\nTotal files skipped: {len(skipped_files)}")
        print("-" * 50)

def execute_changes(proposed_moves, duplicates, root_dir):
    """Execute the proposed changes"""
    print("\nExecuting changes...")
    
    # Create progress bars
    moves_total = sum(len(files) for files in proposed_moves.values())
    duplicates_total = sum(len(dups) for dups in duplicates.values())
    
    move_pbar = tqdm(total=moves_total, desc="Moving/renaming files", unit="file")
    dup_pbar = tqdm(total=duplicates_total, desc="Removing duplicates", unit="file")
    
    # Track results
    results = {
        'successful_moves': 0,
        'failed_moves': 0,
        'successful_deletions': 0,
        'failed_deletions': 0,
        'errors': []
    }
    
    # Process moves first
    for new_path, files in proposed_moves.items():
        # Create destination directory
        if not create_directory(new_path):
            results['errors'].append(f"Failed to create directory: {new_path}")
            continue
            
        for file in files:
            success, result = move_and_rename_file(
                file['original_path'],
                new_path,
                file['new_filename']
            )
            
            if success:
                results['successful_moves'] += 1
            else:
                results['failed_moves'] += 1
                results['errors'].append(
                    f"Failed to move {file['original_path']}: {result}"
                )
            
            move_pbar.update(1)
    
    # Remove duplicates
    for file_hash, duplicate_paths in duplicates.items():
        for dup_path in duplicate_paths:
            try:
                os.remove(dup_path)
                results['successful_deletions'] += 1
            except Exception as e:
                results['failed_deletions'] += 1
                results['errors'].append(f"Failed to delete {dup_path}: {str(e)}")
            dup_pbar.update(1)
    
    move_pbar.close()
    dup_pbar.close()
    
    return results

def cleanup_empty_directories(root_dir):
    """
    Delete empty directories recursively from bottom up
    Returns count of removed directories
    """
    print("\nCleaning up empty directories...")
    removed_count = 0
    
    # Use a progress bar for directories being checked
    dirs_to_check = []
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        for dirname in dirnames:
            dirs_to_check.append(os.path.join(dirpath, dirname))
    
    if not dirs_to_check:
        return 0
        
    with tqdm(total=len(dirs_to_check), desc="Checking directories", unit="dir") as pbar:
        # Walk bottom up so we can remove empty dirs
        for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
            for dirname in dirnames:
                full_path = os.path.join(dirpath, dirname)
                try:
                    # Check if directory is empty
                    if not os.listdir(full_path):
                        os.rmdir(full_path)
                        removed_count += 1
                except Exception as e:
                    print(f"Error removing directory {full_path}: {str(e)}")
                pbar.update(1)
    
    print(f"\nRemoved {removed_count} empty directories")
    return removed_count

def main():
    # Set up command line argument parser
    parser = argparse.ArgumentParser(
        description='Music library cleanup and organization tool'
    )
    parser.add_argument(
        'directory',
        help='Path to the music directory to process'
    )
    parser.add_argument(
        '--mode',
        choices=['demo', 'execute'],
        required=True,
        help='Operation mode: "demo" to show proposed changes, "execute" to perform changes'
    )
    
    # Parse arguments
    args = parser.parse_args()
    
    input_dir = args.directory
    
    if not os.path.exists(input_dir):
        print("Error: Directory does not exist!")
        return
    
    print("\nInitializing analysis...")
    proposed_moves, duplicates, skipped_files = analyze_music_directory(input_dir)
    
    if not (proposed_moves or duplicates or skipped_files):
        print("\nNo music files found or no changes needed.")
        return
    
    # Always show the proposed changes first
    print("\nGenerating report...")
    print_proposed_changes(proposed_moves, duplicates, skipped_files)
    
    if args.mode == 'demo':
        print("\nDEMO MODE - No changes were made.")
        print("\nTo execute these changes, run again with --mode execute")
    
    else:  # execute mode
        # Execute changes and get results
        results = execute_changes(proposed_moves, duplicates, input_dir)
        
        # Print results
        print("\nOperation Complete!")
        print("\nResults:")
        print(f"Successfully moved/renamed: {results['successful_moves']} files")
        print(f"Failed moves/renames: {results['failed_moves']} files")
        print(f"Successfully removed duplicates: {results['successful_deletions']} files")
        print(f"Failed deletions: {results['failed_deletions']} files")
        
        if results['errors']:
            print("\nErrors encountered:")
            for error in results['errors']:
                print(f"- {error}")
        
        print("\nNote: Files with missing metadata were skipped (see report above)")
        
        # Clean up empty directories
        print("\nPerforming final cleanup...")
        removed_dirs = cleanup_empty_directories(input_dir)
        if removed_dirs > 0:
            print(f"Cleanup complete: removed {removed_dirs} empty directories")
        else:
            print("No empty directories to clean up")


if __name__ == "__main__":
    main()