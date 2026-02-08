"""
features.py - Main feature extraction module combining all feature families.

This module provides the unified interface for extracting all pixel-level
features used for AI vs Real image classification.
"""

import os

# Force single-threaded execution for all numerical libraries
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['VECLIB_MAXIMUM_THREADS'] = '1'
os.environ['NUMEXPR_NUM_THREADS'] = '1'
os.environ['NUMBA_NUM_THREADS'] = '1'
os.environ['NUMBA_THREADING_LAYER'] = 'workqueue'

import warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)
warnings.filterwarnings('ignore', category=UserWarning, module='skimage')

import hashlib
import json
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from PIL import Image
import time
import logging
import threading

# Limit OpenCV threads
try:
    import cv2
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
except ImportError:
    pass

from features_color import extract_color_features
from features_frequency import extract_frequency_features
from features_spectral_diffusion import extract_spectral_diffusion_features
from features_noise import extract_noise_features
from features_texture import extract_texture_features
from image_precomputed import ImagePrecomputedData

# Advanced detection modules
from features_gradient import extract_gradient_features
from features_forensic import extract_forensic_features
from features_model_specific import extract_model_specific_features

# Extended feature modules (per spec families 11-26)
from features_nss import extract_nss_features
from features_cfa import extract_cfa_features
from features_self_similarity import extract_self_similarity_features
from features_residual import extract_residual_features

logger = logging.getLogger(__name__)


# Dispatch table: family name -> (extract_func, label)
# Kept at module scope to avoid rebuilding the list per image.
_FAMILY_DISPATCH = [
    ('color', extract_color_features, 'color'),
    ('frequency', extract_frequency_features, 'frequency'),
    ('spectral_diffusion', extract_spectral_diffusion_features, 'spectral_diffusion'),
    ('noise', extract_noise_features, 'noise'),
    ('texture', extract_texture_features, 'texture'),
    ('gradient', extract_gradient_features, 'gradient'),
    ('forensic', extract_forensic_features, 'forensic'),
    ('model_specific', extract_model_specific_features, 'model_specific'),
    ('nss', extract_nss_features, 'nss'),
    ('cfa', extract_cfa_features, 'cfa'),
    ('self_similarity', extract_self_similarity_features, 'self_similarity'),
    ('residual', extract_residual_features, 'residual'),
]


# ============================================================================
# Feature Caching System
# ============================================================================

