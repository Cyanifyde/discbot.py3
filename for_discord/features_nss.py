"""
features_nss.py - Natural Scene Statistics (NSS) Features for AI detection.

Implements model-agnostic statistics commonly used in NR-IQA (No-Reference 
Image Quality Assessment):
- MSCN (Mean Subtracted Contrast Normalized) coefficients
- Paired-product statistics (H/V/D1/D2) distribution fits
- Subband NSS using wavelets
- NIQE/BRISQUE-style intermediate parameters

Natural images follow specific statistical regularities that AI-generated
images often violate.
"""

import numpy as np
from scipy import ndimage
from scipy.special import gamma
from typing import Dict, Tuple, Optional, TYPE_CHECKING
import warnings

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Try to import JIT utilities for acceleration
try:
    from jit_utils import fast_skewness, fast_kurtosis
    HAS_JIT = True
except ImportError:
    HAS_JIT = False

from utils import fast_skew, fast_kurt


def estimate_ggd_params(data: np.ndarray) -> Tuple[float, float]:
    """
    Estimate parameters of Generalized Gaussian Distribution.
    """
    data = data.ravel()
    data = data[~np.isnan(data)]
    
    if len(data) < 10:
        return 1.0, 1.0
    
    sigma_sq = np.var(data)
    if sigma_sq < 1e-10:
        return 1.0, 0.0
    
    E_abs = np.mean(np.abs(data))
    rho = sigma_sq / (E_abs ** 2 + 1e-10)
    
    if rho < 0.3: alpha = 0.2
    elif rho > 2.0: alpha = 10.0
    else:
        alpha = 1.0 / (np.sqrt(rho) * 0.5 + 0.5)
        alpha = np.clip(alpha, 0.2, 10.0)
    
    sigma = np.sqrt(sigma_sq)
    return float(alpha), float(sigma)


def estimate_aggd_params(data: np.ndarray) -> Tuple[float, float, float]:
    """
    Estimate parameters of Asymmetric Generalized Gaussian Distribution.
    """
    data = data.ravel()
    data = data[~np.isnan(data)]
    if len(data) < 10: return 1.0, 1.0, 1.0
    
    left = data[data < 0]
    right = data[data >= 0]
    
    if len(left) < 5 or len(right) < 5:
        alpha, sigma = estimate_ggd_params(data)
        return alpha, sigma, sigma
    
    sigma_l = np.sqrt(np.mean(left ** 2)) if len(left) > 0 else 1.0
    sigma_r = np.sqrt(np.mean(right ** 2)) if len(right) > 0 else 1.0
    alpha, _ = estimate_ggd_params(data)
    
    return float(alpha), float(sigma_l), float(sigma_r)


