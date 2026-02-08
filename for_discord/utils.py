"""
utils.py - Utility functions for hashing, IO, logging, and image validation.
"""

import hashlib
import json
import logging
import os
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

# ---- Shared statistical helpers (used by 8+ feature modules) ----

try:
    from jit_utils import fast_skewness as _jit_skew, fast_kurtosis as _jit_kurt, fast_entropy as _jit_entropy, fast_corrcoef as _jit_corrcoef
    _HAS_JIT = True
except ImportError:
    _HAS_JIT = False


def fast_skew(data) -> float:
    """Fast skewness — JIT-accelerated when available, pure-numpy fallback."""
    if _HAS_JIT:
        return float(_jit_skew(np.asarray(data, dtype=np.float64).ravel()))
    data = np.asarray(data).ravel()
    n = len(data)
    if n < 3:
        return 0.0
    mean = np.mean(data)
    m2 = np.mean((data - mean) ** 2)
    m3 = np.mean((data - mean) ** 3)
    if m2 < 1e-10:
        return 0.0
    return float(m3 / (m2 ** 1.5))


def fast_kurt(data) -> float:
    """Fast excess kurtosis — JIT-accelerated when available, pure-numpy fallback."""
    if _HAS_JIT:
        return float(_jit_kurt(np.asarray(data, dtype=np.float64).ravel()))
    data = np.asarray(data).ravel()
    n = len(data)
    if n < 4:
        return 0.0
    mean = np.mean(data)
    m2 = np.mean((data - mean) ** 2)
    m4 = np.mean((data - mean) ** 4)
    if m2 < 1e-10:
        return 0.0
    return float(m4 / (m2 ** 2) - 3.0)


def fast_entropy_hist(hist) -> float:
    """Fast histogram entropy in bits — JIT-accelerated when available."""
    if _HAS_JIT:
        return float(_jit_entropy(hist))
    return float(-np.sum(hist * np.log2(hist + 1e-10)))


def fast_corrcoef(a, b) -> float:
    """Fast pairwise Pearson correlation — JIT-accelerated when available."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2 or b.size < 2:
        return 0.0
    if _HAS_JIT:
        return float(_jit_corrcoef(a, b))
    r = np.corrcoef(a, b)[0, 1]
    return float(r) if not np.isnan(r) else 0.0

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def compute_sha256(data: bytes) -> str:
    """Compute SHA256 hash of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_phash(image: Image.Image, hash_size: int = 16) -> str:
    """
    Compute perceptual hash (pHash) of an image.
    Uses DCT-based approach for robustness to minor modifications.
    """
    # Convert to grayscale and resize
    img = image.convert('L').resize((hash_size * 4, hash_size * 4), Image.Resampling.LANCZOS)
    pixels = np.array(img, dtype=np.float64)
    
    # Compute DCT (simplified version using FFT)
    # Apply 2D DCT via FFT
    dct = np.fft.fft2(pixels)
    dct_low = dct[:hash_size, :hash_size]
    
    # Use magnitude and take low frequencies
    dct_mag = np.abs(dct_low)
    
    # Compute median (excluding DC component)
    dct_flat = dct_mag.flatten()
    median_val = np.median(dct_flat[1:])  # Exclude DC
    
    # Generate hash bits
    hash_bits = (dct_flat > median_val).astype(int)
    
    # Convert to hex string
    hash_int = 0
    for bit in hash_bits:
        hash_int = (hash_int << 1) | int(bit)
    
    return format(hash_int, f'0{hash_size * hash_size // 4}x')


def compute_dhash(image: Image.Image, hash_size: int = 16) -> str:
    """
    Compute difference hash (dHash) of an image.
    More robust to gamma/color changes than average hash.
    """
    # Resize to (hash_size+1, hash_size) for horizontal gradient
    img = image.convert('L').resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = np.array(img)
    
    # Compute horizontal gradient (difference between adjacent pixels)
    diff = pixels[:, 1:] > pixels[:, :-1]
    
    # Convert to hash
    hash_int = 0
    for bit in diff.flatten():
        hash_int = (hash_int << 1) | int(bit)
    
    return format(hash_int, f'0{hash_size * hash_size // 4}x')


def hamming_distance(hash1: str, hash2: str) -> int:
    """Compute Hamming distance between two hex hash strings."""
    if len(hash1) != len(hash2):
        return float('inf')
    
    val1 = int(hash1, 16)
    val2 = int(hash2, 16)
    xor = val1 ^ val2
    
    # Count set bits
    distance = 0
    while xor:
        distance += xor & 1
        xor >>= 1
    
    return distance


def are_perceptually_similar(hash1: str, hash2: str, threshold: int = 10) -> bool:
    """Check if two perceptual hashes are similar within threshold."""
    return hamming_distance(hash1, hash2) <= threshold


def validate_image(image_bytes: bytes, min_size: int = 256, max_aspect_ratio: float = 4.0) -> Tuple[bool, str, Optional[Image.Image]]:
    """
    Validate image bytes and return (is_valid, reason, PIL.Image or None).
    """
    try:
        from io import BytesIO
        img = Image.open(BytesIO(image_bytes))
        img.load()  # Force decode
        
        # Check format
        if img.format not in ['JPEG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF']:
            return False, f"Unsupported format: {img.format}", None
        
        # Check dimensions
        width, height = img.size
        if width < min_size or height < min_size:
            return False, f"Too small: {width}x{height} < {min_size}", None
        
        # Check aspect ratio
        aspect = max(width, height) / max(min(width, height), 1)
        if aspect > max_aspect_ratio:
            return False, f"Extreme aspect ratio: {aspect:.2f} > {max_aspect_ratio}", None
        
        # Check if image is mostly uniform (likely placeholder)
        if img.mode in ['RGB', 'RGBA', 'L']:
            arr = np.array(img.convert('L'))
            if np.std(arr) < 5:
                return False, "Image appears to be uniform/placeholder", None
        
        return True, "OK", img
        
    except Exception as e:
        return False, f"Decode error: {str(e)}", None