class FeatureCache:
    """
    Simple in-memory cache for extracted features.
    
    This cache is designed for single-run deduplication only - it prevents
    re-extracting features if the same image is processed twice in one run.
    
    For cross-run persistence, use the .pkl.gz feature files saved by
    extract_features.py instead. This avoids the overhead of database
    operations during parallel extraction.
    """
    
    def __init__(self, cache_dir: Optional[Union[str, Path]] = None, enable_memory_cache: bool = True):
        """
        Initialize the feature cache.
        
        Args:
            cache_dir: Unused, kept for API compatibility.
            enable_memory_cache: Whether to use in-memory cache (disable in worker processes)
        """
        # In-memory cache only - no disk persistence
        self.enable_memory_cache = enable_memory_cache
        self._memory_cache: Dict[str, Dict[str, float]] = {} if enable_memory_cache else None
        self._cache_hits = 0
        self._cache_misses = 0
        self._lock = threading.Lock()  # Thread-safe operations
        
    def _get_cache_key(
        self, 
        image_path: Optional[str], 
        families: List[str],
        max_dimension: int,
        resize_for_speed: bool
    ) -> str:
        """
        Generate a unique cache key for an image and settings combination.
        
        Args:
            image_path: Path to the image file
            families: List of feature families
            max_dimension: Max image dimension
            resize_for_speed: Whether resizing is enabled
            
        Returns:
            Unique cache key string
        """
        if not image_path:
            return None  # Can't cache without a path
        
        # Normalize path for consistent keys
        try:
            p = os.path.expanduser(str(image_path))
            p = os.path.abspath(p)
            p = os.path.normpath(p)
            p = os.path.normcase(p)
        except Exception:
            p = str(image_path)
        
        # Include settings in key
        settings = f"{max_dimension}_{resize_for_speed}_{','.join(sorted(families))}"
        settings_hash = hashlib.md5(settings.encode()).hexdigest()[:8]
        path_hash = hashlib.md5(p.encode()).hexdigest()[:12]
        
        return f"{path_hash}_{settings_hash}"
    
    def get(
        self,
        image_path: Optional[str],
        image: Image.Image,
        families: List[str],
        max_dimension: int,
        resize_for_speed: bool
    ) -> Optional[Dict[str, float]]:
        """
        Retrieve cached features if available.
        
        Args:
            image_path: Path to the image file
            image: PIL Image object (unused, kept for API compatibility)
            families: List of feature families
            max_dimension: Max image dimension
            resize_for_speed: Whether resizing is enabled
            
        Returns:
            Cached features dict or None if not cached
        """
        if self._memory_cache is None:
            return None
        
        cache_key = self._get_cache_key(image_path, families, max_dimension, resize_for_speed)
        if cache_key is None:
            return None
        
        with self._lock:
            if cache_key in self._memory_cache:
                self._cache_hits += 1
                return self._memory_cache[cache_key]
            self._cache_misses += 1
        return None
    
    def put(
        self,
        image_path: Optional[str],
        image: Image.Image,
        families: List[str],
        max_dimension: int,
        resize_for_speed: bool,
        features: Dict[str, float]
    ) -> None:
        """
        Store features in the cache.
        
        Args:
            image_path: Path to the image file
            image: PIL Image object (unused, kept for API compatibility)
            families: List of feature families
            max_dimension: Max image dimension
            resize_for_speed: Whether resizing is enabled
            features: Extracted features to cache
        """
        if self._memory_cache is None:
            return
        
        cache_key = self._get_cache_key(image_path, families, max_dimension, resize_for_speed)
        if cache_key is None:
            return
        
        with self._lock:
            self._memory_cache[cache_key] = features
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        with self._lock:
            memory_size = len(self._memory_cache) if self._memory_cache is not None else 0
            total = self._cache_hits + self._cache_misses
            return {
                'hits': self._cache_hits,
                'misses': self._cache_misses,
                'size': memory_size,
                'hit_rate': self._cache_hits / max(1, total),
            }
    
    def clear(self) -> None:
        """Clear all cached features."""
        with self._lock:
            if self._memory_cache is not None:
                self._memory_cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            logger.info("Feature cache cleared")


# Global cache instance
_global_cache: Optional[FeatureCache] = None