def compute_mscn_coefficients(
    gray: np.ndarray,
    sigma: float = 7 / 6,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> np.ndarray:
    """
    Compute Mean Subtracted Contrast Normalized (MSCN) coefficients.
    GPU-accelerated when available for faster processing.
    """
    gray = gray.astype(np.float32, copy=False)
    # Keep exact semantics: mu = gaussian(gray), mu_sq = gaussian(gray**2)
    if precomputed is not None:
        mu = precomputed.gaussian(float(sigma))
        mu_sq = precomputed.gaussian_sq(float(sigma))
    else:
        import cv2
        ksize = int(2 * round(3 * sigma) + 1)
        mu = cv2.GaussianBlur(gray, (ksize, ksize), sigma)
        mu_sq = cv2.GaussianBlur(gray ** 2, (ksize, ksize), sigma)
    sigma_local = np.sqrt(np.maximum(mu_sq - mu ** 2, 0))
    C = np.float32(1.0)
    return (gray - mu) / (sigma_local + C)


def compute_paired_products(mscn: np.ndarray) -> Dict[str, np.ndarray]:
    """
    Compute paired products of MSCN coefficients in different directions.
    """
    return {
        'H': mscn[:, :-1] * mscn[:, 1:],
        'V': mscn[:-1, :] * mscn[1:, :],
        'D1': mscn[:-1, :-1] * mscn[1:, 1:],
        'D2': mscn[:-1, 1:] * mscn[1:, :-1]
    }


def compute_nss_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract Natural Scene Statistics features.
    """
    features = {}
    gray = precomputed.gray
    
    mscn = compute_mscn_coefficients(gray, precomputed=precomputed)
    mscn_flat = mscn.ravel()
    
    _m, _s = precomputed.mean_std(mscn_flat)
    features['nss_mscn_mean'] = _m
    features['nss_mscn_std'] = _s
    features['nss_mscn_skew'] = float(fast_skew(mscn_flat))
    features['nss_mscn_kurt'] = float(fast_kurt(mscn_flat))
    
    alpha, sigma = estimate_ggd_params(mscn_flat)
    features['nss_mscn_ggd_alpha'] = alpha
    features['nss_mscn_ggd_sigma'] = sigma
    
    products = compute_paired_products(mscn)
    for direction, prod in products.items():
        prod_flat = prod.ravel()
        _m, _s = precomputed.mean_std(prod_flat)
        features[f'nss_pair_{direction}_mean'] = _m
        features[f'nss_pair_{direction}_std'] = _s
        alpha_p, sigma_l, sigma_r = estimate_aggd_params(prod_flat)
        features[f'nss_pair_{direction}_aggd_alpha'] = alpha_p
        features[f'nss_pair_{direction}_aggd_sigma_l'] = sigma_l
        features[f'nss_pair_{direction}_aggd_sigma_r'] = sigma_r
        features[f'nss_pair_{direction}_asymmetry'] = float((sigma_r - sigma_l) / (sigma_r + sigma_l + 1e-10))
    
    return features


def compute_multiscale_nss_features(
    img_array: np.ndarray, 
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
    scales: int = 3
) -> Dict[str, float]:
    """
    Compute NSS features at multiple scales.
    """
    features = {}
    gray = precomputed.gray
    
    current = gray
    for scale in range(scales):
        prefix = f's{scale}_'
        if min(current.shape) < 32: break
        # Only scale=0 corresponds to the original image / precomputed caches.
        use_pre = precomputed if (scale == 0 and precomputed is not None) else None
        mscn = compute_mscn_coefficients(current, precomputed=use_pre)
        alpha, sigma = estimate_ggd_params(mscn.ravel())
        features[f'{prefix}nss_ggd_alpha'] = alpha
        features[f'{prefix}nss_ggd_sigma'] = sigma
        features[f'{prefix}nss_mscn_var'] = float(np.var(mscn))
        h_c, w_c = current.shape
        new_h, new_w = max(1, h_c // 2), max(1, w_c // 2)
        try:
            import cv2
            current = cv2.resize(current.astype(np.float32), (new_w, new_h), interpolation=cv2.INTER_AREA)
        except ImportError:
            current = ndimage.zoom(current, 0.5, order=1)
    
    if len(features) >= 4:
        alphas = [features.get(f's{i}_nss_ggd_alpha', 1.0) for i in range(scales)]
        features['nss_alpha_consistency'] = float(np.std(alphas))
    
    return features


def compute_wavelet_nss_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Compute NSS features in wavelet subbands.
    """
    features = {}
    try:
        import pywt
    except ImportError: return features
    
    gray = precomputed.gray
    
    max_level = min(3, pywt.dwt_max_level(min(gray.shape), 'db4'))
    try:
        # Use cached wavelet if available
        if hasattr(precomputed, 'wavelet_db4') and precomputed.wavelet_db4 is not None:
            coeffs = precomputed.wavelet_db4
        else:
            coeffs = pywt.wavedec2(gray, 'db4', level=max_level)
    except Exception: return features
    
    for level in range(1, len(coeffs)):
        cH, cV, cD = coeffs[level]
        for name, subband in [('H', cH), ('V', cV), ('D', cD)]:
            prefix = f'wv_l{level}_{name}_'
            flat = subband.ravel()
            alpha, sigma = estimate_ggd_params(flat)
            features[f'{prefix}ggd_alpha'] = alpha
            features[f'{prefix}ggd_sigma'] = sigma
            energy = np.sum(flat ** 2)
            features[f'{prefix}energy'] = float(energy)
            features[f'{prefix}entropy'] = float(-np.sum((flat ** 2 / (energy + 1e-10)) * np.log2((flat ** 2 / (energy + 1e-10)) + 1e-10)))
    
    return features


def extract_nss_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all NSS (Natural Scene Statistics) features.
    """
    features = {}
    try:
        features.update(compute_nss_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing NSS features: {e}")
    try:
        features.update(compute_multiscale_nss_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing multi-scale NSS: {e}")
    try:
        features.update(compute_wavelet_nss_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error computing wavelet NSS: {e}")
    return features