def save_metadata(meta_dir: Path, image_id: str, metadata: Dict[str, Any]) -> None:
    """Save metadata JSON for an image."""
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{image_id}.json"
    
    # Add timestamp
    metadata['saved_at'] = datetime.utcnow().isoformat()
    
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def load_metadata(meta_path: Path) -> Optional[Dict[str, Any]]:
    """Load metadata JSON from path."""
    if not meta_path.exists():
        return None
    
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def generate_image_id(sha256: str, source_name: str) -> str:
    """Generate a unique image ID from hash and source."""
    return f"{source_name}_{sha256[:16]}"


def setup_dataset_structure(base_dir: Path) -> Dict[str, Path]:
    """Create dataset directory structure."""
    dirs = {
        'ai': base_dir / 'ai',
        'real': base_dir / 'real',
        'meta': base_dir / 'meta',
        'splits': base_dir / 'splits',
        'rejected': base_dir / 'rejected',
    }
    
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    
    return dirs


def get_split_files(splits_dir: Path, split_name: str) -> List[str]:
    """Load image IDs from a split file."""
    split_file = splits_dir / f"{split_name}.txt"
    if not split_file.exists():
        return []
    
    with open(split_file, 'r') as f:
        return [line.strip() for line in f if line.strip()]


def save_split_files(splits_dir: Path, splits: Dict[str, List[str]]) -> None:
    """Save split files (train.txt, val.txt, test.txt)."""
    splits_dir.mkdir(parents=True, exist_ok=True)
    
    for split_name, image_ids in splits.items():
        split_file = splits_dir / f"{split_name}.txt"
        with open(split_file, 'w') as f:
            f.write('\n'.join(image_ids))


def create_disjoint_splits(
    image_ids: List[str],
    phashes: Dict[str, str],
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    similarity_threshold: int = 10,
    seed: int = 42
) -> Dict[str, List[str]]:
    """
    Create train/val/test splits ensuring no near-duplicates leak across splits.
    Uses perceptual hashes to detect similar images.
    """
    np.random.seed(seed)
    
    # Group similar images together
    groups = []
    assigned = set()
    
    for img_id in image_ids:
        if img_id in assigned:
            continue
        
        group = [img_id]
        assigned.add(img_id)
        
        phash1 = phashes.get(img_id, '')
        if phash1:
            for other_id in image_ids:
                if other_id in assigned:
                    continue
                phash2 = phashes.get(other_id, '')
                if phash2 and are_perceptually_similar(phash1, phash2, similarity_threshold):
                    group.append(other_id)
                    assigned.add(other_id)
        
        groups.append(group)
    
    # Shuffle groups
    np.random.shuffle(groups)
    
    # Assign groups to splits
    n_groups = len(groups)
    train_end = int(n_groups * train_ratio)
    val_end = train_end + int(n_groups * val_ratio)
    
    train_groups = groups[:train_end]
    val_groups = groups[train_end:val_end]
    test_groups = groups[val_end:]
    
    return {
        'train': [img_id for group in train_groups for img_id in group],
        'val': [img_id for group in val_groups for img_id in group],
        'test': [img_id for group in test_groups for img_id in group],
    }


def load_image_for_inference(path_or_url: str) -> Tuple[Optional[Image.Image], str]:
    """
    Load an image from local path or URL for inference.
    Returns (image, error_message).
    """
    import requests
    from io import BytesIO
    
    try:
        if path_or_url.startswith('http://') or path_or_url.startswith('https://'):
            # URL
            response = requests.get(path_or_url, timeout=30, headers={
                'User-Agent': 'AIChecker/1.0 (https://github.com/yourusername/aichecker; contact@example.com) Python-requests'
            })
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
        else:
            # Local path
            img = Image.open(path_or_url)
        
        img.load()
        
        # Convert to RGB if necessary
        if img.mode not in ['RGB', 'L']:
            img = img.convert('RGB')
        
        return img, ""
        
    except Exception as e:
        return None, str(e)


def ensure_rgb(image: Image.Image) -> Image.Image:
    """Ensure image is in RGB mode."""
    if image.mode == 'RGBA':
        # Create white background
        background = Image.new('RGB', image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        return background
    elif image.mode != 'RGB':
        return image.convert('RGB')
    return image


class ProgressTracker:
    """Track download/processing progress."""
    
    def __init__(self, total: int, desc: str = "Processing"):
        self.total = total
        self.current = 0
        self.desc = desc
        self.success = 0
        self.failed = 0
        self.skipped = 0
    
    def update(self, status: str = 'success'):
        """Update progress with status."""
        self.current += 1
        if status == 'success':
            self.success += 1
        elif status == 'failed':
            self.failed += 1
        elif status == 'skipped':
            self.skipped += 1
    
    def get_summary(self) -> Dict[str, int]:
        """Get progress summary."""
        return {
            'total': self.total,
            'processed': self.current,
            'success': self.success,
            'failed': self.failed,
            'skipped': self.skipped,
        }


def save_manifest(base_dir: Path, stats: Dict[str, Any]) -> None:
    """Save dataset manifest with statistics."""
    manifest_path = base_dir / 'manifest.json'
    
    stats['generated_at'] = datetime.utcnow().isoformat()
    
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2)
    
    logger.info(f"Manifest saved to {manifest_path}")
