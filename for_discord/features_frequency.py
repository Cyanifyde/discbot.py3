"""
features_frequency.py - Frequency domain / spectral features for AI detection.

Analyzes images in the frequency domain using FFT, DCT, and wavelets
to detect artifacts characteristic of AI-generated images.

OPTIMIZATION: Uses scipy.fft when available (pocketfft backend) which is
faster than numpy.fft for most array sizes. Falls back to numpy.fft.
"""

import numpy as np
import cv2
from scipy import fftpack, signal
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING

# GPU acceleration utilities - will use GPU if available, otherwise falls back to CPU
from gpu_utils import gpu_fft2, gpu_fftshift, gpu_maximum_filter, gpu_binary_dilation

# Define FFT wrappers that use GPU acceleration when available
def _fft2(arr):
    """2D FFT with optional GPU acceleration."""
    return gpu_fft2(arr)

def _fftshift(arr):
    """FFT shift with optional GPU acceleration."""
    return gpu_fftshift(arr)

try:
    from jit_utils import fast_entropy
    HAS_JIT = True
except ImportError:
    HAS_JIT = False


def _entropy(hist):
    """Fast histogram entropy in bits."""
    if HAS_JIT:
        return float(fast_entropy(hist))
    return float(-np.sum(hist * np.log2(hist + 1e-10)))

# Fast IFFT/ifftshift using scipy.fft with multi-threading
try:
    from scipy.fft import ifft2 as _sp_ifft2, ifftshift as _sp_ifftshift
    def _ifft2(arr):
        """Inverse 2D FFT with scipy.fft multi-threading."""
        return _sp_ifft2(arr, workers=-1)
    _ifftshift = _sp_ifftshift
except ImportError:
    def _ifft2(arr):
        """Inverse 2D FFT."""
        return np.fft.ifft2(arr)
    from numpy.fft import ifftshift as _ifftshift

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData


