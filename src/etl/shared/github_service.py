"""
GitHub integration service for fetching course material metadata
"""
import requests
import logging
from typing import Optional, Dict
import json


# Cache for chapter.json files to avoid repeated API calls
_chapter_cache: Dict[str, dict] = {}

def get_chapter_title(chapter_id: str) -> Optional[str]:
    """
    Fetch chapter title from GitHub repository
    """
    try:
        # Reuse the existing loader 
        chapter_data = _load_chapter_json(chapter_id)
        
        if chapter_data:
            return chapter_data.get('title')
            
        return None
    except Exception as e:
        logging.error(f"Error fetching chapter title: {str(e)}")
        return None
    

def get_material_title(chapter_id: str, material_id: str, course_id: str = "fullstack-2025") -> Optional[str]:
    """
    Fetch material title from GitHub repository
    
    Args:
        chapter_id: e.g. "ch01"
        material_id: e.g. "mat01-01"
        course_id: Course identifier (default: "fullstack-2025")
    
    Returns:
        Material title or None if not found
    """
    try:
        # Load chapter metadata (cached)
        chapter_data = _load_chapter_json(chapter_id)
        
        if not chapter_data:
            logging.warning(f"Chapter {chapter_id} metadata not found")
            return None
        
        # Find material in chapter
        materials = chapter_data.get('materials', [])
        for material in materials:
            if material.get('materialId') == material_id:
                title = material.get('title')
                logging.info(f"Found title for {chapter_id}/{material_id}: {title}")
                return title
        
        logging.warning(f"Material {material_id} not found in chapter {chapter_id}")
        return None
        
    except Exception as e:
        logging.error(f"Error fetching material title: {str(e)}")
        return None


def _load_chapter_json(chapter_id: str) -> Optional[dict]:
    """
    Load chapter.json from GitHub (with caching)
    
    Args:
        chapter_id: e.g. "ch01"
    
    Returns:
        Chapter metadata dict or None
    """
    # Check cache first
    if chapter_id in _chapter_cache:
        logging.info(f"Using cached chapter data for {chapter_id}")
        return _chapter_cache[chapter_id]
    
    try:
        # Map chapter ID to folder name
        # ch01 -> 01-how-internet-works
        # ch02 -> 02-interactivity-ux
        # etc.
        chapter_folder_map = {
            'ch01': '01-how-internet-works',
            'ch02': '02-interactivity-ux',
            'ch03': '03-html-and-css',
            'ch04': '04-version-control-and-hosting',
            'ch05': '05-paid-assignment-portfolio',
            'ch06': '06-building-dynamic-websites',
            'ch07': '07-typescript',
            'ch08': '08-databases',
            'ch09': '09-react',
            'ch10': '10-nodejs',
            'ch11': '11-paid-assignment-fullstack',
        }
        
        folder_name = chapter_folder_map.get(chapter_id)
        
        if not folder_name:
            logging.warning(f"Unknown chapter ID: {chapter_id}")
            return None
        
        # GitHub raw content URL
        url = f"https://raw.githubusercontent.com/BitSavvy-Solutions/AITut-CB-FullStackCourse/dev/chapters/{folder_name}/chapter.json"
        
        logging.info(f"Fetching chapter.json from: {url}")
        
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            chapter_data = response.json()
            
            # Cache the result
            _chapter_cache[chapter_id] = chapter_data
            
            logging.info(f"Successfully loaded chapter.json for {chapter_id}")
            return chapter_data
        else:
            logging.error(f"Failed to fetch chapter.json: {response.status_code}")
            return None
            
    except Exception as e:
        logging.error(f"Error loading chapter.json for {chapter_id}: {str(e)}")
        return None


def clear_cache():
    """Clear the chapter metadata cache"""
    global _chapter_cache
    _chapter_cache = {}
    logging.info("Chapter cache cleared")