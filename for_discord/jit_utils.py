"""
jit_utils.py - JIT-compiled utility functions for fast feature extraction.

Uses numba for performance-critical operations.

OPTIMIZATIONS:
- JIT-compiled functions for compute-intensive operations
- Uses scipy's C-optimized distance_transform_edt when available (10-100x faster)
- Uses scipy.fft (pocketfft) when available for FFT operations
- Provides fast_corrcoef for pairwise correlation (faster than np.corrcoef matrix)
"""

import numpy as np
from numba import jit, prange
from typing import Tuple


@jit(nopython=True, cache=True, fastmath=True)
def fast_histogram_1d(data: np.ndarray, bins: int, min_val: float, max_val: float) -> np.ndarray:
    """Fast 1D histogram using JIT.
    
    NOTE: For most use cases, np.histogram is faster as it's implemented in C.
    This function is useful when you need to call histogram in a JIT context.
    """
    hist = np.zeros(bins, dtype=np.float64)
    scale = bins / (max_val - min_val + 1e-10)
    
    for i in range(data.shape[0]):
        val = data[i]
        if val >= min_val and val <= max_val:
            idx = int((val - min_val) * scale)
            if idx >= bins:
                idx = bins - 1
            hist[idx] += 1
    
    return hist


@jit(nopython=True, cache=True, fastmath=True)
def fast_moments(data: np.ndarray) -> Tuple[float, float, float, float]:
    """Compute mean, std, skewness, kurtosis in one pass."""
    n = data.shape[0]
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0
    
    # First pass: mean
    mean = 0.0
    for i in range(n):
        mean += data[i]
    mean /= n
    
    # Second pass: variance, skewness, kurtosis
    m2 = 0.0
    m3 = 0.0
    m4 = 0.0
    
    for i in range(n):
        diff = data[i] - mean
        d2 = diff * diff
        m2 += d2
        m3 += d2 * diff
        m4 += d2 * d2
    
    m2 /= n
    m3 /= n
    m4 /= n
    
    std = np.sqrt(m2)
    
    if std < 1e-10:
        return mean, std, 0.0, 0.0
    
    skewness = m3 / (m2 ** 1.5 + 1e-10)
    kurtosis = m4 / (m2 ** 2 + 1e-10) - 3.0
    
    return mean, std, skewness, kurtosis


@jit(nopython=True, cache=True, fastmath=True)
def fast_skewness(data: np.ndarray) -> float:
    """Fast skewness computation."""
    n = data.shape[0]
    if n < 3:
        return 0.0
    
    mean = 0.0
    for i in range(n):
        mean += data[i]
    mean /= n
    
    m2 = 0.0
    m3 = 0.0
    for i in range(n):
        diff = data[i] - mean
        d2 = diff * diff
        m2 += d2
        m3 += d2 * diff
    
    m2 /= n
    m3 /= n
    
    if m2 < 1e-10:
        return 0.0
    
    return m3 / (m2 ** 1.5 + 1e-10)


@jit(nopython=True, cache=True, fastmath=True)
def fast_kurtosis(data: np.ndarray) -> float:
    """Fast kurtosis computation."""
    n = data.shape[0]
    if n < 4:
        return 0.0
    
    mean = 0.0
    for i in range(n):
        mean += data[i]
    mean /= n
    
    m2 = 0.0
    m4 = 0.0
    for i in range(n):
        diff = data[i] - mean
        d2 = diff * diff
        m2 += d2
        m4 += d2 * d2
    
    m2 /= n
    m4 /= n
    
    if m2 < 1e-10:
        return 0.0
    
    return m4 / (m2 ** 2 + 1e-10) - 3.0


@jit(nopython=True, cache=True, fastmath=True)
def fast_sobel_magnitude(gray: np.ndarray) -> np.ndarray:
    """Fast Sobel gradient magnitude."""
    h, w = gray.shape
    result = np.zeros((h, w), dtype=np.float64)
    
    for i in range(1, h - 1):
        for j in range(1, w - 1):
            # Sobel X
            gx = (-gray[i-1, j-1] + gray[i-1, j+1]
                  - 2*gray[i, j-1] + 2*gray[i, j+1]
                  - gray[i+1, j-1] + gray[i+1, j+1])
            
            # Sobel Y
            gy = (-gray[i-1, j-1] - 2*gray[i-1, j] - gray[i-1, j+1]
                  + gray[i+1, j-1] + 2*gray[i+1, j] + gray[i+1, j+1])
            
            result[i, j] = np.sqrt(gx*gx + gy*gy)
    
    return result


