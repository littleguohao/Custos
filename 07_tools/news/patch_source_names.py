# -*- coding: utf-8 -*-
"""Patch source_name in existing normalized and filtered RSS JSON files."""
from __future__ import annotations
import json
from pathlib import Path
import sys

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_name_overrides import fix_source_name

BASE = Path(__file__).resolve().parents[2]
DATA = BASE / '01_data' / 'news' / 'rss'

def patch_file(path: Path) -> int:
    """Patch source_name in a JSON file. Returns count of items fixed."""
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return 0
    
    if not isinstance(data, list):
        return 0
    
    fixed = 0
    for item in data:
        sid = item.get('source_id', '')
        sname = item.get('source_name', '')
        corrected = fix_source_name(sid, sname)
        if corrected != sname:
            item['source_name'] = corrected
            fixed += 1
    
    if fixed > 0:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    
    return fixed

def main():
    total_fixed = 0
    files_fixed = 0
    
    # Patch normalized files
    norm_dir = DATA / 'normalized'
    if norm_dir.exists():
        for f in sorted(norm_dir.glob('*.json')):
            n = patch_file(f)
            if n > 0:
                print(f'  normalized/{f.name}: {n} items fixed')
                total_fixed += n
                files_fixed += 1
    
    # Patch filtered files
    filt_dir = DATA / 'filtered'
    if filt_dir.exists():
        for f in sorted(filt_dir.glob('*.json')):
            n = patch_file(f)
            if n > 0:
                print(f'  filtered/{f.name}: {n} items fixed')
                total_fixed += n
                files_fixed += 1
    
    # Patch postclose news digest if it contains source references
    postclose_dir = BASE / '01_data' / 'news' / 'postclose'
    if postclose_dir.exists():
        for f in sorted(postclose_dir.glob('*.json')):
            n = patch_file(f)
            if n > 0:
                print(f'  postclose/{f.name}: {n} items fixed')
                total_fixed += n
                files_fixed += 1
    
    print(f'\nTotal: {total_fixed} items fixed in {files_fixed} files')

if __name__ == '__main__':
    main()
