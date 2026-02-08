"""
features_self_similarity.py - Self-Similarity and Repetition Features for AI detection.

Analyzes pattern repetition and self-similarity:
- Nearest-neighbor patch distance distribution
- Copy-move style correlation (summary stats)
- Cross-scale patch matching
- Motif repetition peaks

AI images often have characteristic repetition patterns from
the generation process, especially in textures and backgrounds.
"""

import numpy as np
from scipy import ndimage, signal
from typing import Dict, Tuple, Optional, List, TYPE_CHECKING
import warnings

# GPU acceleration utilities
from gpu_utils import gpu_correlate2d_fft

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Optional OpenCV acceleration
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Try to import JIT utilities for acceleration
try:
    from jit_utils import fast_patch_nn_distances, fast_radial_profile
    HAS_JIT_PATCHES = True
except ImportError:
    HAS_JIT_PATCHES = False

from utils import fast_skew, fast_kurt

# Try Cython-compiled versions (even faster than Numba)
try:
    from cython_ext.fast_patches import fast_patch_distances_cy, fast_radial_profile_cy
    HAS_CYTHON_PATCHES = True
except ImportError:
    HAS_CYTHON_PATCHES = False

# Fast FFT using scipy.fft with multi-threading (3-4x faster than numpy.fft)
try:
    from scipy.fft import fft2 as _sp_fft2, ifft2 as _sp_ifft2, fftshift as _sp_fftshift, ifftshift as _sp_ifftshift, fft as _sp_fft
    def _fast_fft2(x, **kw): return _sp_fft2(x, workers=-1, **kw)
    def _fast_ifft2(x, **kw): return _sp_ifft2(x, workers=-1, **kw)
    def _fast_fft(x, **kw): return _sp_fft(x, workers=-1, **kw)
    _fast_fftshift = _sp_fftshift
    _fast_ifftshift = _sp_ifftshift
except ImportError:
    from numpy.fft import fft2 as _fast_fft2, ifft2 as _fast_ifft2, fftshift as _fast_fftshift, ifftshift as _fast_ifftshift, fft as _fast_fft