def get_feature_cache(cache_dir: Optional[Union[str, Path]] = None) -> FeatureCache:
    """Get or create the global feature cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = FeatureCache(cache_dir)
    return _global_cache


class FeatureExtractor:
    """
    Main feature extraction class that combines all feature families.
    
    Provides a unified interface for extracting pixel-level features
    from images for AI vs Real classification.
    
    ROBUST IMAGE TYPE HANDLING:
    ===========================
    This extractor is designed to work with ALL image types:
    
    - **Normal photographs**: Full feature extraction with camera-specific features
    - **Illustrations/Digital Art**: Focuses on lineart, brush, shading, palette features
    - **Screenshots**: Detects resampling, UI elements, compression chains
    - **Cropped images**: All features work regardless of crop boundaries
    - **Compressed/Resized**: Codec and frequency features detect artifacts
    - **Mixed content**: Text/graphic detection handles text overlays
    
    Each feature family gracefully handles missing or inappropriate features:
    - Camera-specific features (CFA, optics) return zeros for non-photos
    - Illustration features (lineart, brush) return zeros for photos
    - Screenshot features activate only when relevant patterns detected
    - All features include robust error handling and fallbacks
    
    FEATURE CACHING:
    ================
    Automatically caches extracted features (enabled by default) to avoid
    recomputation when re-running training. Cache is invalidated if image
    content changes. Set use_cache=False to disable.
    """
    
    # Feature families that can be selectively enabled
    FEATURE_FAMILIES = [
        'color',
        'frequency',
        'spectral_diffusion',
        'noise',
        'texture',
        'gradient',
        'forensic',
        'model_specific',
        'nss',
        'cfa',
        'self_similarity',
        'residual',
    ]
    
    def __init__(
        self,
        families: Optional[List[str]] = None,
        resize_for_speed: bool = True,
        max_dimension: int = 1024,  # Maximum dimension for feature extraction
        normalize_features: bool = False,
        n_jobs: int = 1,  # Number of workers (kept at 1 for resource-limited hosting)
        use_cache: bool = True,  # Enable feature caching
        cache_dir: Optional[Union[str, Path]] = None,  # Cache directory
        use_gpu: bool = False,  # Ignored, kept for API compatibility
    ):
        """
        Initialize the feature extractor.

        Args:
            families: List of feature families to extract. None = all.
            resize_for_speed: Whether to resize large images for faster processing.
            max_dimension: Maximum dimension when resizing (default 1024).
            normalize_features: Whether to normalize features (requires fitted scaler).
            n_jobs: Number of workers (always 1 on constrained hosting).
            use_cache: Whether to use feature caching for faster re-extraction.
            cache_dir: Directory for feature cache. None = default location.
            use_gpu: Ignored. Kept for API compatibility only.
        """
        self.families = families or self.FEATURE_FAMILIES
        self.resize_for_speed = resize_for_speed
        self.max_dimension = max_dimension
        self.normalize_features = normalize_features
        self.n_jobs = 1  # Always single-threaded
        self.scaler = None
        self.feature_names = None
        self.use_cache = use_cache
        self.cache = get_feature_cache(cache_dir) if use_cache else None
    
    def _preprocess_image(self, image: Image.Image) -> np.ndarray:
        """
        Preprocess image for feature extraction.
        
        - Uses original image size for most images
        - Only if image is larger than max_dimension in any dimension, scales it down
          and adds black bars (letterbox/pillarbox) to maintain aspect ratio
          at max_dimension x max_dimension.
        
        Args:
            image: PIL Image
            
        Returns:
            Numpy array (H, W, 3) in RGB format, uint8
        """
        # Convert to RGB if needed
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        w, h = image.size
        
        # Only resize if image is larger than max_dimension
        if self.resize_for_speed and max(w, h) > self.max_dimension:
            # Calculate scale to fit within max_dimension
            scale = self.max_dimension / max(w, h)
            new_w = int(w * scale)
            new_h = int(h * scale)
            
            # Resize the image
            image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            # Create black canvas at max_dimension x max_dimension
            canvas = Image.new('RGB', (self.max_dimension, self.max_dimension), (0, 0, 0))
            
            # Calculate position to paste (center the image)
            x_offset = (self.max_dimension - new_w) // 2
            y_offset = (self.max_dimension - new_h) // 2
            
            # Paste the resized image onto the black canvas
            canvas.paste(image, (x_offset, y_offset))
            
            return np.array(canvas)
        
        # Use original size for images within max_dimension
        return np.array(image)

    def _extract_impl(
        self,
        image: Image.Image,
        *,
        return_timings: bool,
    ) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        """
        Internal extract implementation with optional per-family timings.

        When FEATURE_PROFILE=1 is set, timings will be logged regardless of
        return_timings, but the returned timings dict is only provided when
        return_timings=True.
        """
        img_array = self._preprocess_image(image)

        # Create precomputed data instance to share common computations.
        precomputed = ImagePrecomputedData(img_array)

        features: Dict[str, float] = {}

        env_profile = os.environ.get('FEATURE_PROFILE', '') == '1'
        do_profile = return_timings or env_profile
        timings: Optional[Dict[str, float]] = {} if do_profile else None

        # Build list of families to run
        active_families = [
            (family_name, extract_func, label)
            for family_name, extract_func, label in _FAMILY_DISPATCH
            if family_name in self.families
        ]

        if do_profile or len(active_families) <= 1:
            # Sequential path – used for profiling (accurate per-family timings)
            for family_name, extract_func, label in active_families:
                t0 = time.perf_counter() if do_profile else 0.0
                try:
                    features.update(extract_func(img_array, precomputed=precomputed))
                except Exception as e:
                    logger.warning(f"Error extracting {label} features: {e}")
                if timings is not None:
                    timings[label] = time.perf_counter() - t0
        else:
            # Sequential path – images dispatched in separate processes already
            for family_name, extract_func, label in active_families:
                try:
                    features.update(extract_func(img_array, precomputed=precomputed))
                except Exception as e:
                    logger.warning(f"Error extracting {label} features: {e}")

        if env_profile and timings is not None:
            total = sum(timings.values())
            ranked = sorted(timings.items(), key=lambda kv: kv[1], reverse=True)
            logger.info("=== FEATURE PROFILE (%.3fs total) ===" % total)
            for name, dur in ranked:
                logger.info("  %-22s %7.3fs  (%5.1f%%)" % (name, dur, 100.0 * dur / max(total, 1e-9)))
            
            # Cache sizes for memory profiling
            if precomputed is not None:
                cache_info = precomputed.get_cache_sizes()
                logger.info("=== CACHE SIZES ===")
                for cache_name, cache_size in cache_info.items():
                    logger.info("  %-22s %d entries" % (cache_name, cache_size))
            
            # Export flamegraph-compatible JSON if requested
            if os.environ.get('FEATURE_PROFILE_FLAMEGRAPH', '') == '1':
                try:
                    import json as _json
                    flamegraph_data = []
                    for name, dur in ranked:
                        flamegraph_data.append({
                            'name': name,
                            'value': round(dur * 1000, 2),  # ms
                            'category': 'feature_extraction',
                        })
                    _fg_path = os.environ.get('FEATURE_PROFILE_FLAMEGRAPH_PATH', 'profile_output.json')
                    with open(_fg_path, 'a') as _fg_f:
                        _fg_f.write(_json.dumps({'timings': flamegraph_data, 'total_ms': round(total * 1000, 2)}) + '\n')
                except Exception:
                    pass

        # Handle NaN/Inf values
        features = self._sanitize_features(features)

        # Clear precomputed dict-caches to free memory after feature extraction
        if precomputed is not None:
            precomputed.clear_caches()
        del precomputed
        import gc; gc.collect()

        if return_timings:
            return features, (timings or {})
        return features, None
    
    def extract(self, image: Image.Image) -> Dict[str, float]:
        """
        Extract all features from an image.

        Args:
            image: PIL Image

        Returns:
            Dictionary of feature names to values
        """
        features, _ = self._extract_impl(image, return_timings=False)
        return features

    def extract_with_timings(self, image: Image.Image) -> Tuple[Dict[str, float], Dict[str, float]]:
        """
        Extract features and return a per-family timing breakdown.

        Returns:
            (features_dict, timings_dict)
        """
        features, timings = self._extract_impl(image, return_timings=True)
        return features, (timings or {})
    
    def extract_from_path(self, image_path: str) -> Dict[str, float]:
        """
        Extract features from an image file path, using cache if available.
        
        This method is preferred for training as it enables feature caching
        to avoid recomputing features for already-processed images.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Dictionary of feature names to values
        """
        # Normalize path for stable caching and to avoid collisions between
        # identical filenames in different folders (e.g., dataset/ai vs dataset/real).
        try:
            normalized_path = str(Path(image_path).expanduser().resolve())
        except Exception:
            normalized_path = str(image_path)

        with Image.open(normalized_path) as image:
            # Check cache first
            if self.cache is not None:
                cached = self.cache.get(
                    normalized_path,
                    image,
                    self.families,
                    self.max_dimension,
                    self.resize_for_speed,
                )
                if cached is not None:
                    return cached

            # Extract features
            features = self.extract(image)

            # Store in cache
            if self.cache is not None:
                self.cache.put(
                    normalized_path,
                    image,
                    self.families,
                    self.max_dimension,
                    self.resize_for_speed,
                    features,
                )

            return features
    
    def get_cache_stats(self) -> Optional[Dict[str, int]]:
        """Get cache statistics if caching is enabled."""
        if self.cache is not None:
            return self.cache.get_stats()
        return None

    def _sanitize_features(self, features: Dict[str, Union[float, List[float]]]) -> Dict[str, Union[float, List[float]]]:
        """Replace NaN/Inf values with 0, handling scalars and lists."""
        sanitized = {}
        for key, value in features.items():
            if isinstance(value, (list, tuple, np.ndarray)):
                # Handle list/array
                clean_list = []
                for v in value:
                    v_scalar = v
                    if hasattr(v, 'item'):
                        v_scalar = v.item()
                        
                    if np.isnan(v_scalar) or np.isinf(v_scalar):
                        clean_list.append(0.0)
                    else:
                        clean_list.append(float(v_scalar))
                sanitized[key] = clean_list
            else:
                # Handle scalar
                v_scalar = value
                if hasattr(value, 'item'):
                    v_scalar = value.item()
                    
                if np.isnan(v_scalar) or np.isinf(v_scalar):
                    sanitized[key] = 0.0
                else:
                    sanitized[key] = float(v_scalar)
        return sanitized
    
    def extract_batch(self, images: List[Image.Image], show_progress: bool = True) -> List[Dict[str, float]]:
        """
        Extract features from multiple images.
        
        Args:
            images: List of PIL Images
            show_progress: Whether to show progress bar
            
        Returns:
            List of feature dictionaries
        """
        from tqdm import tqdm
        
        # Always process sequentially to limit resource usage
        results = []
        iterator = tqdm(images, desc="Extracting features") if show_progress else images
        
        for image in iterator:
            features = self.extract(image)
            results.append(features)
        
        return results
    
    def to_vector(self, features: Dict[str, float]) -> np.ndarray:
        """
        Convert feature dictionary to numpy vector.
        
        Uses consistent feature ordering.
        """
        if self.feature_names is None:
            self.feature_names = sorted(features.keys())
        
        vector = np.array([features.get(name, 0.0) for name in self.feature_names])
        
        if self.normalize_features and self.scaler is not None:
            vector = self.scaler.transform(vector.reshape(1, -1))[0]
        
        return vector
    
    def get_feature_names(self) -> List[str]:
        """Get ordered list of feature names."""
        return self.feature_names if self.feature_names else []
    
    def fit_scaler(self, feature_dicts: List[Dict[str, float]]) -> None:
        """
        Fit a standard scaler on training features.
        
        Args:
            feature_dicts: List of feature dictionaries from training set
        """
        from sklearn.preprocessing import StandardScaler
        
        # Ensure consistent feature names
        if not feature_dicts:
            return
        
        self.feature_names = sorted(feature_dicts[0].keys())
        
        # Convert to matrix
        X = np.array([
            [fd.get(name, 0.0) for name in self.feature_names]
            for fd in feature_dicts
        ])
        
        # Fit scaler
        self.scaler = StandardScaler()
        self.scaler.fit(X)
        self.normalize_features = True
    
    def save_scaler(self, path: str) -> None:
        """Save fitted scaler to file."""
        import joblib
        if self.scaler is not None:
            joblib.dump({
                'scaler': self.scaler,
                'feature_names': self.feature_names
            }, path)
    
    def load_scaler(self, path: str) -> None:
        """Load scaler from file."""
        import joblib
        data = joblib.load(path)
        self.scaler = data['scaler']
        self.feature_names = data['feature_names']
        self.normalize_features = True


def _load_image_data(path: str) -> Optional[Tuple[np.ndarray, str]]:
    """Pre-load image data to avoid I/O in workers."""
    try:
        with Image.open(path) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            return np.array(img), path
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        return None


# Module-level extractor for worker reuse (avoid recreation overhead)
_worker_extractor = None
_worker_params = None


def _init_worker(families, resize_for_speed, max_dimension):
    """Initialize worker with reusable extractor (single-threaded)."""
    global _worker_extractor, _worker_params
    
    # Force single-threaded execution in worker processes
    import os
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    os.environ['VECLIB_MAXIMUM_THREADS'] = '1'
    os.environ['NUMEXPR_NUM_THREADS'] = '1'
    os.environ['NUMBA_NUM_THREADS'] = '1'
    os.environ['NUMBA_THREADING_LAYER'] = 'workqueue'
    
    try:
        import cv2
        cv2.setNumThreads(1)
        cv2.ocl.setUseOpenCL(False)
    except ImportError:
        pass
    
    _worker_params = (families, resize_for_speed, max_dimension)
    _worker_extractor = FeatureExtractor(
        families=families,
        resize_for_speed=resize_for_speed,
        max_dimension=max_dimension,
        n_jobs=1,
        use_cache=False,
    )


def _extract_single_image(args):
    """Worker function for parallel processing - extracts features from pre-loaded image data."""
    global _worker_extractor, _worker_params
    
    img_array, path, label, families, resize_for_speed, max_dimension = args
    try:
        # Reuse extractor if params match, otherwise create new one
        if _worker_extractor is None or _worker_params != (families, resize_for_speed, max_dimension):
            _init_worker(families, resize_for_speed, max_dimension)
        
        # Convert image array to PIL Image for preprocessing
        from PIL import Image as PILImage
        if getattr(img_array, "dtype", None) != np.uint8:
            img_array = img_array.astype(np.uint8, copy=False)
        img = PILImage.fromarray(img_array)
        
        # Extract features directly from image
        features = _worker_extractor.extract(img)
        
        return features, label, path
    except Exception as e:
        logger.warning("Error processing %s (%s): %r", path, type(e).__name__, e)
        return None, None, path


def _extract_batch(batch_args):
    """Process a batch of images in a single worker call to reduce overhead."""
    results = []
    for args in batch_args:
        results.append(_extract_single_image(args))
    return results


def extract_features_for_training(
    image_paths: List[str],
    labels: List[int],
    extractor: Optional[FeatureExtractor] = None,
    n_jobs: int = 4,  # Default to 4 workers
    use_cache: bool = True,  # Enable feature caching
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Extract features from a list of image paths for training with caching support.
    
    This function automatically caches extracted features to avoid recomputation
    when re-running training on the same images. Cached features are invalidated
    if the image content changes.
    
    Args:
        image_paths: List of paths to images
        labels: List of labels (0=real, 1=ai)
        extractor: Optional FeatureExtractor instance
        n_jobs: Number of parallel jobs (1 = sequential, >1 = parallel)
        use_cache: Whether to use feature caching
        
    Returns:
        X: Feature matrix (n_samples, n_features)
        y: Label array (n_samples,)
        feature_names: List of feature names
    """
    from tqdm import tqdm
    from joblib import Parallel, delayed
    import time
    from datetime import datetime, timedelta
    
    if extractor is None:
        extractor = FeatureExtractor(use_cache=use_cache)
    
    features_list = []
    valid_labels = []
    skipped_paths = []
    
    # Get cache for stats
    cache = get_feature_cache() if use_cache else None
    
    if n_jobs == 1:
        # Sequential processing with caching
        for path, label in tqdm(zip(image_paths, labels), total=len(image_paths), desc="Extracting features"):
            try:
                # Use extract_from_path for caching support
                features = extractor.extract_from_path(path)
                features_list.append(features)
                valid_labels.append(label)
            except Exception as e:
                logger.warning("Error processing %s (%s): %r", path, type(e).__name__, e)
                skipped_paths.append(path)
                continue
    else:
        # Parallel processing with pre-loaded images to avoid I/O bottlenecks
        print(f"\n📂 Pre-loading {len(image_paths)} images...")
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        # Pre-load all images in parallel using threads (I/O bound)
        loaded_images = []
        with ThreadPoolExecutor(max_workers=min(n_jobs * 2, 32)) as executor:
            futures = {executor.submit(_load_image_data, path): (path, label) 
                      for path, label in zip(image_paths, labels)}
            
            for future in tqdm(as_completed(futures), total=len(futures), desc="Loading images"):
                result = future.result()
                if result is not None:
                    img_array, path = result
                    _, label = futures[future]
                    loaded_images.append((img_array, path, label))
                else:
                    _, label = futures[future]
                    skipped_paths.append(futures[future][0])
        
        if not loaded_images:
            raise ValueError("No images could be loaded!")
        
        print(f"✅ Loaded {len(loaded_images)} images successfully")
        
        # Parallel feature extraction with pre-loaded images
        extractor_params = (
            extractor.families,
            extractor.resize_for_speed,
            extractor.max_dimension,
        )
        args_list = [(img_array, path, label) + extractor_params 
                    for img_array, path, label in loaded_images]
        
        # Progress tracking with ETA using tqdm
        total = len(args_list)
        start_time = time.time()
        
        print(f"\n📊 Extracting features from {total} images with {n_jobs} workers...")
        print(f"⏰ Started at: {datetime.now().strftime('%H:%M:%S')}")
        print("-" * 60)
        
        # Limit thread usage in main process too
        import os
        os.environ['OMP_NUM_THREADS'] = '1'
        os.environ['MKL_NUM_THREADS'] = '1'
        os.environ['OPENBLAS_NUM_THREADS'] = '1'
        os.environ['NUMEXPR_NUM_THREADS'] = '1'
        os.environ.setdefault('LOKY_MAX_CPU_COUNT', str(n_jobs))
        
        # Create batches to reduce per-task overhead
        # Optimal batch size: enough work per batch to amortize overhead
        batch_size = max(1, min(8, total // (n_jobs * 4)))
        batches = [args_list[i:i + batch_size] for i in range(0, len(args_list), batch_size)]
        
        # Use loky backend with optimized settings
        # - pre_dispatch='2*n_jobs' keeps workers busy
        # - batch_size=1 since we're already batching manually
        batch_results = Parallel(
            n_jobs=n_jobs, 
            backend='loky',
            return_as='generator',
            timeout=1200,  # 20 minutes timeout
            pre_dispatch='2*n_jobs',  # Keep workers fed
            verbose=0,
        )(
            delayed(_extract_batch)(batch) 
            for batch in batches
        )
        
        # Flatten results with progress
        all_results = []
        for batch_result in tqdm(
            batch_results, 
            total=len(batches),
            desc="Extracting",
            unit="batch",
            bar_format='{l_bar}{bar:30}{r_bar}{bar:-10b}',
            dynamic_ncols=True
        ):
            all_results.extend(batch_result)
        
        # Process results
        for features, label, path in all_results:
            if features is not None:
                features_list.append(features)
                valid_labels.append(label)
            else:
                skipped_paths.append(path)
        
        # Final stats
        elapsed = time.time() - start_time
        successful = len(features_list)
        throughput = successful / elapsed if elapsed > 0 else 0
        time_per_image = elapsed / successful if successful > 0 else 0
        
        print(f"\n✅ Completed at: {datetime.now().strftime('%H:%M:%S')}")
        print(f"⏱️  Total time: {int(elapsed // 60)}m {int(elapsed % 60)}s")
        print(f"📈 Throughput: {throughput:.2f} images/second")
        print(f"⌛ Time per image: {time_per_image*1000:.0f}ms")
        
        if n_jobs > 1:
            # Estimate single-threaded time (rough estimate ~10s per image)
            estimated_single = successful * 10  
            speedup = estimated_single / elapsed if elapsed > 0 else 1
            print(f"⚡ Estimated speedup: {speedup:.1f}x vs single-threaded")
        
        print()
    
    if skipped_paths:
        logger.warning(f"Skipped {len(skipped_paths)} images due to errors")
    
    if not features_list:
        raise ValueError("No features extracted!")
    
    # Collect ALL unique feature names across all images
    # This ensures consistent feature sets even if some images fail to extract certain features
    all_feature_names = set()
    for fd in features_list:
        all_feature_names.update(fd.keys())
    
    feature_names = sorted(all_feature_names)
    extractor.feature_names = feature_names
    
    logger.info(f"Total unique features: {len(feature_names)}")
    
    # Convert to arrays, filling missing features with 0.0
    X = np.array([
        [fd.get(name, 0.0) for name in feature_names]
        for fd in features_list
    ])
    y = np.array(valid_labels)
    
    # Verify shape consistency
    logger.info(f"Feature matrix shape: {X.shape} (samples × features)")
    
    return X, y, feature_names


def get_top_features(
    feature_importances: np.ndarray,
    feature_names: List[str],
    top_k: int = 20
) -> List[Tuple[str, float]]:
    """
    Get top-k most important features.
    
    Args:
        feature_importances: Array of feature importance scores
        feature_names: List of feature names
        top_k: Number of top features to return
        
    Returns:
        List of (feature_name, importance) tuples
    """
    indices = np.argsort(feature_importances)[::-1][:top_k]
    return [(feature_names[i], feature_importances[i]) for i in indices]


def explain_prediction(
    features: Dict[str, float],
    feature_importances: Dict[str, float],
    prediction_proba: float,
    top_k: int = 10
) -> Dict[str, any]:
    """
    Generate human-readable explanation for a prediction.
    
    Args:
        features: Extracted features
        feature_importances: Feature importance scores
        prediction_proba: Predicted probability of AI
        top_k: Number of top contributing features
        
    Returns:
        Dictionary with explanation details
    """
    # Get top contributing features
    contributions = []
    for name, value in features.items():
        importance = feature_importances.get(name, 0)
        contribution = abs(importance * value)
        contributions.append((name, value, importance, contribution))
    
    # Sort by contribution
    contributions.sort(key=lambda x: x[3], reverse=True)
    top_contributors = contributions[:top_k]
    
    # Generate text explanation
    if prediction_proba > 0.7:
        confidence = "high"
        label = "AI-GENERATED"
    elif prediction_proba > 0.55:
        confidence = "moderate"
        label = "LIKELY AI"
    elif prediction_proba < 0.3:
        confidence = "high"
        label = "REAL"
    elif prediction_proba < 0.45:
        confidence = "moderate"
        label = "LIKELY REAL"
    else:
        confidence = "low"
        label = "UNCERTAIN"
    
    explanation = {
        'label': label,
        'probability_ai': prediction_proba,
        'confidence': confidence,
        'top_features': [
            {
                'name': name,
                'value': value,
                'importance': importance,
                'contribution': contrib
            }
            for name, value, importance, contrib in top_contributors
        ],
    }
    
    # Add specific insights based on feature values
    insights = []
    
    for name, value, importance, contrib in top_contributors[:5]:
        if 'noise' in name.lower() and importance > 0:
            if value < 0.5:
                insights.append("Unusually low noise levels detected")
            else:
                insights.append("Noise pattern suggests real photograph")
        elif 'fft' in name.lower() or 'frequency' in name.lower():
            if importance > 0 and value > 0.5:
                insights.append("Unusual frequency domain patterns detected")
        elif 'saturation' in name.lower():
            if value > 180:
                insights.append("High saturation levels (common in AI)")
        elif 'checkerboard' in name.lower():
            if value > 0.1:
                insights.append("Checkerboard artifacts detected (GAN signature)")
    
    explanation['insights'] = insights[:3]  # Limit to top 3 insights
    
    return explanation