@jit(nopython=True, cache=True, parallel=True, fastmath=True)
def fast_local_std_2d(gray: np.ndarray, size: int) -> np.ndarray:
    """Fast local standard deviation with parallelization."""
    h, w = gray.shape
    half = size // 2
    result = np.zeros((h, w), dtype=np.float64)
    
    for i in prange(half, h - half):
        for j in range(half, w - half):
            # Compute local mean and std
            s = 0.0
            s2 = 0.0
            count = 0
            
            for di in range(-half, half + 1):
                for dj in range(-half, half + 1):
                    val = gray[i + di, j + dj]
                    s += val
                    s2 += val * val
                    count += 1
            
            mean = s / count
            variance = s2 / count - mean * mean
            if variance > 0:
                result[i, j] = np.sqrt(variance)
    
    return result


@jit(nopython=True, cache=True, fastmath=True)
def fast_rgb_to_saturation(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Fast RGB to saturation conversion."""
    h, w = r.shape
    result = np.zeros((h, w), dtype=np.float64)
    
    for i in range(h):
        for j in range(w):
            rv, gv, bv = r[i, j], g[i, j], b[i, j]
            max_val = max(rv, max(gv, bv))
            min_val = min(rv, min(gv, bv))
            
            if max_val > 1e-10:
                result[i, j] = (max_val - min_val) / max_val
    
    return result


@jit(nopython=True, cache=True, fastmath=True)
def fast_glcm_features(gray_int: np.ndarray, levels: int = 16) -> Tuple[float, float, float, float]:
    """Fast GLCM feature computation (contrast, homogeneity, energy, correlation)."""
    h, w = gray_int.shape
    
    # Compute GLCM for horizontal adjacency (dx=1, dy=0)
    glcm = np.zeros((levels, levels), dtype=np.float64)
    
    for i in range(h):
        for j in range(w - 1):
            a = gray_int[i, j]
            b = gray_int[i, j + 1]
            if 0 <= a < levels and 0 <= b < levels:
                glcm[a, b] += 1
                glcm[b, a] += 1  # Symmetric
    
    # Normalize
    total = np.sum(glcm)
    if total > 0:
        glcm /= total
    
    # Compute features
    contrast = 0.0
    homogeneity = 0.0
    energy = 0.0
    
    mean_i = 0.0
    mean_j = 0.0
    
    for i in range(levels):
        for j in range(levels):
            p = glcm[i, j]
            contrast += p * (i - j) ** 2
            homogeneity += p / (1 + abs(i - j))
            energy += p ** 2
            mean_i += i * p
            mean_j += j * p
    
    # Correlation
    std_i = 0.0
    std_j = 0.0
    for i in range(levels):
        for j in range(levels):
            p = glcm[i, j]
            std_i += p * (i - mean_i) ** 2
            std_j += p * (j - mean_j) ** 2
    
    std_i = np.sqrt(std_i)
    std_j = np.sqrt(std_j)
    
    correlation = 0.0
    if std_i > 1e-10 and std_j > 1e-10:
        for i in range(levels):
            for j in range(levels):
                correlation += glcm[i, j] * (i - mean_i) * (j - mean_j) / (std_i * std_j)
    
    return contrast, homogeneity, energy, correlation


@jit(nopython=True, cache=True, fastmath=True)
def fast_block_boundary_energy(gray: np.ndarray, block_size: int = 8) -> float:
    """Compute energy at block boundaries (JPEG artifact detection)."""
    h, w = gray.shape
    boundary_sum = 0.0
    non_boundary_sum = 0.0
    boundary_count = 0
    non_boundary_count = 0
    
    for i in range(1, h):
        for j in range(w):
            diff = abs(gray[i, j] - gray[i-1, j])
            if i % block_size == 0:
                boundary_sum += diff
                boundary_count += 1
            else:
                non_boundary_sum += diff
                non_boundary_count += 1
    
    for i in range(h):
        for j in range(1, w):
            diff = abs(gray[i, j] - gray[i, j-1])
            if j % block_size == 0:
                boundary_sum += diff
                boundary_count += 1
            else:
                non_boundary_sum += diff
                non_boundary_count += 1
    
    if boundary_count == 0 or non_boundary_count == 0:
        return 0.0
    
    boundary_avg = boundary_sum / boundary_count
    non_boundary_avg = non_boundary_sum / non_boundary_count
    
    if non_boundary_avg < 1e-10:
        return 0.0
    
    return boundary_avg / non_boundary_avg


@jit(nopython=True, cache=True, fastmath=True)
def fast_edge_coherence(edges: np.ndarray, threshold: float) -> float:
    """Compute edge direction coherence."""
    h, w = edges.shape
    
    # Count edge pixels and their neighbors
    edge_count = 0
    neighbor_count = 0
    
    for i in range(1, h - 1):
        for j in range(1, w - 1):
            if edges[i, j] > threshold:
                edge_count += 1
                # Check 8-connected neighbors
                for di in range(-1, 2):
                    for dj in range(-1, 2):
                        if di != 0 or dj != 0:
                            if edges[i + di, j + dj] > threshold:
                                neighbor_count += 1
    
    if edge_count == 0:
        return 0.0
    
    return neighbor_count / (edge_count * 8)


@jit(nopython=True, cache=True, fastmath=True)
def fast_percentile(data: np.ndarray, p: float) -> float:
    """Fast approximate percentile."""
    sorted_data = np.sort(data.flatten())
    n = len(sorted_data)
    if n == 0:
        return 0.0
    idx = int(p / 100.0 * (n - 1))
    return sorted_data[idx]


@jit(nopython=True, cache=True, fastmath=True)
def fast_entropy(hist: np.ndarray) -> float:
    """Compute entropy from histogram (any shape, auto-flattened)."""
    flat = hist.ravel()
    n = len(flat)
    total = 0.0
    for i in range(n):
        total = total + flat[i]
    if total == 0.0:
        return 0.0
    
    log2_inv = 1.0 / np.log(2.0)
    entropy = 0.0
    for i in range(n):
        p = flat[i] / total
        if p > 1e-10:
            entropy = entropy - p * np.log(p) * log2_inv
    
    return entropy


@jit(nopython=True, cache=True, fastmath=True)
def _fast_distance_transform_chamfer(binary: np.ndarray) -> np.ndarray:
    """Approximate distance transform (chamfer) - JIT fallback.
    
    NOTE: This is slower than scipy.ndimage.distance_transform_edt.
    Use fast_distance_transform_approx() which automatically uses
    the faster scipy version when available.
    """
    h, w = binary.shape
    dist = np.full((h, w), 1e10, dtype=np.float64)
    
    # Initialize with 0 for True pixels
    for i in range(h):
        for j in range(w):
            if binary[i, j]:
                dist[i, j] = 0.0
    
    # Forward pass
    for i in range(1, h):
        for j in range(1, w - 1):
            dist[i, j] = min(dist[i, j],
                            dist[i-1, j-1] + 1.414,
                            dist[i-1, j] + 1.0,
                            dist[i-1, j+1] + 1.414,
                            dist[i, j-1] + 1.0)
    
    # Backward pass
    for i in range(h - 2, -1, -1):
        for j in range(w - 2, 0, -1):
            dist[i, j] = min(dist[i, j],
                            dist[i+1, j+1] + 1.414,
                            dist[i+1, j] + 1.0,
                            dist[i+1, j-1] + 1.414,
                            dist[i, j+1] + 1.0)
    
    return dist


# Check if scipy is available for fast distance transform
try:
    from scipy.ndimage import distance_transform_edt as _scipy_edt
    _HAS_SCIPY_EDT = True
except ImportError:
    _HAS_SCIPY_EDT = False


def fast_distance_transform_approx(binary: np.ndarray) -> np.ndarray:
    """Fast distance transform - uses scipy's C-optimized version when available.
    
    scipy.ndimage.distance_transform_edt is implemented in C and is
    significantly faster (often 10-100x) than the numba JIT chamfer
    approximation, especially for large images.
    """
    if _HAS_SCIPY_EDT:
        # scipy's edt is exact and much faster (C implementation)
        return _scipy_edt(binary)
    else:
        # Fallback to JIT chamfer approximation
        return _fast_distance_transform_chamfer(binary)


@jit(nopython=True, cache=True, fastmath=True)
def fast_corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    """Fast correlation between two 1D arrays."""
    if a.size < 2 or b.size < 2:
        return 0.0
    
    n = a.shape[0]
    
    mean_a = 0.0
    mean_b = 0.0
    for i in range(n):
        mean_a += a[i]
        mean_b += b[i]
    mean_a /= n
    mean_b /= n
    
    num = 0.0
    den_a = 0.0
    den_b = 0.0
    
    for i in range(n):
        da = a[i] - mean_a
        db = b[i] - mean_b
        num += da * db
        den_a += da * da
        den_b += db * db
        
    den = np.sqrt(den_a * den_b)
    if den < 1e-10:
        return 0.0
    
    return num / den

def safe_corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    """
    Safe pairwise correlation that handles NaN and edge cases.
    
    This is faster than np.corrcoef(a, b)[0, 1] because:
    1. It doesn't compute the full correlation matrix
    2. Uses JIT-compiled fast_corrcoef for the actual computation
    
    Args:
        a: First array (will be flattened)
        b: Second array (will be flattened)
        
    Returns:
        Pearson correlation coefficient, or 0.0 if computation fails
    """
    try:
        a_flat = np.asarray(a, dtype=np.float64).ravel()
        b_flat = np.asarray(b, dtype=np.float64).ravel()
        
        if len(a_flat) != len(b_flat):
            min_len = min(len(a_flat), len(b_flat))
            a_flat = a_flat[:min_len]
            b_flat = b_flat[:min_len]
        
        if len(a_flat) < 2:
            return 0.0
        
        # Check for NaN
        mask = ~(np.isnan(a_flat) | np.isnan(b_flat))
        if np.sum(mask) < 2:
            return 0.0
        
        if not np.all(mask):
            a_flat = a_flat[mask]
            b_flat = b_flat[mask]
        
        result = fast_corrcoef(a_flat, b_flat)
        
        if np.isnan(result) or np.isinf(result):
            return 0.0
        
        return float(result)
    except Exception:
        return 0.0


# ============================================================================
# Additional JIT-compiled functions for performance
# ============================================================================

@jit(nopython=True, cache=True, parallel=True, fastmath=True)
def fast_radial_profile(autocorr: np.ndarray, max_r: int, center_size: int) -> np.ndarray:
    """
    Compute radial average profile of a 2D autocorrelation map.
    
    Much faster than np.bincount approach for large images.
    Returns profile from center_size to max_r.
    """
    h, w = autocorr.shape
    cy, cx = h // 2, w // 2
    
    radial_sum = np.zeros(max_r, dtype=np.float64)
    radial_count = np.zeros(max_r, dtype=np.float64)
    
    for i in prange(h):
        for j in range(w):
            dx = j - cx
            dy = i - cy
            r = int(np.sqrt(dx * dx + dy * dy))
            if r < max_r:
                radial_sum[r] += autocorr[i, j]
                radial_count[r] += 1.0
    
    # Compute means, skip center
    result_len = max_r - center_size
    if result_len <= 0:
        return np.zeros(1, dtype=np.float64)
    
    result = np.zeros(result_len, dtype=np.float64)
    for r in range(center_size, max_r):
        if radial_count[r] > 0:
            result[r - center_size] = radial_sum[r] / radial_count[r]
    
    return result


@jit(nopython=True, cache=True, parallel=True, fastmath=True)
def fast_patch_nn_distances(
    sample_patches: np.ndarray,
    all_patches: np.ndarray,
    sample_idx: np.ndarray,
) -> np.ndarray:
    """
    Compute nearest-neighbor distances from sample patches to all patches.
    
    Uses ||a-b||^2 = ||a||^2 + ||b||^2 - 2*a.b expansion with parallelization.
    Returns array of NN distances for each sample patch.
    """
    n_samples = sample_patches.shape[0]
    n_all = all_patches.shape[0]
    dim = sample_patches.shape[1]
    
    nn_dists = np.empty(n_samples, dtype=np.float64)
    
    # Precompute ||b||^2
    b2 = np.empty(n_all, dtype=np.float64)
    for j in range(n_all):
        s = 0.0
        for d in range(dim):
            s += all_patches[j, d] * all_patches[j, d]
        b2[j] = s
    
    for i in prange(n_samples):
        # Compute ||a||^2
        a2 = 0.0
        for d in range(dim):
            a2 += sample_patches[i, d] * sample_patches[i, d]
        
        min_dist2 = 1e30
        self_idx = sample_idx[i]
        
        for j in range(n_all):
            if j == self_idx:
                continue
            # dot product
            dot = 0.0
            for d in range(dim):
                dot += sample_patches[i, d] * all_patches[j, d]
            d2 = a2 + b2[j] - 2.0 * dot
            if d2 < 0.0:
                d2 = 0.0
            if d2 < min_dist2:
                min_dist2 = d2
        
        nn_dists[i] = np.sqrt(min_dist2)
    
    return nn_dists


@jit(nopython=True, cache=True, parallel=True, fastmath=True)
def fast_window_stats(data: np.ndarray, window_size: int) -> np.ndarray:
    """
    Compute sliding window mean, std, min, max over a 1D array.
    
    Returns (n_windows, 4) array with [mean, std, min, max] per window.
    Useful for radial profile analysis, spectral analysis, etc.
    """
    n = len(data)
    if n < window_size or window_size < 1:
        result = np.zeros((1, 4), dtype=np.float64)
        if n > 0:
            result[0, 0] = np.mean(data)
            result[0, 1] = np.std(data)
            result[0, 2] = np.min(data)
            result[0, 3] = np.max(data)
        return result
    
    n_windows = n - window_size + 1
    result = np.zeros((n_windows, 4), dtype=np.float64)
    
    for w in prange(n_windows):
        s = 0.0
        s2 = 0.0
        mn = data[w]
        mx = data[w]
        for i in range(window_size):
            v = data[w + i]
            s += v
            s2 += v * v
            if v < mn:
                mn = v
            if v > mx:
                mx = v
        mean = s / window_size
        var = s2 / window_size - mean * mean
        if var < 0.0:
            var = 0.0
        result[w, 0] = mean
        result[w, 1] = np.sqrt(var)
        result[w, 2] = mn
        result[w, 3] = mx
    
    return result