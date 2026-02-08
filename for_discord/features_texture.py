"""
features_texture.py - Edge and texture coherence features for AI detection.

Analyzes texture patterns using LBP, GLCM, and edge statistics
to detect AI-generated image characteristics.
"""

import numpy as np
from scipy import ndimage
from scipy.ndimage import uniform_filter
from typing import Dict, List, Tuple

# GPU acceleration utilities
from gpu_utils import gpu_sobel, gpu_convolve, gpu_uniform_filter, gpu_binary_dilation

# Optional OpenCV acceleration
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Try to import Cython GLCM (fastest)
try:
    from cython_ext.fast_glcm import fast_glcm_cy
    HAS_CYTHON_GLCM = True
except ImportError:
    HAS_CYTHON_GLCM = False

# Try to import JIT utilities for acceleration
try:
    from jit_utils import (
        fast_sobel_magnitude,
        fast_glcm_features,
        fast_corrcoef,
        fast_entropy,
        fast_local_std_2d,
        fast_rgb_to_saturation,
    )
    HAS_JIT = True
except ImportError:
    HAS_JIT = False


def _entropy(hist):
    """Fast histogram entropy in bits."""
    if HAS_JIT:
        return float(fast_entropy(hist))
    return float(-np.sum(hist * np.log2(hist + 1e-10)))


def _corrcoef(a, b):
    """Fast pairwise Pearson correlation."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2 or b.size < 2:
        return 0.0
    if HAS_JIT:
        return float(fast_corrcoef(a, b))
    r = np.corrcoef(a, b)[0, 1]
    return float(r) if not np.isnan(r) else 0.0


def compute_edge_features(
    gray: np.ndarray,
    *,
    edges: np.ndarray = None,
    sobel_h: np.ndarray = None,
    sobel_v: np.ndarray = None,
    precomputed = None,
) -> Dict[str, float]:
    """
    Compute edge-based features using gradient analysis.

    Args:
        gray: Grayscale image as float64
        edges: Precomputed edge magnitude (optional)
        sobel_h: Precomputed horizontal Sobel gradient (optional)
        sobel_v: Precomputed vertical Sobel gradient (optional)
        precomputed: ImagePrecomputedData instance (optional)
    """
    features = {}

    # Use precomputed values if available, otherwise compute
    if edges is not None and sobel_h is not None and sobel_v is not None:
        gradient_mag = edges
    else:
        # Sobel gradients - prefer OpenCV when available for speed
        if HAS_CV2:
            g = gray.astype(np.float32)
            sobel_h = cv2.Sobel(g, cv2.CV_64F, 1, 0, ksize=3)
            sobel_v = cv2.Sobel(g, cv2.CV_64F, 0, 1, ksize=3)
            gradient_mag = np.sqrt(sobel_h**2 + sobel_v**2)
        elif HAS_JIT:
            gradient_mag = fast_sobel_magnitude(gray.astype(np.float64))
            # Compute direction separately (still needed)
            sobel_h = gpu_sobel(gray, axis=0)
            sobel_v = gpu_sobel(gray, axis=1)
        else:
            sobel_h = gpu_sobel(gray, axis=0)
            sobel_v = gpu_sobel(gray, axis=1)
            gradient_mag = np.sqrt(sobel_h**2 + sobel_v**2)
    
    # Edge statistics
    features['edge_mean'] = float(np.mean(gradient_mag))
    features['edge_std'] = float(np.std(gradient_mag))
    features['edge_max'] = float(np.max(gradient_mag))
    features['edge_energy'] = float(np.sum(gradient_mag**2))
    
    # Edge density (percentage of strong edges) - use sorted array instead of percentile
    flat_mag = gradient_mag.ravel()
    sorted_mag = np.sort(flat_mag)
    edge_threshold = sorted_mag[int(len(sorted_mag) * 0.9)]
    features['edge_density'] = float(np.mean(gradient_mag > edge_threshold))
    
    # Gradient direction statistics
    gradient_dir = precomputed.gradient_direction
    
    # Direction histogram entropy - use bincount for speed
    n_bins = 36
    bin_indices = ((gradient_dir + np.pi) * (n_bins / (2 * np.pi))).astype(np.int32)
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)
    dir_hist = np.bincount(bin_indices.ravel(), minlength=n_bins).astype(np.float64)
    dir_hist = dir_hist / (dir_hist.sum() + 1e-10)
    features['edge_dir_entropy'] = _entropy(dir_hist)
    
    # Dominant direction
    features['edge_dominant_dir'] = float(np.argmax(dir_hist) * 10 - 180)
    
    # Direction uniformity (AI often has more uniform directions)
    features['edge_dir_uniformity'] = float(np.max(dir_hist))
    
    return features


def compute_canny_features(
    gray: np.ndarray,
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Extract features from Canny edge detection.

    Args:
        gray: Grayscale image
        precomputed: Optional ImagePrecomputedData instance (currently unused but available for future optimization)
    """
    features = {}
    
    try:
        gray_uint8 = precomputed.gray_uint8
        
        # Apply Canny with different thresholds
        edges_low = cv2.Canny(gray_uint8, 50, 150)
        edges_high = cv2.Canny(gray_uint8, 100, 200)
        
        # Edge pixel ratios
        features['canny_low_ratio'] = float(np.mean(edges_low > 0))
        features['canny_high_ratio'] = float(np.mean(edges_high > 0))
        features['canny_threshold_ratio'] = float(features['canny_low_ratio'] / (features['canny_high_ratio'] + 1e-10))
        
        # Connected components analysis - use cv2.connectedComponentsWithStats for efficiency
        num_labels_low, labels_low, stats_low, _ = cv2.connectedComponentsWithStats(edges_low, connectivity=8)
        num_labels_high, _, _, _ = cv2.connectedComponentsWithStats(edges_high, connectivity=8)
        
        features['canny_num_components_low'] = float(num_labels_low - 1)  # Exclude background
        features['canny_num_components_high'] = float(num_labels_high - 1)
        
        # Average component size - use stats directly (area is at index 4)
        if num_labels_low > 1:
            # stats: x, y, width, height, area for each component
            component_sizes = stats_low[1:, cv2.CC_STAT_AREA]  # Skip background (index 0)
            features['canny_avg_component_size'] = float(np.mean(component_sizes))
            features['canny_component_size_std'] = float(np.std(component_sizes))
        else:
            features['canny_avg_component_size'] = 0.0
            features['canny_component_size_std'] = 0.0
            
    except Exception:
        # Fallback without cv2
        features['canny_low_ratio'] = 0.0
        features['canny_high_ratio'] = 0.0
        features['canny_threshold_ratio'] = 1.0
        features['canny_num_components_low'] = 0.0
        features['canny_num_components_high'] = 0.0
        features['canny_avg_component_size'] = 0.0
        features['canny_component_size_std'] = 0.0
    
    return features