def extract_patches(gray: np.ndarray, patch_size: int = 8, stride: int = 4,
                    max_patches: int = 500) -> np.ndarray:
    """
    Extract patches from image for analysis using stride_tricks for speed.

    Returns:
        Array of shape (n_patches, patch_size * patch_size), list of positions
    """
    h, w = gray.shape

    if h < patch_size or w < patch_size:
        return np.array([]), []

    # Calculate number of patches in each dimension
    n_patches_h = (h - patch_size) // stride + 1
    n_patches_w = (w - patch_size) // stride + 1
    total_patches = n_patches_h * n_patches_w

    if total_patches == 0:
        return np.array([]), []

    # Use stride_tricks for zero-copy patch extraction
    shape = (n_patches_h, n_patches_w, patch_size, patch_size)
    strides = (gray.strides[0] * stride, gray.strides[1] * stride,
               gray.strides[0], gray.strides[1])
    patches_4d = np.lib.stride_tricks.as_strided(gray, shape=shape, strides=strides)

    # Limit to max_patches
    if total_patches > max_patches:
        # Sample uniformly across the grid
        step_h = max(1, n_patches_h * n_patches_w // max_patches)
        flat_indices = np.arange(0, total_patches, step_h)[:max_patches]
        i_coords = flat_indices // n_patches_w
        j_coords = flat_indices % n_patches_w
        patches = patches_4d[i_coords, j_coords].reshape(len(flat_indices), -1).copy()
        positions = [(int(i * stride), int(j * stride)) for i, j in zip(i_coords, j_coords)]
    else:
        # Reshape all patches
        patches = patches_4d.reshape(-1, patch_size * patch_size).copy()
        positions = [(i * stride, j * stride)
                     for i in range(n_patches_h) for j in range(n_patches_w)]
        # Convert to array for vectorized operations
        patches_array = np.array(patches)

    return patches, positions


def _downsample_gray(gray: np.ndarray, max_dim: int = 256) -> Tuple[np.ndarray, float]:
    """Downsample grayscale to a max dimension; returns (downsampled, scale_factor)."""
    h, w = gray.shape
    max_side = max(h, w)
    if max_side <= max_dim:
        return gray, 1.0
    scale = max_dim / max_side
    new_w = max(8, int(w * scale))
    new_h = max(8, int(h * scale))
    if HAS_CV2:
        ds = cv2.resize(gray.astype(np.float32), (new_w, new_h), interpolation=cv2.INTER_AREA)
    else:
        try:
            import cv2 as _cv2
            ds = _cv2.resize(gray.astype(np.float32), (new_w, new_h), interpolation=_cv2.INTER_AREA)
        except ImportError:
            ds = ndimage.zoom(gray, (new_h / h, new_w / w), order=1)
    return ds.astype(np.float32), 1.0 / scale


def compute_patch_distance_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Analyze nearest-neighbor patch distance distribution.
    
    AI images often have patches that are either too similar
    (repetition) or too different (inconsistency).
    """
    features = {}
    gray = precomputed.gray
    
    # Normalize
    gray_min, gray_max = np.min(gray), np.max(gray)
    if gray_max - gray_min > 1e-10:
        gray = (gray - gray_min) / (gray_max - gray_min)
    
    # Extract patches (reduced from 400 to 200 for speed, low risk)
    patches, positions = extract_patches(gray, patch_size=8, stride=8, max_patches=200)
    
    if len(patches) < 10:
        return {'patch_nn_dist_mean': 0.0, 'patch_repetition_score': 0.0}
    
    # Compute pairwise distances (Euclidean)
    # Use random sampling for efficiency with fixed seed for reproducibility
    n = len(patches)
    n_samples = min(100, n)
    
    rng = np.random.RandomState(42)
    sample_idx = rng.choice(n, n_samples, replace=False)
    sample_patches = patches[sample_idx]

    patches = np.asarray(patches, dtype=np.float64)
    sample_patches = np.asarray(sample_patches, dtype=np.float64)

    if HAS_CYTHON_PATCHES:
        nn_distances = fast_patch_distances_cy(
            sample_patches, patches, sample_idx.astype(np.int64)
        )
    elif HAS_JIT_PATCHES:
        # Use JIT-parallelized NN distance computation
        nn_distances = fast_patch_nn_distances(
            sample_patches, patches, sample_idx.astype(np.int64)
        )
    else:
        # Vectorized nearest-neighbor distances:
        # ||a-b||^2 = ||a||^2 + ||b||^2 - 2 a·b
        a2 = np.sum(sample_patches * sample_patches, axis=1, keepdims=True)
        b2 = np.sum(patches * patches, axis=1)
        d2 = a2 + b2[None, :] - 2.0 * (sample_patches @ patches.T)
        d2 = np.maximum(d2, 0.0)
        d2[np.arange(n_samples), sample_idx] = np.inf
        nn_distances = np.sqrt(np.min(d2, axis=1))
    
    features['patch_nn_dist_mean'] = float(np.mean(nn_distances))
    features['patch_nn_dist_std'] = float(np.std(nn_distances))
    features['patch_nn_dist_min'] = float(np.min(nn_distances))
    features['patch_nn_dist_max'] = float(np.max(nn_distances))
    
    # Percentiles
    _flat_nn = nn_distances.ravel()
    _n_nn = len(_flat_nn)
    _i10, _i50, _i90 = int(0.10 * (_n_nn - 1)), int(0.50 * (_n_nn - 1)), int(0.90 * (_n_nn - 1))
    _part_nn = np.partition(_flat_nn, [_i10, _i50, _i90])
    features['patch_nn_dist_p10'] = float(_part_nn[_i10])
    features['patch_nn_dist_p50'] = float(_part_nn[_i50])
    features['patch_nn_dist_p90'] = float(_part_nn[_i90])
    
    # Repetition score (percentage of very similar patches)
    similarity_threshold = features['patch_nn_dist_mean'] * 0.3
    features['patch_repetition_score'] = float(
        np.mean(nn_distances < similarity_threshold)
    )
    
    # Distribution shape
    features['patch_nn_dist_skew'] = float(fast_skew(nn_distances))
    features['patch_nn_dist_kurtosis'] = float(fast_kurt(nn_distances))
    
    return features


def compute_spatial_repetition_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect spatial patterns of repetition.
    
    Check if similar patches appear at regular intervals
    (indicating AI-like texture generation).
    """
    features = {}
    gray = precomputed.gray
    h, w = gray.shape
    
    # Compute autocorrelation using cached precomputed or GPU-accelerated FFT
    autocorr = precomputed.autocorr_gray
    
    # Normalize
    autocorr = autocorr / (autocorr.max() + 1e-10)
    
    cy, cx = h // 2, w // 2
    
    # Exclude center region (self-correlation)
    center_size = min(20, h // 10, w // 10)
    autocorr_masked = autocorr.copy()
    autocorr_masked[cy-center_size:cy+center_size, cx-center_size:cx+center_size] = 0
    
    # Find peaks (indicating repetition)
    # Use local maxima detection
    peak_threshold = 0.3
    high_corr = autocorr_masked > peak_threshold
    
    features['repetition_peak_count'] = float(np.sum(high_corr))
    features['repetition_max_corr'] = float(np.max(autocorr_masked))
    
    # Find dominant repetition offset
    if np.max(autocorr_masked) > 0.1:
        max_idx = np.unravel_index(np.argmax(autocorr_masked), autocorr_masked.shape)
        offset_y = abs(max_idx[0] - cy)
        offset_x = abs(max_idx[1] - cx)
        features['repetition_dominant_offset_y'] = float(offset_y / h)
        features['repetition_dominant_offset_x'] = float(offset_x / w)
    else:
        features['repetition_dominant_offset_y'] = 0.0
        features['repetition_dominant_offset_x'] = 0.0
    
    # Radial profile of autocorrelation
    max_r = min(cy, cx) // 2
    
    if max_r > 10:
        if HAS_CYTHON_PATCHES:
            radial_profile = fast_radial_profile_cy(autocorr.astype(np.float64), max_r, center_size)
        elif HAS_JIT_PATCHES:
            # Use JIT-parallelized radial profile
            radial_profile = fast_radial_profile(autocorr, max_r, center_size)
        else:
            y, x = np.ogrid[:h, :w]
            r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2).astype(int)
            r_flat = r.ravel()
            auto_flat = autocorr.ravel()
            valid_mask = r_flat < max_r
            
            radial_sum = np.bincount(r_flat[valid_mask], weights=auto_flat[valid_mask], minlength=max_r)
            radial_count = np.bincount(r_flat[valid_mask], minlength=max_r)
            radial_profile = np.divide(radial_sum, radial_count, out=np.zeros(max_r), where=radial_count > 0)
            
            # Skip center
            radial_profile = radial_profile[center_size:]
        
        if len(radial_profile) > 5:
            features['repetition_radial_mean'] = float(np.mean(radial_profile))
            features['repetition_radial_max'] = float(np.max(radial_profile))
            
            # Periodicity in radial profile (indicates regular repetition)
            fft_radial = np.abs(_fast_fft(radial_profile))
            fft_radial[0] = 0  # Remove DC
            features['repetition_radial_periodicity'] = float(np.max(fft_radial))
        else:
            features['repetition_radial_mean'] = 0.0
            features['repetition_radial_max'] = 0.0
            features['repetition_radial_periodicity'] = 0.0
    else:
        features['repetition_radial_mean'] = 0.0
        features['repetition_radial_max'] = 0.0
        features['repetition_radial_periodicity'] = 0.0
    
    return features


def compute_cross_scale_similarity_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Analyze cross-scale self-similarity.
    
    Compare patches between different scales of the image.
    Natural images have consistent cross-scale relationships.
    """
    features = {}
    gray = precomputed.gray
    h, w = gray.shape
    
    if min(h, w) < 64:
        return {'cross_scale_similarity': 0.0}
    
    # Create downsampled version
    if HAS_CV2:
        new_w = max(1, int(round(w * 0.5)))
        new_h = max(1, int(round(h * 0.5)))
        gray_small = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
    else:
        try:
            import cv2 as _cv2
            new_w = max(1, int(round(w * 0.5)))
            new_h = max(1, int(round(h * 0.5)))
            gray_small = _cv2.resize(gray, (new_w, new_h), interpolation=_cv2.INTER_AREA)
        except ImportError:
            gray_small = ndimage.zoom(gray, 0.5, order=1)
    
    # Upsample back
    if HAS_CV2:
        gray_upsampled = cv2.resize(
            gray_small,
            (w, h),
            interpolation=cv2.INTER_LINEAR,
        )
    else:
        try:
            import cv2 as _cv2
            gray_upsampled = _cv2.resize(
                gray_small,
                (w, h),
                interpolation=_cv2.INTER_LINEAR,
            )
        except ImportError:
            gray_upsampled = ndimage.zoom(gray_small, 2, order=1)
    
    # Match sizes
    min_h = min(h, gray_upsampled.shape[0])
    min_w = min(w, gray_upsampled.shape[1])
    
    gray = gray[:min_h, :min_w]
    gray_upsampled = gray_upsampled[:min_h, :min_w]
    
    # Compare patches between original and upsampled
    patch_size = 16
    stride = 16

    # Match existing behavior: iterate i,j in range(0, min_h - patch_size, stride)
    stop_h = min_h - patch_size
    stop_w = min_w - patch_size
    if stop_h <= 0 or stop_w <= 0:
        features['cross_scale_similarity_mean'] = 1.0
        features['cross_scale_similarity_std'] = 0.0
        features['cross_scale_similarity_min'] = 1.0
        return features

    n_ph = (stop_h + stride - 1) // stride
    n_pw = (stop_w + stride - 1) // stride

    gray_c = np.ascontiguousarray(gray)
    up_c = np.ascontiguousarray(gray_upsampled)

    shape = (n_ph, n_pw, patch_size, patch_size)
    strides = (
        gray_c.strides[0] * stride,
        gray_c.strides[1] * stride,
        gray_c.strides[0],
        gray_c.strides[1],
    )
    patches_o = np.lib.stride_tricks.as_strided(gray_c, shape=shape, strides=strides).reshape(-1, patch_size * patch_size)
    patches_u = np.lib.stride_tricks.as_strided(up_c, shape=shape, strides=strides).reshape(-1, patch_size * patch_size)

    patches_o = patches_o - patches_o.mean(axis=1, keepdims=True)
    patches_u = patches_u - patches_u.mean(axis=1, keepdims=True)
    num = np.sum(patches_o * patches_u, axis=1)
    den = (np.linalg.norm(patches_o, axis=1) * np.linalg.norm(patches_u, axis=1))
    valid = den > 1e-10

    if np.any(valid):
        sims = num[valid] / den[valid]
        features['cross_scale_similarity_mean'] = float(np.mean(sims))
        features['cross_scale_similarity_std'] = float(np.std(sims))
        features['cross_scale_similarity_min'] = float(np.min(sims))
    else:
        features['cross_scale_similarity_mean'] = 1.0
        features['cross_scale_similarity_std'] = 0.0
        features['cross_scale_similarity_min'] = 1.0
    
    return features


def compute_texture_repetition_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect texture repetition patterns specific to AI generation.
    
    AI models often create textures with subtle repetition that
    doesn't occur in natural or manually created textures.
    """
    features = {}
    gray = precomputed.gray
    h, w = gray.shape
    
    # High-pass filter to isolate texture
    low_pass = precomputed.gaussian_3
    texture = gray - low_pass
    
    # Compute texture autocorrelation using cached precomputed
    autocorr = precomputed.autocorr_texture
    
    # Normalize
    if autocorr.max() > 0:
        autocorr = autocorr / autocorr.max()
    
    cy, cx = h // 2, w // 2
    
    # Analyze texture repetition
    center_size = 10
    autocorr_masked = autocorr.copy()
    autocorr_masked[cy-center_size:cy+center_size, cx-center_size:cx+center_size] = 0
    
    features['texture_repetition_max'] = float(np.max(autocorr_masked))
    
    # Count distinct texture motifs
    motif_threshold = 0.2
    motif_mask = autocorr_masked > motif_threshold
    labeled, n_motifs = ndimage.label(motif_mask)
    features['texture_motif_count'] = float(n_motifs)
    
    # Regularity of motif distribution
    if n_motifs > 1:
        # Vectorized motif center computation using ndimage.center_of_mass
        motif_centers = ndimage.center_of_mass(np.ones_like(labeled), labeled, range(1, n_motifs + 1))
        motif_centers = np.array([c for c in motif_centers if not np.any(np.isnan(c))])
        
        if len(motif_centers) > 1:
            # Vectorized pairwise distances using broadcasting
            # Compute upper triangle of distance matrix
            diff = motif_centers[:, None, :] - motif_centers[None, :, :]  # (n, n, 2)
            dist_matrix = np.linalg.norm(diff, axis=2)  # (n, n)
            # Extract upper triangle (excluding diagonal)
            upper_tri_idx = np.triu_indices(len(motif_centers), k=1)
            distances = dist_matrix[upper_tri_idx]
            
            features['texture_motif_dist_std'] = float(np.std(distances))
            features['texture_motif_regularity'] = float(
                1.0 / (np.std(distances) / (np.mean(distances) + 1e-10) + 1e-10)
            )
        else:
            features['texture_motif_dist_std'] = 0.0
            features['texture_motif_regularity'] = 0.0
    else:
        features['texture_motif_dist_std'] = 0.0
        features['texture_motif_regularity'] = 0.0
    
    return features


def compute_latent_repetition_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect latent-space repetition / tiled decoding grids.
    
    Uses autocorrelation peaks of high-pass texture to estimate dominant repetition periods.
    """
    features = {
        'latent_repeat_peak1_dist': 0.0,
        'latent_repeat_peak2_dist': 0.0,
        'latent_repeat_peak3_dist': 0.0,
        'latent_repeat_peak1_strength': 0.0,
        'latent_repeat_peak2_strength': 0.0,
        'latent_repeat_peak3_strength': 0.0,
        'latent_repeat_grid_strength': 0.0,
        'latent_repeat_peak_count': 0.0,
    }

    gray = precomputed.gray
    gray_ds, scale_back = _downsample_gray(gray, max_dim=256)
    h, w = gray_ds.shape
    if min(h, w) < 32:
        return features

    # High-pass texture
    low_pass = cv2.GaussianBlur(gray_ds.astype(np.float32), (13, 13), 2).astype(gray_ds.dtype)
    texture = gray_ds - low_pass
    texture_centered = texture - np.mean(texture)

    # Autocorrelation via FFT
    fft = _fast_fft2(texture_centered)
    autocorr = _fast_ifft2(fft * np.conj(fft)).real
    autocorr = _fast_fftshift(autocorr)
    if autocorr.max() > 0:
        autocorr = autocorr / autocorr.max()

    cy, cx = h // 2, w // 2
    center_size = max(4, min(10, min(h, w) // 20))
    autocorr_masked = autocorr.copy()
    autocorr_masked[cy-center_size:cy+center_size, cx-center_size:cx+center_size] = 0

    # Peak detection
    peak_threshold = 0.25
    max_f = ndimage.maximum_filter(autocorr_masked, size=5)
    peaks = (autocorr_masked == max_f) & (autocorr_masked > peak_threshold)
    coords = np.argwhere(peaks)

    if coords.size == 0:
        return features

    # Distances and strengths
    dists = []
    strengths = []
    axis_strengths = []
    for y, x in coords:
        dy = y - cy
        dx = x - cx
        dist = np.sqrt(dy * dy + dx * dx)
        if dist < 1:
            continue
        strength = autocorr_masked[y, x]
        dists.append(dist)
        strengths.append(strength)
        if abs(dx) <= 2 or abs(dy) <= 2:
            axis_strengths.append(strength)

    if not dists:
        return features

    # Sort by strength
    order = np.argsort(strengths)[::-1]
    dists = np.array(dists)[order]
    strengths = np.array(strengths)[order]

    # Scale distances back to original pixel units
    dists_px = dists * scale_back

    features['latent_repeat_peak_count'] = float(len(dists))
    features['latent_repeat_peak1_dist'] = float(dists_px[0])
    features['latent_repeat_peak1_strength'] = float(strengths[0])
    if len(dists_px) > 1:
        features['latent_repeat_peak2_dist'] = float(dists_px[1])
        features['latent_repeat_peak2_strength'] = float(strengths[1])
    if len(dists_px) > 2:
        features['latent_repeat_peak3_dist'] = float(dists_px[2])
        features['latent_repeat_peak3_strength'] = float(strengths[2])

    if axis_strengths:
        features['latent_repeat_grid_strength'] = float(np.mean(axis_strengths))

    return features


def compute_near_far_patch_similarity(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Compare similarity of nearby vs far patches (proxy for cross-attention leakage).
    """
    features = {
        'near_patch_sim_mean': 0.0,
        'near_patch_sim_std': 0.0,
        'far_patch_sim_mean': 0.0,
        'far_patch_sim_std': 0.0,
        'cross_attention_leakage_score': 0.0,
    }

    gray = precomputed.gray
    gray_ds, _ = _downsample_gray(gray, max_dim=256)
    h, w = gray_ds.shape
    if min(h, w) < 64:
        return features

    patch_size = 16
    stride = 16
    patches, positions = extract_patches(gray_ds, patch_size=patch_size, stride=stride, max_patches=200)
    if len(patches) < 20:
        return features

    # Normalize patches for cosine similarity
    patches = patches - patches.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(patches, axis=1, keepdims=True) + 1e-10
    patches = patches / norms

    pos = np.array(positions)
    near_sims = []
    far_sims = []

    rng = np.random.default_rng(42)
    idxs = rng.choice(len(patches), size=min(40, len(patches)), replace=False)
    for idx in idxs:
        p = patches[idx]
        y, x = pos[idx]
        # Near: within 2 strides
        dist = np.sqrt(((pos - [y, x]) ** 2).sum(axis=1))
        near_idx = np.where((dist > 0) & (dist <= stride * 2))[0]
        far_idx = np.where(dist >= stride * 6)[0]

        if len(near_idx) > 0:
            j = near_idx[rng.integers(0, len(near_idx))]
            near_sims.append(float(np.dot(p, patches[j])))
        if len(far_idx) > 0:
            j = far_idx[rng.integers(0, len(far_idx))]
            far_sims.append(float(np.dot(p, patches[j])))

    if near_sims:
        features['near_patch_sim_mean'] = float(np.mean(near_sims))
        features['near_patch_sim_std'] = float(np.std(near_sims))
    if far_sims:
        features['far_patch_sim_mean'] = float(np.mean(far_sims))
        features['far_patch_sim_std'] = float(np.std(far_sims))
        features['cross_attention_leakage_score'] = float(
            features['far_patch_sim_mean'] / (features['near_patch_sim_mean'] + 1e-10)
        )

    return features


def compute_micro_repetition_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect repeated micro-patterns using tiny patches on a downsampled image.
    """
    features = {
        'micro_patch_nn_dist_mean': 0.0,
        'micro_patch_nn_dist_std': 0.0,
        'micro_patch_repetition_score': 0.0,
    }

    gray = precomputed.gray
    h, w = gray.shape

    # Downsample for speed (target ~128px)
    step = max(1, max(h, w) // 128)
    gray_small = gray[::step, ::step]

    patches, _ = extract_patches(gray_small, patch_size=4, stride=4, max_patches=300)
    if len(patches) < 10:
        return features

    n = len(patches)
    n_samples = min(80, n)
    # Use fixed seed for reproducibility
    rng = np.random.RandomState(42)
    sample_idx = rng.choice(n, n_samples, replace=False)
    sample_patches = patches[sample_idx]

    patches = np.asarray(patches, dtype=np.float64)
    sample_patches = np.asarray(sample_patches, dtype=np.float64)

    if HAS_CYTHON_PATCHES:
        nn_distances = fast_patch_distances_cy(
            sample_patches, patches, sample_idx.astype(np.int64)
        )
    elif HAS_JIT_PATCHES:
        nn_distances = fast_patch_nn_distances(
            sample_patches, patches, sample_idx.astype(np.int64)
        )
    else:
        # Vectorized nearest-neighbor distances
        a2 = np.sum(sample_patches * sample_patches, axis=1, keepdims=True)
        b2 = np.sum(patches * patches, axis=1)
        d2 = a2 + b2[None, :] - 2.0 * (sample_patches @ patches.T)
        d2 = np.maximum(d2, 0.0)
        d2[np.arange(n_samples), sample_idx] = np.inf
        nn_distances = np.sqrt(np.min(d2, axis=1))
    features['micro_patch_nn_dist_mean'] = float(np.mean(nn_distances))
    features['micro_patch_nn_dist_std'] = float(np.std(nn_distances))

    similarity_threshold = features['micro_patch_nn_dist_mean'] * 0.3
    features['micro_patch_repetition_score'] = float(np.mean(nn_distances < similarity_threshold))

    return features


def extract_self_similarity_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all self-similarity and repetition features.
    
    Main entry point for the module.
    """
    features = {}
    
    # Patch distance analysis
    try:
        features.update(compute_patch_distance_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing patch distance features: {e}")
    
    # Spatial repetition
    try:
        features.update(compute_spatial_repetition_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing spatial repetition: {e}")
    
    # Cross-scale similarity
    try:
        features.update(compute_cross_scale_similarity_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing cross-scale similarity: {e}")
    
    # Texture repetition
    try:
        features.update(compute_texture_repetition_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing texture repetition: {e}")

    # Micro-pattern repetition
    try:
        features.update(compute_micro_repetition_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing micro repetition: {e}")

    # Latent-space repetition / tiled decoding grids
    try:
        features.update(compute_latent_repetition_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing latent repetition: {e}")

    # Cross-attention leakage proxy (near vs far patch similarity)
    try:
        features.update(compute_near_far_patch_similarity(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing patch similarity leakage: {e}")
    
    return features