def compute_fft_features(
    gray: np.ndarray,
    *,
    magnitude: Optional[np.ndarray] = None,
    log_magnitude: Optional[np.ndarray] = None,
    power: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """
    Compute 2D FFT features.
    
    AI images often show:
    - Unusual frequency peaks (especially from upsampling)
    - Different high-frequency content distribution
    - Periodic patterns in frequency domain
    """
    features = {}
    
    # Compute 2D FFT (reuse precomputed arrays when provided)
    # Uses scipy.fft (pocketfft) when available for better performance
    if magnitude is None or log_magnitude is None:
        f_transform = _fft2(gray)
        f_shift = _fftshift(f_transform)
        if magnitude is None:
            magnitude = np.abs(f_shift)
        if log_magnitude is None:
            log_magnitude = np.log1p(magnitude)
    if power is None:
        power = magnitude ** 2
    
    # Get center coordinates
    h, w = gray.shape
    cy, cx = h // 2, w // 2
    
    # Radial profile analysis - vectorized with np.bincount
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - cx)**2 + (y - cy)**2).astype(int)
    max_r = min(cy, cx)
    
    # Compute radial average using bincount (vectorized)
    r_flat = r.ravel()
    log_mag_flat = log_magnitude.ravel()
    valid_mask = r_flat < max_r
    r_valid = r_flat[valid_mask]
    log_mag_valid = log_mag_flat[valid_mask]
    radial_sum = np.bincount(r_valid, weights=log_mag_valid, minlength=max_r)
    radial_count = np.bincount(r_valid, minlength=max_r)
    radial_profile = np.divide(radial_sum, radial_count, out=np.zeros(max_r), where=radial_count > 0)

    # Radial energy (power) profile for roll-off metrics
    power_valid = power.ravel()[valid_mask]
    radial_energy_sum = np.bincount(r_valid, weights=power_valid, minlength=max_r)
    radial_energy = np.divide(radial_energy_sum, radial_count, out=np.zeros(max_r), where=radial_count > 0)
    energy_cum = np.cumsum(radial_energy)
    total_energy = energy_cum[-1] if len(energy_cum) > 0 else 0.0
    
    # Radial profile statistics
    features['fft_radial_mean'] = float(np.mean(radial_profile))
    features['fft_radial_std'] = float(np.std(radial_profile))
    features['fft_radial_slope'] = float(np.polyfit(np.arange(len(radial_profile)), radial_profile, 1)[0])

    # High-frequency roll-off (spectral roll-off points)
    if total_energy > 0:
        rolloff80 = np.searchsorted(energy_cum, 0.8 * total_energy)
        rolloff95 = np.searchsorted(energy_cum, 0.95 * total_energy)
        features['fft_rolloff_80'] = float(rolloff80 / max(1, max_r))
        features['fft_rolloff_95'] = float(rolloff95 / max(1, max_r))
    else:
        features['fft_rolloff_80'] = 0.0
        features['fft_rolloff_95'] = 0.0
    
    # Energy distribution by frequency bands
    low_freq_mask = r <= max_r * 0.1
    mid_freq_mask = (r > max_r * 0.1) & (r <= max_r * 0.5)
    high_freq_mask = r > max_r * 0.5
    
    total_energy = np.sum(magnitude**2)
    features['fft_low_freq_ratio'] = float(np.sum(magnitude[low_freq_mask]**2) / (total_energy + 1e-10))
    features['fft_mid_freq_ratio'] = float(np.sum(magnitude[mid_freq_mask]**2) / (total_energy + 1e-10))
    features['fft_high_freq_ratio'] = float(np.sum(magnitude[high_freq_mask]**2) / (total_energy + 1e-10))
    
    # Detect periodic peaks (resampling artifacts)
    # Look for unusual spikes in the spectrum
    median_mag = np.median(magnitude)
    std_mag = np.std(magnitude)
    peak_threshold = median_mag + 5 * std_mag
    
    peaks = magnitude > peak_threshold
    features['fft_num_peaks'] = float(np.sum(peaks))
    features['fft_peak_ratio'] = float(np.sum(peaks) / magnitude.size)
    features['fft_peak_max_z'] = float((np.max(magnitude) - median_mag) / (std_mag + 1e-10))
    
    # Spectral centroid
    y_coords, x_coords = np.meshgrid(np.arange(h), np.arange(w), indexing='ij')
    total_mag = np.sum(magnitude)
    if total_mag > 0:
        centroid_y = np.sum(y_coords * magnitude) / total_mag
        centroid_x = np.sum(x_coords * magnitude) / total_mag
        features['fft_centroid_y'] = float(centroid_y / h)
        features['fft_centroid_x'] = float(centroid_x / w)
    else:
        features['fft_centroid_y'] = 0.5
        features['fft_centroid_x'] = 0.5
    
    # Spectral entropy
    mag_normalized = magnitude / (np.sum(magnitude) + 1e-10)
    spectral_entropy = _entropy(mag_normalized)
    features['fft_spectral_entropy'] = float(spectral_entropy)
    
    return features