def compute_lbp_features(
    gray: np.ndarray,
    radius: int = 1,
    n_points: int = 8,
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Compute Local Binary Pattern (LBP) features.

    LBP captures local texture patterns. AI images often have
    different LBP distributions than real photographs.

    Args:
        gray: Grayscale image
        radius: LBP radius
        n_points: Number of points in LBP
        precomputed: Optional ImagePrecomputedData instance (currently unused but available for future optimization)
    """
    features = {}
    
    gray_uint8 = precomputed.gray_uint8
    
    try:
        from gpu_utils import fast_lbp_uniform
        
        # OPTIMIZATION: Use fast vectorized LBP (2.5x faster than skimage, exact match)
        lbp = fast_lbp_uniform(gray_uint8)
        
        # Cache for reuse in compute_clbp_features
        precomputed._lbp_uniform = lbp
        
        # LBP histogram
        n_bins = n_points + 2  # For uniform LBP
        # OPTIMIZATION: bincount is faster than histogram for integer LBP values (0..n_bins-1)
        lbp_hist = np.bincount(lbp.ravel(), minlength=n_bins)[:n_bins].astype(np.float64)
        lbp_hist = lbp_hist / (lbp_hist.sum() + 1e-10)
        
        # Histogram statistics
        features['lbp_entropy'] = _entropy(lbp_hist)
        features['lbp_uniformity'] = float(np.sum(lbp_hist**2))
        features['lbp_max_bin'] = float(np.max(lbp_hist))
        
        # Non-uniform patterns (often higher in real photos)
        features['lbp_nonuniform_ratio'] = float(lbp_hist[-1])
        
        # Skip additional LBP scales for speed - they add marginal value
        features['lbp2_entropy'] = 0.0
        features['lbp2_uniformity'] = 0.0
        features['lbp3_entropy'] = 0.0
        features['lbp3_uniformity'] = 0.0
            
    except ImportError:
        # Vectorized fallback LBP implementation (no Python loops)
        h, w = gray.shape
        center = gray_uint8[1:-1, 1:-1]
        
        # Vectorized LBP computation
        lbp = np.zeros((h-2, w-2), dtype=np.uint8)
        lbp |= ((gray_uint8[:-2, :-2] >= center).astype(np.uint8) << 7)
        lbp |= ((gray_uint8[:-2, 1:-1] >= center).astype(np.uint8) << 6)
        lbp |= ((gray_uint8[:-2, 2:] >= center).astype(np.uint8) << 5)
        lbp |= ((gray_uint8[1:-1, 2:] >= center).astype(np.uint8) << 4)
        lbp |= ((gray_uint8[2:, 2:] >= center).astype(np.uint8) << 3)
        lbp |= ((gray_uint8[2:, 1:-1] >= center).astype(np.uint8) << 2)
        lbp |= ((gray_uint8[2:, :-2] >= center).astype(np.uint8) << 1)
        lbp |= ((gray_uint8[1:-1, :-2] >= center).astype(np.uint8) << 0)
        
        lbp_hist = np.bincount(lbp.ravel(), minlength=256)[:256].astype(np.float64)
        lbp_hist = lbp_hist / (lbp_hist.sum() + 1e-10)
        
        features['lbp_entropy'] = _entropy(lbp_hist)
        features['lbp_uniformity'] = float(np.sum(lbp_hist**2))
        features['lbp_max_bin'] = float(np.max(lbp_hist))
        features['lbp_nonuniform_ratio'] = 0.0
        features['lbp2_entropy'] = 0.0
        features['lbp2_uniformity'] = 0.0
        features['lbp3_entropy'] = 0.0
        features['lbp3_uniformity'] = 0.0
    
    return features


def compute_glcm_features(
    gray: np.ndarray,
    distances: List[int] = [1, 2, 5],
    angles: List[float] = [0, np.pi/4, np.pi/2, 3*np.pi/4],
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Compute Gray-Level Co-occurrence Matrix (GLCM) features.

    GLCM captures texture relationships. AI images often show
    different GLCM statistics than real photos.

    Args:
        gray: Grayscale image
        distances: Distances for GLCM computation
        angles: Angles for GLCM computation
        precomputed: Optional ImagePrecomputedData instance (currently unused but available for future optimization)
    """
    features = {}
    
    # Use JIT GLCM for speed if available
    gray_uint8 = precomputed.gray_uint8

    if HAS_CYTHON_GLCM or HAS_JIT:
        # Quantize to 16 levels for fast path
        levels = 16
        gray_quantized = (gray_uint8 / 256 * levels).astype(np.int32)
        gray_quantized = np.clip(gray_quantized, 0, levels - 1)
        
        if HAS_CYTHON_GLCM:
            contrast, homogeneity, energy, correlation = fast_glcm_cy(gray_quantized, levels)
        else:
            contrast, homogeneity, energy, correlation = fast_glcm_features(gray_quantized, levels)
        
        features['glcm_contrast_mean'] = float(contrast)
        features['glcm_contrast_std'] = 0.0
        features['glcm_homogeneity_mean'] = float(homogeneity)
        features['glcm_homogeneity_std'] = 0.0
        features['glcm_energy_mean'] = float(energy)
        features['glcm_energy_std'] = 0.0
        features['glcm_correlation_mean'] = float(correlation)
        features['glcm_correlation_std'] = 0.0
        features['glcm_dissimilarity_mean'] = 0.0  # Not computed in JIT version
        features['glcm_dissimilarity_std'] = 0.0
        features['glcm_ASM_mean'] = float(energy)  # ASM = energy
        features['glcm_ASM_std'] = 0.0
        features['glcm_contrast_angle_var'] = 0.0
        features['glcm_homogeneity_angle_var'] = 0.0
        # Haralick extras not available in fast path
        features['haralick_entropy'] = 0.0
        features['haralick_sum_entropy'] = 0.0
        features['haralick_sum_variance'] = 0.0
        features['haralick_diff_entropy'] = 0.0
        features['haralick_diff_variance'] = 0.0
        return features
    
    try:
        from skimage.feature import graycomatrix, graycoprops
        
        # Quantize to fewer levels for efficiency
        levels = 32
        gray_quantized = (gray_uint8 / 256 * levels).astype(np.uint8)
        gray_quantized = np.clip(gray_quantized, 0, levels - 1)
        
        # Compute GLCM
        glcm = graycomatrix(gray_quantized, distances=distances, angles=angles, 
                           levels=levels, symmetric=True, normed=True)
        
        # GLCM properties
        for prop in ['contrast', 'dissimilarity', 'homogeneity', 'energy', 'correlation', 'ASM']:
            try:
                values = graycoprops(glcm, prop)
                features[f'glcm_{prop}_mean'] = float(np.mean(values))
                features[f'glcm_{prop}_std'] = float(np.std(values))
            except Exception:
                features[f'glcm_{prop}_mean'] = 0.0
                features[f'glcm_{prop}_std'] = 0.0
        
        # Directional consistency (AI often more uniform across angles)
        for prop in ['contrast', 'homogeneity']:
            try:
                values = graycoprops(glcm, prop)
                angle_variation = np.std(values, axis=1)  # Variation across angles
                features[f'glcm_{prop}_angle_var'] = float(np.mean(angle_variation))
            except Exception:
                features[f'glcm_{prop}_angle_var'] = 0.0

        # Haralick-style extras from averaged GLCM
        try:
            # Average across distances/angles
            P = np.mean(glcm, axis=(2, 3))
            P = P / (P.sum() + 1e-10)
            # Entropy
            features['haralick_entropy'] = _entropy(P)

            # p_x+y and p_x-y distributions - vectorized
            levels_range = np.arange(levels)
            i_idx, j_idx = np.meshgrid(levels_range, levels_range, indexing='ij')
            
            # Vectorized p_sum using bincount
            sum_indices = (i_idx + j_idx).ravel()
            p_sum = np.bincount(sum_indices, weights=P.ravel(), minlength=2*levels-1)
            
            # Vectorized p_diff using bincount
            diff_indices = np.abs(i_idx - j_idx).ravel()
            p_diff = np.bincount(diff_indices, weights=P.ravel(), minlength=levels)

            p_sum = p_sum / (p_sum.sum() + 1e-10)
            p_diff = p_diff / (p_diff.sum() + 1e-10)

            sum_entropy = _entropy(p_sum)
            sum_avg = np.sum(np.arange(len(p_sum)) * p_sum)
            sum_var = np.sum((np.arange(len(p_sum)) - sum_avg) ** 2 * p_sum)

            diff_entropy = _entropy(p_diff)
            diff_var = float(np.var(p_diff))

            features['haralick_sum_entropy'] = float(sum_entropy)
            features['haralick_sum_variance'] = float(sum_var)
            features['haralick_diff_entropy'] = float(diff_entropy)
            features['haralick_diff_variance'] = float(diff_var)
        except Exception:
            features['haralick_entropy'] = 0.0
            features['haralick_sum_entropy'] = 0.0
            features['haralick_sum_variance'] = 0.0
            features['haralick_diff_entropy'] = 0.0
            features['haralick_diff_variance'] = 0.0
                
    except ImportError:
        # Simplified fallback
        features['glcm_contrast_mean'] = float(np.std(gray)**2)
        features['glcm_contrast_std'] = 0.0
        features['glcm_homogeneity_mean'] = 0.0
        features['glcm_homogeneity_std'] = 0.0
        features['glcm_energy_mean'] = 0.0
        features['glcm_energy_std'] = 0.0
        features['glcm_correlation_mean'] = 0.0
        features['glcm_correlation_std'] = 0.0
        features['glcm_dissimilarity_mean'] = 0.0
        features['glcm_dissimilarity_std'] = 0.0
        features['glcm_ASM_mean'] = 0.0
        features['glcm_ASM_std'] = 0.0
        features['glcm_contrast_angle_var'] = 0.0
        features['glcm_homogeneity_angle_var'] = 0.0
        features['haralick_entropy'] = 0.0
        features['haralick_sum_entropy'] = 0.0
        features['haralick_sum_variance'] = 0.0
        features['haralick_diff_entropy'] = 0.0
        features['haralick_diff_variance'] = 0.0
    
    return features


def compute_clbp_features(
    gray: np.ndarray,
    radius: int = 1,
    n_points: int = 8,
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Compute CLBP (Completed Local Binary Pattern) features.

    Approximates CLBP_S (sign), CLBP_M (magnitude) and CLBP_C (center).
    
    OPTIMIZED: Reuses precomputed LBP if available, uses cv2 blur for magnitude.

    Args:
        gray: Grayscale image
        radius: CLBP radius
        n_points: Number of points in CLBP
        precomputed: Optional ImagePrecomputedData instance
    """
    features = {
        'clbp_s_entropy': 0.0,
        'clbp_s_uniformity': 0.0,
        'clbp_m_entropy': 0.0,
        'clbp_m_uniformity': 0.0,
        'clbp_c_ratio': 0.0,
    }

    gray_uint8 = precomputed.gray_uint8

    try:
        from gpu_utils import fast_lbp_uniform

        # CLBP_S (sign) using standard uniform LBP
        # OPTIMIZATION: Reuse precomputed LBP if available
        if hasattr(precomputed, '_lbp_uniform') and precomputed._lbp_uniform is not None:
            lbp_s = precomputed._lbp_uniform
        else:
            lbp_s = fast_lbp_uniform(gray_uint8)

        # CLBP_M (magnitude) via local magnitude image
        # OPTIMIZATION: Use cv2.blur if available (faster)
        try:
            local_mean = cv2.blur(gray_uint8.astype(np.float32), (3, 3))
        except Exception:
            local_mean = precomputed.uniform(3)
        
        mag = np.abs(gray_uint8.astype(np.float32) - local_mean)
        mag_max = mag.max()
        if mag_max > 1e-10:
            mag_norm = (mag * (255.0 / mag_max)).astype(np.uint8)
        else:
            mag_norm = np.zeros_like(gray_uint8)
        lbp_m = fast_lbp_uniform(mag_norm)

        n_bins = n_points + 2
        # OPTIMIZATION: bincount is faster than histogram for integer LBP values (0..n_bins-1)
        hist_s = np.bincount(lbp_s.ravel(), minlength=n_bins)[:n_bins].astype(np.float64)
        hist_m = np.bincount(lbp_m.ravel(), minlength=n_bins)[:n_bins].astype(np.float64)
        
        hist_s_sum = hist_s.sum() + 1e-10
        hist_m_sum = hist_m.sum() + 1e-10
        hist_s = hist_s / hist_s_sum
        hist_m = hist_m / hist_m_sum

        features['clbp_s_entropy'] = _entropy(hist_s)
        features['clbp_s_uniformity'] = float(np.sum(hist_s ** 2))
        features['clbp_m_entropy'] = _entropy(hist_m)
        features['clbp_m_uniformity'] = float(np.sum(hist_m ** 2))

        # CLBP_C: center pixel comparison to global mean
        mean_val = np.mean(gray_uint8)
        features['clbp_c_ratio'] = float(np.mean(gray_uint8 > mean_val))

    except Exception:
        # Keep defaults
        pass

    return features


def compute_gabor_features(
    gray: np.ndarray,
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Compute lightweight Gabor response features.

    Uses a small bank of frequencies/orientations on a downsampled image.

    Args:
        gray: Grayscale image
        precomputed: Optional ImagePrecomputedData instance (currently unused but available for future optimization)
    """
    features = {
        'gabor_energy_mean': 0.0,
        'gabor_energy_std': 0.0,
    }

    try:
        # Downsample for speed (target ~128px max dim)
        h, w = gray.shape
        step = max(1, max(h, w) // 128)
        gray_small = gray[::step, ::step].astype(np.float32)
        if gray_small.max() > 1.0:
            gray_small = gray_small / 255.0

        energies = []

        if HAS_CV2:
            # Use OpenCV Gabor kernels (fast)
            ksize = 15
            sigmas = [3.0]
            lambdas = [6.0, 10.0]
            thetas = [0.0, np.pi / 4]
            gamma = 0.5
            psi = 0

            idx = 0
            for theta in thetas:
                for lambd in lambdas:
                    kernel = cv2.getGaborKernel((ksize, ksize), sigmas[0], theta, lambd, gamma, psi, ktype=cv2.CV_32F)
                    resp = cv2.filter2D(gray_small, cv2.CV_32F, kernel)
                    mag = np.abs(resp)
                    mean_val = float(np.mean(mag))
                    std_val = float(np.std(mag))
                    features[f'gabor_f{idx}_mean'] = mean_val
                    features[f'gabor_f{idx}_std'] = std_val
                    energies.append(mean_val)
                    idx += 1
        else:
            # Fallback to skimage
            from skimage.filters import gabor

            freqs = [0.1, 0.2]
            thetas = [0.0, np.pi / 4]

            idx = 0
            for freq in freqs:
                for theta in thetas:
                    real, imag = gabor(gray_small, frequency=freq, theta=theta)
                    mag = np.sqrt(real ** 2 + imag ** 2)
                    mean_val = float(np.mean(mag))
                    std_val = float(np.std(mag))
                    features[f'gabor_f{idx}_mean'] = mean_val
                    features[f'gabor_f{idx}_std'] = std_val
                    energies.append(mean_val)
                    idx += 1

        if energies:
            features['gabor_energy_mean'] = float(np.mean(energies))
            features['gabor_energy_std'] = float(np.std(energies))

    except Exception:
        # Keep defaults
        pass

    return features


def compute_edge_width_features(
    gray: np.ndarray,
    *,
    edges: np.ndarray = None,
    precomputed=None,
) -> Dict[str, float]:
    """
    Edge width distribution proxy using gradient magnitude thresholds.

    Args:
        gray: Grayscale image
        edges: Precomputed edge magnitude (optional)
        precomputed: Optional ImagePrecomputedData instance for shared computations
    """
    features = {
        'edge_width_ratio': 0.0,
        'edge_width_mean': 0.0,
        'edge_width_std': 0.0,
        'edge_width_p90': 0.0,
    }

    # Gradient magnitude
    if edges is not None:
        grad = edges
    else:
        grad = precomputed.edges

    flat = grad.ravel()
    if flat.size == 0:
        return features

    _n_f = len(flat)
    _i70, _i90 = int(0.70 * (_n_f - 1)), int(0.90 * (_n_f - 1))
    _part_f = np.partition(flat, [_i70, _i90])
    p70 = _part_f[_i70]
    p90 = _part_f[_i90]
    edge_low = flat > p70
    edge_high = flat > p90

    low_ratio = float(np.mean(edge_low))
    high_ratio = float(np.mean(edge_high))
    features['edge_width_ratio'] = float(low_ratio / (high_ratio + 1e-10))

    if np.any(edge_low):
        vals = flat[edge_low]
        features['edge_width_mean'] = float(np.mean(vals))
        features['edge_width_std'] = float(np.std(vals))
        _flat_v = vals.ravel()
        _idx90_v = int(0.90 * (len(_flat_v) - 1))
        features['edge_width_p90'] = float(np.partition(_flat_v, _idx90_v)[_idx90_v])

    return features


def compute_edge_color_features(
    img_array: np.ndarray,
    *,
    gray: np.ndarray = None,
    edges: np.ndarray = None,
    precomputed=None,
) -> Dict[str, float]:
    """
    Saturation and clipping near edges.

    Args:
        img_array: RGB image array
        gray: Precomputed grayscale image (optional)
        edges: Precomputed edge magnitude (optional)
        precomputed: Optional ImagePrecomputedData instance for shared computations
    """
    features = {
        'edge_high_sat_ratio': 0.0,
        'edge_low_sat_ratio': 0.0,
        'edge_clipped_high_ratio': 0.0,
        'edge_clipped_shadow_ratio': 0.0,
        'edge_sat_ratio': 0.0,
    }

    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return features

    if gray is None:
        gray = precomputed.gray

    # Edge mask
    if edges is not None:
        grad = edges
    else:
        grad = precomputed.edges
    _flat_gr = grad.ravel()
    _idx90_gr = int(0.90 * (len(_flat_gr) - 1))
    thresh = np.partition(_flat_gr, _idx90_gr)[_idx90_gr]
    edge_mask = grad > thresh
    if np.sum(edge_mask) < 10:
        return features

    # Dilate edges to include near-edge pixels
    edge_mask = gpu_binary_dilation(edge_mask, iterations=2)

    r = precomputed.r
    g = precomputed.g
    b = precomputed.b
    maxc = np.maximum(r, np.maximum(g, b))
    minc = np.minimum(r, np.minimum(g, b))
    if HAS_JIT:
        sat = fast_rgb_to_saturation(r, g, b)
    else:
        sat = (maxc - minc) / (maxc + 1e-10)

    sat_edge = sat[edge_mask]
    sat_all = sat.ravel()
    features['edge_high_sat_ratio'] = float(np.mean(sat_edge > 0.8))
    features['edge_low_sat_ratio'] = float(np.mean(sat_edge < 0.1))
    features['edge_sat_ratio'] = float(np.mean(sat_edge) / (np.mean(sat_all) + 1e-10))

    gray_edge = gray[edge_mask]
    features['edge_clipped_high_ratio'] = float(np.mean(gray_edge >= 254))
    features['edge_clipped_shadow_ratio'] = float(np.mean(gray_edge <= 1))

    return features


def compute_gradient_histogram_features(
    gray: np.ndarray,
    *,
    sobel_h: np.ndarray = None,
    sobel_v: np.ndarray = None,
    precomputed=None,
) -> Dict[str, float]:
    """
    Compute histogram of oriented gradients (HOG-like) features.

    Args:
        gray: Grayscale image
        sobel_h: Precomputed horizontal Sobel gradient (optional)
        sobel_v: Precomputed vertical Sobel gradient (optional)
        precomputed: Optional ImagePrecomputedData instance for shared computations
    """
    features = {}

    # Compute gradients
    # Note: sobel_h is axis=0 (Y gradient), sobel_v is axis=1 (X gradient)
    # So sobel_h -> gy, sobel_v -> gx
    if sobel_h is not None and sobel_v is not None:
        gy = sobel_h
        gx = sobel_v
    else:
        gy = precomputed.sobel_h
        gx = precomputed.sobel_v
    
    magnitude = np.sqrt(gx**2 + gy**2)
    orientation = np.arctan2(gy, gx)
    
    # Orientation histogram (weighted by magnitude) - vectorized with np.bincount
    n_bins = 18  # 20-degree bins
    
    # Map orientations from [-pi, pi] to bin indices [0, n_bins-1]
    bin_width = 2 * np.pi / n_bins
    bin_indices = ((orientation + np.pi) / bin_width).astype(int)
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)  # Handle edge cases
    
    # Use bincount with magnitude weights
    hist = np.bincount(bin_indices.ravel(), weights=magnitude.ravel(), minlength=n_bins).astype(np.float64)
    
    hist = hist / (hist.sum() + 1e-10)
    
    # Histogram statistics
    features['hog_entropy'] = _entropy(hist)
    features['hog_max'] = float(np.max(hist))
    features['hog_uniformity'] = float(np.sum(hist**2))
    
    # Symmetry in histogram (natural images often more symmetric)
    half = len(hist) // 2
    symmetry = _corrcoef(hist[:half], hist[half:])
    features['hog_symmetry'] = float(symmetry)
    
    return features


def compute_texture_regularity_features(
    gray: np.ndarray,
    block_size: int = 32,
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Analyze texture regularity across the image.

    AI images may have inconsistent texture patterns.

    Args:
        gray: Grayscale image
        block_size: Size of blocks for texture analysis
        precomputed: Optional ImagePrecomputedData instance (currently unused but available for future optimization)
    """
    features = {}
    
    h, w = gray.shape
    
    # Vectorized approach: use uniform_filter for fast block statistics
    # Local mean and variance via uniform filter
    from scipy.ndimage import uniform_filter
    
    if block_size == 5 and precomputed is not None:
        local_std = np.sqrt(precomputed.local_variance)
    elif HAS_JIT and block_size <= 9:
        local_std = fast_local_std_2d(gray.astype(np.float64), block_size)
    else:
        local_mean = cv2.blur(gray, (block_size, block_size), borderType=cv2.BORDER_REFLECT)
        local_sq_mean = cv2.blur(gray**2, (block_size, block_size), borderType=cv2.BORDER_REFLECT)
        local_var = local_sq_mean - local_mean**2
        local_std = np.sqrt(np.maximum(local_var, 0))
    
    # Sample at block centers (non-overlapping)
    step = block_size // 2
    rows = np.arange(step, h - step, step)
    cols = np.arange(step, w - step, step)
    
    block_contrasts = local_std[np.ix_(rows, cols)].ravel()
    
    # For entropy, sample a few blocks (limit for speed)
    max_blocks = 64
    # Sample blocks in vectorized manner
    sample_rows = rows[::max(1, len(rows) // 8)][:8]
    sample_cols = cols[::max(1, len(cols) // 8)][:8]
    
    # Fully vectorized block entropy computation
    if len(sample_rows) > 0 and len(sample_cols) > 0:
        # Create meshgrid of valid block centers
        row_grid, col_grid = np.meshgrid(sample_rows, sample_cols, indexing='ij')
        row_centers = row_grid.ravel()
        col_centers = col_grid.ravel()
        
        # Filter valid centers
        valid_mask = (row_centers < h - block_size // 2) & (col_centers < w - block_size // 2)
        row_centers = row_centers[valid_mask]
        col_centers = col_centers[valid_mask]
        n_blocks = len(row_centers)
        
        if n_blocks > 0:
            # Extract all blocks using advanced indexing
            half_bs = block_size // 2
            row_offsets = np.arange(-half_bs, half_bs)
            col_offsets = np.arange(-half_bs, half_bs)
            
            # Create index arrays: (n_blocks, block_size, block_size)
            r_idx = row_centers[:, None, None] + row_offsets[None, :, None]
            c_idx = col_centers[:, None, None] + col_offsets[None, None, :]
            
            # Extract all blocks at once
            all_blocks = gray[r_idx, c_idx]  # (n_blocks, block_size, block_size)
            
            # Quantize to 32 bins
            all_blocks_int = np.clip((all_blocks / 8).astype(np.int32), 0, 31)
            
            # Compute entropy for each block using vectorized bincount via apply_along_axis workaround
            block_entropies = np.zeros(n_blocks)
            for i in range(n_blocks):
                hist = np.bincount(all_blocks_int[i].ravel(), minlength=32).astype(np.float64)
                hist = hist / (hist.sum() + 1e-10)
                block_entropies[i] = _entropy(hist)
        else:
            block_entropies = np.array([0.0])
    else:
        block_entropies = np.array([0.0])
    
    # Block energies from local squared mean
    block_energies = local_sq_mean[np.ix_(rows, cols)].ravel() * block_size * block_size
    
    # Texture consistency metrics
    features['texture_contrast_mean'] = float(np.mean(block_contrasts))
    features['texture_contrast_std'] = float(np.std(block_contrasts))
    features['texture_contrast_cv'] = float(np.std(block_contrasts) / (np.mean(block_contrasts) + 1e-10))
    
    features['texture_entropy_mean'] = float(np.mean(block_entropies))
    features['texture_entropy_std'] = float(np.std(block_entropies))
    
    features['texture_energy_mean'] = float(np.mean(block_energies))
    features['texture_energy_std'] = float(np.std(block_energies))
    
    return features


def compute_fractal_dimension(
    gray: np.ndarray,
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Estimate fractal dimension using box-counting.

    Natural images often have different fractal properties than AI.

    Args:
        gray: Grayscale image
        precomputed: Optional ImagePrecomputedData instance (currently unused but available for future optimization)
    """
    features = {}
    
    # Binarize at multiple thresholds and compute box-counting dimension
    dimensions = []
    
    _gray_thresholds = precomputed.gray_percentiles([25, 50, 75])
    for threshold in _gray_thresholds:
        binary = gray > threshold
        
        # Box counting - vectorized
        sizes = [2, 4, 8, 16, 32, 64]
        counts = []
        h, w = binary.shape
        
        for size in sizes:
            # Reshape into blocks and check if any True values
            # Pad to make divisible by size
            pad_h = (size - h % size) % size
            pad_w = (size - w % size) % size
            padded = np.pad(binary, ((0, pad_h), (0, pad_w)), mode='constant', constant_values=False)
            
            # Reshape into blocks and count non-empty boxes
            new_h, new_w = padded.shape
            blocks = padded.reshape(new_h // size, size, new_w // size, size)
            count = np.sum(np.any(blocks, axis=(1, 3)))
            counts.append(count)
        
        # Fit log-log slope
        if len(counts) > 2 and all(c > 0 for c in counts):
            log_sizes = np.log(sizes)
            log_counts = np.log(counts)
            slope, _ = np.polyfit(log_sizes, log_counts, 1)
            dimensions.append(-slope)
    
    if dimensions:
        features['fractal_dim_mean'] = float(np.mean(dimensions))
        features['fractal_dim_std'] = float(np.std(dimensions))
    else:
        features['fractal_dim_mean'] = 0.0
        features['fractal_dim_std'] = 0.0
    
    return features


def extract_texture_features(
    img_array: np.ndarray,
    *,
    precomputed=None,
) -> Dict[str, float]:
    """
    Extract all texture-related features from an image.

    Args:
        img_array: RGB image as numpy array (H, W, 3), uint8
        precomputed: Optional ImagePrecomputedData instance for shared computations

    Returns:
        Dictionary of feature names to values
    """
    # Ensure uint8 input
    if img_array.dtype != np.uint8:
        if img_array.max() <= 1.0:
            img_array = (img_array * 255).astype(np.uint8)
        else:
            img_array = img_array.astype(np.uint8)

    gray = precomputed.gray

    features = {}

    features.update(compute_edge_features(
        gray,
        edges=precomputed.edges,
        sobel_h=precomputed.sobel_h,
        sobel_v=precomputed.sobel_v,
        precomputed=precomputed,
    ))

    # Canny edge features
    gray_u8 = precomputed.gray_uint8
    features.update(compute_canny_features(gray_u8, precomputed=precomputed))

    # LBP features
    features.update(compute_lbp_features(gray, precomputed=precomputed))

    # CLBP features
    features.update(compute_clbp_features(gray, precomputed=precomputed))

    # GLCM features
    features.update(compute_glcm_features(gray_u8, precomputed=precomputed))

    features.update(compute_gradient_histogram_features(
        gray,
        sobel_h=precomputed.sobel_h,
        sobel_v=precomputed.sobel_v,
        precomputed=precomputed,
    ))

    # Gabor features
    features.update(compute_gabor_features(gray, precomputed=precomputed))

    # Texture regularity features
    features.update(compute_texture_regularity_features(gray, precomputed=precomputed))

    # Fractal dimension
    features.update(compute_fractal_dimension(gray, precomputed=precomputed))

    features.update(compute_edge_width_features(gray, edges=precomputed.edges, precomputed=precomputed))

    features.update(compute_edge_color_features(
        img_array,
        gray=gray,
        edges=precomputed.edges,
        precomputed=precomputed,
    ))
    
    return features