def compute_dct_features(gray: np.ndarray, *, precomputed=None) -> Dict[str, float]:
    """
    Compute DCT (Discrete Cosine Transform) features.
    
    DCT is used in JPEG compression, so DCT analysis can reveal:
    - JPEG compression artifacts
    - Block-based processing patterns
    - AI upsampling artifacts
    """
    features = {}
    
    # 2D DCT
    dct = fftpack.dct(fftpack.dct(gray.T, norm='ortho').T, norm='ortho')
    dct_abs = np.abs(dct)
    
    # DCT coefficient statistics
    features['dct_mean'] = float(np.mean(dct_abs))
    features['dct_std'] = float(np.std(dct_abs))
    features['dct_max'] = float(np.max(dct_abs))
    
    # Energy concentration
    h, w = dct.shape
    
    # Low frequency region (top-left 1/8)
    low_region = dct_abs[:h//8, :w//8]
    features['dct_low_freq_energy'] = float(np.sum(low_region**2))
    
    # High frequency region
    high_region_mask = np.ones_like(dct_abs, dtype=bool)
    high_region_mask[:h//4, :w//4] = False
    features['dct_high_freq_energy'] = float(np.sum(dct_abs[high_region_mask]**2))
    
    # Energy ratio
    total_energy = np.sum(dct_abs**2)
    features['dct_low_high_ratio'] = float(features['dct_low_freq_energy'] / (features['dct_high_freq_energy'] + 1e-10))
    
    # Block-based DCT analysis (8x8 blocks like JPEG) - vectorized
    block_size = 8

    # Crop-aligned block analysis: codec block grids can be phase-shifted by cropping.
    block_gray = gray
    try:
        from blockgrid import estimate_block_grid_phase

        _bd = precomputed.boundary_diffs
        row_phase, col_phase = estimate_block_grid_phase(gray, block_size, boundary_diffs=_bd)
        if (row_phase or col_phase) and gray.shape[0] > row_phase and gray.shape[1] > col_phase:
            block_gray = gray[int(row_phase) :, int(col_phase) :]
    except Exception:
        pass

    bh, bw = block_gray.shape
    n_blocks_h = bh // block_size
    n_blocks_w = bw // block_size
    
    if n_blocks_h > 0 and n_blocks_w > 0:
        # Reshape into blocks
        cropped = block_gray[:n_blocks_h * block_size, :n_blocks_w * block_size]
        blocks = cropped.reshape(n_blocks_h, block_size, n_blocks_w, block_size)
        blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, block_size, block_size)
        
        # Batch 2D DCT using separable property
        dct_blocks = fftpack.dct(fftpack.dct(blocks, axis=2, norm='ortho'), axis=1, norm='ortho')
        
        # Compute energies and DC values
        block_energies = np.sum(dct_blocks**2, axis=(1, 2))
        block_dc_values = dct_blocks[:, 0, 0]
        
        features['dct_block_energy_mean'] = float(np.mean(block_energies))
        features['dct_block_energy_std'] = float(np.std(block_energies))
        features['dct_dc_mean'] = float(np.mean(block_dc_values))
        features['dct_dc_std'] = float(np.std(block_dc_values))
    else:
        features['dct_block_energy_mean'] = 0.0
        features['dct_block_energy_std'] = 0.0
        features['dct_dc_mean'] = 0.0
        features['dct_dc_std'] = 0.0
    
    return features


def compute_wavelet_features(gray: np.ndarray, *, precomputed=None) -> Dict[str, float]:
    """
    Compute wavelet transform features.
    
    Wavelets can capture multi-scale texture information
    that differs between AI and real images.
    """
    features = {}
    
    try:
        import pywt
        
        # Perform 2D DWT with Haar wavelet
        if hasattr(precomputed, 'wavelet_haar') and precomputed.wavelet_haar is not None:
            coeffs = precomputed.wavelet_haar
        else:
            coeffs = pywt.dwt2(gray, 'haar')
        cA, (cH, cV, cD) = coeffs
        
        # Approximation coefficients (low frequency)
        features['wavelet_approx_mean'] = float(np.mean(np.abs(cA)))
        features['wavelet_approx_std'] = float(np.std(cA))
        features['wavelet_approx_energy'] = float(np.sum(cA**2))
        
        # Horizontal detail (vertical edges)
        features['wavelet_horiz_mean'] = float(np.mean(np.abs(cH)))
        features['wavelet_horiz_std'] = float(np.std(cH))
        features['wavelet_horiz_energy'] = float(np.sum(cH**2))
        
        # Vertical detail (horizontal edges)
        features['wavelet_vert_mean'] = float(np.mean(np.abs(cV)))
        features['wavelet_vert_std'] = float(np.std(cV))
        features['wavelet_vert_energy'] = float(np.sum(cV**2))
        
        # Diagonal detail
        features['wavelet_diag_mean'] = float(np.mean(np.abs(cD)))
        features['wavelet_diag_std'] = float(np.std(cD))
        features['wavelet_diag_energy'] = float(np.sum(cD**2))
        
        # Energy ratios
        total_detail_energy = features['wavelet_horiz_energy'] + features['wavelet_vert_energy'] + features['wavelet_diag_energy']
        features['wavelet_detail_ratio'] = float(total_detail_energy / (features['wavelet_approx_energy'] + 1e-10))
        
        # Multi-level decomposition
        max_level = min(3, pywt.dwt_max_level(min(gray.shape), 'haar'))
        if hasattr(precomputed, 'wavelet_haar_multi') and precomputed.wavelet_haar_multi is not None:
            coeffs_multi = precomputed.wavelet_haar_multi
        else:
            coeffs_multi = pywt.wavedec2(gray, 'haar', level=max_level)
        
        level_energies = []
        for i, c in enumerate(coeffs_multi[1:]):  # Skip approximation
            level_energy = sum(np.sum(x**2) for x in c)
            level_energies.append(level_energy)
        
        if level_energies:
            features['wavelet_energy_decay'] = float(np.polyfit(np.arange(len(level_energies)), np.log1p(level_energies), 1)[0])
        
    except ImportError:
        # Fallback: simple Laplacian pyramid
        features['wavelet_approx_mean'] = 0.0
        features['wavelet_detail_ratio'] = 0.0
    
    return features


def compute_autocorrelation_features(
    gray: np.ndarray,
    *,
    power_spectrum: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """
    Compute autocorrelation features to detect periodic patterns.
    
    Resampling and AI upscaling can introduce periodic patterns
    visible in autocorrelation.
    """
    features = {}
    
    # Compute 2D autocorrelation via FFT (using scipy.fft when available)
    if power_spectrum is None:
        f = _fft2(gray - np.mean(gray))
        power_spectrum = np.abs(f) ** 2
    autocorr = _ifft2(power_spectrum).real
    autocorr = _fftshift(autocorr)
    
    # Normalize
    autocorr = autocorr / (autocorr.max() + 1e-10)
    
    h, w = autocorr.shape
    cy, cx = h // 2, w // 2
    
    # Central peak analysis (should be highest)
    central_region = autocorr[cy-5:cy+5, cx-5:cx+5]
    features['autocorr_central_mean'] = float(np.mean(central_region))
    
    # Look for secondary peaks (periodic patterns)
    # Exclude central region
    autocorr_no_center = autocorr.copy()
    autocorr_no_center[cy-10:cy+10, cx-10:cx+10] = 0
    
    # Find peaks
    local_max = gpu_maximum_filter(autocorr_no_center, size=5)
    peaks = (autocorr_no_center == local_max) & (autocorr_no_center > 0.1)
    
    features['autocorr_num_secondary_peaks'] = float(np.sum(peaks))
    features['autocorr_secondary_peak_max'] = float(np.max(autocorr_no_center))
    
    # Periodicity detection via peak spacing
    peak_coords = np.where(peaks)
    if len(peak_coords[0]) > 1:
        # Find distances to center
        distances = np.sqrt((peak_coords[0] - cy)**2 + (peak_coords[1] - cx)**2)
        features['autocorr_peak_distance_std'] = float(np.std(distances))
        features['autocorr_peak_distance_mean'] = float(np.mean(distances))
    else:
        features['autocorr_peak_distance_std'] = 0.0
        features['autocorr_peak_distance_mean'] = 0.0
    
    return features


def compute_power_spectrum_features(
    gray: np.ndarray,
    *,
    power: Optional[np.ndarray] = None,
    log_power: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """
    Analyze power spectrum for AI detection signatures.
    """
    features = {}
    
    # Compute power spectrum (reuse precomputed arrays when provided)
    if power is None or log_power is None:
        f = _fft2(gray)
        f_shift = _fftshift(f)
        if power is None:
            power = np.abs(f_shift) ** 2
        if log_power is None:
            log_power = np.log1p(power)
    
    h, w = power.shape
    cy, cx = h // 2, w // 2
    
    # Compute radial power spectrum using bincount (vectorized)
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - cx)**2 + (y - cy)**2).astype(np.int32)
    
    # Azimuthal average using bincount
    max_r = int(min(cy, cx) * 0.9)
    r_flat = r.ravel()
    log_power_flat = log_power.ravel()
    
    # Mask to valid radii
    valid_mask = r_flat < max_r
    r_valid = r_flat[valid_mask]
    power_valid = log_power_flat[valid_mask]
    
    # Sum and count per radius bin
    sum_per_bin = np.bincount(r_valid, weights=power_valid, minlength=max_r)
    count_per_bin = np.bincount(r_valid, minlength=max_r).astype(np.float64)
    count_per_bin[count_per_bin == 0] = 1  # Avoid division by zero
    radial_power = sum_per_bin / count_per_bin
    
    # Fit power law: P(f) ~ f^(-beta)
    # Log-log linear fit
    freqs = np.arange(1, max_r)
    if len(freqs) > 10:
        valid = radial_power[1:] > 0
        if np.sum(valid) > 10:
            log_freqs = np.log(freqs[valid])
            log_radial = np.log(radial_power[1:][valid] + 1e-10)
            slope, intercept = np.polyfit(log_freqs, log_radial, 1)
            features['power_spectrum_slope'] = float(slope)
            features['power_spectrum_intercept'] = float(intercept)
        else:
            features['power_spectrum_slope'] = 0.0
            features['power_spectrum_intercept'] = 0.0
    else:
        features['power_spectrum_slope'] = 0.0
        features['power_spectrum_intercept'] = 0.0
    
    # Anisotropy analysis (check if power is uniform across angles)
    # Vectorized: bin all pixels by angle using bincount instead of per-angle masks
    angles = np.arctan2(y - cy, x - cx)  # (h, w), range [-pi, pi]
    bin_idx = ((angles + np.pi) * (6.0 / np.pi)).astype(np.int32)
    np.clip(bin_idx, 0, 11, out=bin_idx)
    bin_flat = bin_idx.ravel()
    lp_flat = log_power.ravel()
    ang_sums = np.bincount(bin_flat, weights=lp_flat, minlength=12)
    ang_counts = np.bincount(bin_flat, minlength=12).astype(np.float64)
    valid_bins = ang_counts > 0
    if np.any(valid_bins):
        angular_power = ang_sums[valid_bins] / ang_counts[valid_bins]
        features['power_spectrum_anisotropy'] = float(np.std(angular_power) / (np.mean(angular_power) + 1e-10))
    else:
        features['power_spectrum_anisotropy'] = 0.0
    
    return features


def compute_frequency_grid_features(gray: np.ndarray) -> Dict[str, float]:
    """
    Detect subtle repeating frequency grids (e.g., tiled VAE decoding).
    """
    features = {
        'grid_peak_strength_x': 0.0,
        'grid_peak_strength_y': 0.0,
        'grid_peak_ratio': 0.0,
        'grid_peak_period_x': 0.0,
        'grid_peak_period_y': 0.0,
        'grid_peak_count_x': 0.0,
        'grid_peak_count_y': 0.0,
    }

    h, w = gray.shape
    # Downsample for speed (target ~256px max) using stride
    max_dim = 256
    if max(h, w) > max_dim:
        step = max(1, max(h, w) // max_dim)
        gray = gray[::step, ::step]
        h, w = gray.shape

    # FFT magnitude (using scipy.fft when available)
    f = _fft2(gray - np.mean(gray))
    mag = np.abs(_fftshift(f))

    # 1D spectra along axes
    spec_x = mag.mean(axis=0)
    spec_y = mag.mean(axis=1)

    # Remove DC center
    cx = w // 2
    cy = h // 2
    spec_x = spec_x.copy()
    spec_y = spec_y.copy()
    spec_x[cx] = 0
    spec_y[cy] = 0

    def _find_peak(spec: np.ndarray) -> Tuple[float, float, int]:
        if spec.size < 5:
            return 0.0, 0.0, 0
        mean = float(np.mean(spec))
        std = float(np.std(spec))
        threshold = mean + 2.0 * std
        peaks = np.where(spec > threshold)[0]
        peak_count = int(len(peaks))
        if peak_count == 0:
            return 0.0, 0.0, 0
        peak_idx = int(peaks[np.argmax(spec[peaks])])
        peak_strength = float(spec[peak_idx] / (mean + 1e-10))
        # Convert frequency index to spatial period (pixels)
        dist = abs(peak_idx - (spec.size // 2))
        period = float(spec.size / dist) if dist > 0 else 0.0
        return peak_strength, period, peak_count

    peak_x, period_x, count_x = _find_peak(spec_x)
    peak_y, period_y, count_y = _find_peak(spec_y)

    features['grid_peak_strength_x'] = peak_x
    features['grid_peak_strength_y'] = peak_y
    features['grid_peak_ratio'] = float((peak_x + peak_y) / 2.0)
    features['grid_peak_period_x'] = period_x
    features['grid_peak_period_y'] = period_y
    features['grid_peak_count_x'] = float(count_x)
    features['grid_peak_count_y'] = float(count_y)

    return features


def compute_ringing_artifact_features(
    gray: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect ringing artifacts from upsampling/filtering.
    
    AI upscaling often produces subtle ringing artifacts around edges.
    """
    features = {}
    
    # Detect edges
    if precomputed is not None:
        edge_h = precomputed.sobel_h  # Y derivative
        edge_v = precomputed.sobel_v  # X derivative
        edge_mag = precomputed.edges
    else:
        edge_h = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        edge_v = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        edge_mag = np.sqrt(edge_h**2 + edge_v**2)
    
    # Find strong edges
    _flat_em = edge_mag.ravel()
    _idx90 = int(0.90 * (len(_flat_em) - 1))
    edge_threshold = np.partition(_flat_em, _idx90)[_idx90]
    strong_edges = edge_mag > edge_threshold
    
    # Analyze intensity oscillations near edges
    # Dilate edge mask to get near-edge regions
    near_edge = gpu_binary_dilation(strong_edges, iterations=5) & ~strong_edges
    
    if np.sum(near_edge) > 0:
        # Compute local variance near edges
        near_edge_values = gray[near_edge]
        features['ringing_near_edge_std'] = float(np.std(near_edge_values))
        
        # Check for oscillation patterns
        # Use second derivative as proxy for ringing
        if precomputed is not None:
            laplacian = np.abs(precomputed.laplacian)
        else:
            laplacian = np.abs(cv2.Sobel(cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3), cv2.CV_64F, 0, 1, ksize=3) + cv2.Sobel(cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3), cv2.CV_64F, 1, 0, ksize=3))
        features['ringing_laplacian_near_edge'] = float(np.mean(laplacian[near_edge]))
    else:
        features['ringing_near_edge_std'] = 0.0
        features['ringing_laplacian_near_edge'] = 0.0
    
    return features


def extract_frequency_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all frequency-domain features from an image.

    Args:
        img_array: RGB image as numpy array (H, W, 3), uint8
        precomputed: Optional precomputed image data for sharing across modules

    Returns:
        Dictionary of feature names to values
    """
    # Use precomputed grayscale and FFT values
    gray = precomputed.gray
    magnitude = precomputed.fft_magnitude
    log_magnitude = precomputed.fft_log_magnitude
    power_shift = precomputed.fft_power

    features = {}

    # FFT features
    features.update(
        compute_fft_features(
            gray,
            magnitude=magnitude,
            log_magnitude=log_magnitude,
            power=power_shift,
        )
    )
    
    # DCT features
    features.update(compute_dct_features(gray, precomputed=precomputed))
    
    # Wavelet features
    features.update(compute_wavelet_features(gray, precomputed=precomputed))
    
    # Autocorrelation features (need unshifted power spectrum with zeroed DC)
    # Power spectrum commutes with shift: ifftshift(power_shift) == abs(fft)**2.
    power_unshifted = _ifftshift(power_shift)
    power_unshifted[0, 0] = 0.0
    features.update(compute_autocorrelation_features(gray, power_spectrum=power_unshifted))
    
    # Power spectrum features
    features.update(
        compute_power_spectrum_features(
            gray,
            power=power_shift,
            log_power=np.log1p(power_shift),
        )
    )

    # Frequency grid / tiled decoding features
    try:
        features.update(compute_frequency_grid_features(gray))
    except Exception:
        features.update({
            'grid_peak_strength_x': 0.0,
            'grid_peak_strength_y': 0.0,
            'grid_peak_ratio': 0.0,
            'grid_peak_period_x': 0.0,
            'grid_peak_period_y': 0.0,
            'grid_peak_count_x': 0.0,
            'grid_peak_count_y': 0.0,
        })
    
    # Ringing artifact features
    features.update(compute_ringing_artifact_features(gray, precomputed=precomputed))
    
    return features
