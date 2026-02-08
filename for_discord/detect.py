"""
detect.py - Simple AI Image Detection for Discord

Usage:
    python detect.py <image_path>
    python detect.py image.png
    python detect.py https://example.com/image.jpg

Output:
    Returns AI probability as a scalar value between 0.0 and 1.0
    - 0.0 = definitely real
    - 1.0 = definitely AI-generated
"""

import sys
import os
import joblib
import numpy as np
from pathlib import Path
from PIL import Image
import warnings

# Limit CPU threads to 1 for resource-constrained environments
# Note: These should ideally be set before any numpy/scipy imports (see main.py)
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore')

# Feature families used by this model
SELECTED_FAMILIES = [
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

MAX_DIMENSION = 512  # Model was trained with 512 max dimension


def load_model():
    """Load the trained model and patch it for single-threaded execution."""
    model_path = Path(__file__).parent / 'best.pkl'
    if not model_path.exists():
        print(f"ERROR: Model file not found: {model_path}", file=sys.stderr)
        sys.exit(1)
    
    model_bundle = joblib.load(model_path)
    
    # Patch model to use single-threaded execution
    # This prevents "can't start new thread" errors on resource-limited systems
    model = model_bundle['model']
    _patch_model_njobs(model)
    _patch_sklearn_compatibility(model)
    
    return model_bundle


def _patch_sklearn_compatibility(estimator):
    """
    Patch sklearn models for version compatibility issues.
    
    Handles missing attributes when models are loaded in different sklearn versions.
    """
    if estimator is None:
        return
    
    # Patch LogisticRegression for sklearn version compatibility
    if hasattr(estimator, '__class__') and estimator.__class__.__name__ == 'LogisticRegression':
        if not hasattr(estimator, 'multi_class'):
            # Default to 'auto' for sklearn 0.22+
            estimator.multi_class = 'auto'
        if not hasattr(estimator, 'l1_ratio'):
            estimator.l1_ratio = None
    
    # Handle meta-estimators
    if hasattr(estimator, 'estimator') and estimator.estimator is not None:
        _patch_sklearn_compatibility(estimator.estimator)
    
    if hasattr(estimator, 'base_estimator') and estimator.base_estimator is not None:
        _patch_sklearn_compatibility(estimator.base_estimator)
    
    # Handle ensemble estimators
    if hasattr(estimator, 'estimators_'):
        for est in estimator.estimators_:
            _patch_sklearn_compatibility(est)
    
    if hasattr(estimator, 'estimators'):
        for est in estimator.estimators:
            if isinstance(est, tuple):
                _patch_sklearn_compatibility(est[1])
            else:
                _patch_sklearn_compatibility(est)
    
    # Handle calibrated classifiers
    if hasattr(estimator, 'calibrated_classifiers_'):
        for cal_clf in estimator.calibrated_classifiers_:
            _patch_sklearn_compatibility(cal_clf)
            if hasattr(cal_clf, 'estimator'):
                _patch_sklearn_compatibility(cal_clf.estimator)


def _patch_model_njobs(estimator, n_jobs=1):
    """
    Recursively patch all n_jobs parameters in sklearn estimators to use single thread.
    
    This prevents thread creation failures on resource-constrained systems.
    """
    if estimator is None:
        return
    
    # Set n_jobs on the estimator itself if it has the attribute
    if hasattr(estimator, 'n_jobs'):
        estimator.n_jobs = n_jobs
    
    # Handle meta-estimators (CalibratedClassifierCV, VotingClassifier, etc.)
    if hasattr(estimator, 'estimator') and estimator.estimator is not None:
        _patch_model_njobs(estimator.estimator, n_jobs)
    
    if hasattr(estimator, 'base_estimator') and estimator.base_estimator is not None:
        _patch_model_njobs(estimator.base_estimator, n_jobs)
    
    # Handle ensemble estimators (list of estimators)
    if hasattr(estimator, 'estimators_'):
        for est in estimator.estimators_:
            _patch_model_njobs(est, n_jobs)
    
    if hasattr(estimator, 'estimators'):
        for est in estimator.estimators:
            if isinstance(est, tuple):
                # Named estimators like in VotingClassifier: (name, estimator)
                _patch_model_njobs(est[1], n_jobs)
            else:
                _patch_model_njobs(est, n_jobs)
    
    # Handle calibrated classifiers
    if hasattr(estimator, 'calibrated_classifiers_'):
        for cal_clf in estimator.calibrated_classifiers_:
            _patch_model_njobs(cal_clf, n_jobs)
            if hasattr(cal_clf, 'estimator'):
                _patch_model_njobs(cal_clf.estimator, n_jobs)


def load_image(image_path):
    """Load image from file path or URL."""
    if image_path.startswith('http://') or image_path.startswith('https://'):
        # Download from URL
        import urllib.request
        import io
        
        print(f"Downloading image from URL...", file=sys.stderr)
        with urllib.request.urlopen(image_path) as response:
            image_data = response.read()
        image = Image.open(io.BytesIO(image_data))
    else:
        # Load from file
        image_path = Path(image_path)
        if not image_path.exists():
            print(f"ERROR: Image file not found: {image_path}", file=sys.stderr)
            sys.exit(1)
        image = Image.open(image_path)
    
    # Convert to RGB
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    return image


def extract_features(image):
    """Extract features from image."""
    from features import FeatureExtractor
    
    extractor = FeatureExtractor(
        families=SELECTED_FAMILIES,
        resize_for_speed=True,
        max_dimension=MAX_DIMENSION,
        n_jobs=1,
        use_cache=False,
        use_gpu=False,
    )
    
    return extractor.extract(image)


def predict(image_path, verbose=False):
    """
    Predict if image is AI-generated.
    
    Args:
        image_path: Path to image file or URL
        verbose: If True, print detailed information
    
    Returns:
        float: AI probability (0.0 to 1.0)
    """
    # Load model
    if verbose:
        print("Loading model...", file=sys.stderr)
    model_bundle = load_model()
    model = model_bundle['model']
    scaler = model_bundle['scaler']
    feature_names = model_bundle['feature_names']
    
    # Load image
    if verbose:
        print(f"Loading image: {image_path}", file=sys.stderr)
    image = load_image(image_path)
    if verbose:
        print(f"  Size: {image.size}", file=sys.stderr)
    
    # Extract features
    if verbose:
        print("Extracting features...", file=sys.stderr)
    features = extract_features(image)
    
    # Build feature vector
    vec = np.zeros(len(feature_names), dtype=np.float32)
    for i, name in enumerate(feature_names):
        vec[i] = features.get(name, 0.0)
    
    # Clean NaN/inf
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    X = vec.reshape(1, -1)
    
    # Scale and predict
    X_sc = scaler.transform(X)
    proba = model.predict_proba(X_sc)[0]
    
    # Return AI probability (class 1)
    ai_probability = float(proba[1])
    
    return ai_probability


def predict_image(image, verbose=False):
    """
    Predict if image is AI-generated from a PIL Image object.
    
    Args:
        image: PIL Image object (already loaded)
        verbose: If True, print detailed information
    
    Returns:
        float: AI probability (0.0 to 1.0)
    """
    # Load model
    if verbose:
        print("Loading model...", file=sys.stderr)
    model_bundle = load_model()
    model = model_bundle['model']
    scaler = model_bundle['scaler']
    feature_names = model_bundle['feature_names']
    
    # Image is already loaded, just ensure RGB
    if verbose:
        print(f"Image size: {image.size}", file=sys.stderr)
    
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Extract features
    if verbose:
        print("Extracting features...", file=sys.stderr)
    features = extract_features(image)
    
    # Build feature vector
    vec = np.zeros(len(feature_names), dtype=np.float32)
    for i, name in enumerate(feature_names):
        vec[i] = features.get(name, 0.0)
    
    # Clean NaN/inf
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    X = vec.reshape(1, -1)
    
    # Scale and predict
    X_sc = scaler.transform(X)
    proba = model.predict_proba(X_sc)[0]
    
    # Return AI probability (class 1)
    ai_probability = float(proba[1])
    
    return ai_probability


def predict_image_with_progress(image, progress_callback=None):
    """
    Predict if image is AI-generated from a PIL Image object with progress updates.
    
    Args:
        image: PIL Image object (already loaded)
        progress_callback: Optional callback function(family_name, idx, total) for progress updates
    
    Returns:
        float: AI probability (0.0 to 1.0)
    """
    # Limit OpenCV threads to 1
    try:
        import cv2
        cv2.setNumThreads(1)
    except ImportError:
        pass
    
    from features import FeatureExtractor
    
    # Load model
    model_bundle = load_model()
    model = model_bundle['model']
    scaler = model_bundle['scaler']
    feature_names = model_bundle['feature_names']
    
    # Image is already loaded, just ensure RGB
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Convert to numpy array for feature extraction
    img_array = np.array(image)
    
    # Resize if needed
    h, w = img_array.shape[:2]
    if max(h, w) > MAX_DIMENSION:
        scale = MAX_DIMENSION / max(h, w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        image_resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        img_array = np.array(image_resized)
    
    # Extract features with progress updates
    from image_precomputed import ImagePrecomputedData
    precomputed = ImagePrecomputedData(img_array)
    
    features = {}
    total_families = len(SELECTED_FAMILIES)
    
    for idx, family_name in enumerate(SELECTED_FAMILIES):
        # Call progress callback if provided
        if progress_callback:
            try:
                progress_callback(family_name, idx, total_families)
            except Exception:
                pass
        
        # Extract features for this family
        try:
            if family_name == 'color':
                from features_color import extract_color_features
                features.update(extract_color_features(img_array, precomputed=precomputed))
            elif family_name == 'frequency':
                from features_frequency import extract_frequency_features
                features.update(extract_frequency_features(img_array, precomputed=precomputed))
            elif family_name == 'spectral_diffusion':
                from features_spectral_diffusion import extract_spectral_diffusion_features
                features.update(extract_spectral_diffusion_features(img_array, precomputed=precomputed))
            elif family_name == 'noise':
                from features_noise import extract_noise_features
                features.update(extract_noise_features(img_array, precomputed=precomputed))
            elif family_name == 'texture':
                from features_texture import extract_texture_features
                features.update(extract_texture_features(img_array, precomputed=precomputed))
            elif family_name == 'gradient':
                from features_gradient import extract_gradient_features
                features.update(extract_gradient_features(img_array, precomputed=precomputed))
            elif family_name == 'forensic':
                from features_forensic import extract_forensic_features
                features.update(extract_forensic_features(img_array, precomputed=precomputed))
            elif family_name == 'model_specific':
                from features_model_specific import extract_model_specific_features
                features.update(extract_model_specific_features(img_array, precomputed=precomputed))
            elif family_name == 'nss':
                from features_nss import extract_nss_features
                features.update(extract_nss_features(img_array, precomputed=precomputed))
            elif family_name == 'cfa':
                from features_cfa import extract_cfa_features
                features.update(extract_cfa_features(img_array, precomputed=precomputed))
            elif family_name == 'self_similarity':
                from features_self_similarity import extract_self_similarity_features
                features.update(extract_self_similarity_features(img_array, precomputed=precomputed))
            elif family_name == 'residual':
                from features_residual import extract_residual_features
                features.update(extract_residual_features(img_array, precomputed=precomputed))
        except Exception as e:
            print(f"Warning: Failed to extract {family_name} features: {e}", file=sys.stderr)
    
    # Build feature vector
    vec = np.zeros(len(feature_names), dtype=np.float32)
    for i, name in enumerate(feature_names):
        vec[i] = features.get(name, 0.0)
    
    # Clean NaN/inf
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    X = vec.reshape(1, -1)
    
    # Scale and predict
    X_sc = scaler.transform(X)
    proba = model.predict_proba(X_sc)[0]
    
    # Return AI probability (class 1)
    ai_probability = float(proba[1])
    
    return ai_probability


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python detect.py <image_path>", file=sys.stderr)
        print("       python detect.py image.png", file=sys.stderr)
        print("       python detect.py https://example.com/image.jpg", file=sys.stderr)
        sys.exit(1)
    
    image_path = sys.argv[1]
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    
    try:
        ai_prob = predict(image_path, verbose=verbose)
        
        if verbose:
            print(f"\nResult:", file=sys.stderr)
            print(f"  AI Probability: {ai_prob:.6f}", file=sys.stderr)
            print(f"  Real Probability: {1-ai_prob:.6f}", file=sys.stderr)
            
            if ai_prob > 0.9:
                verdict = "AI Generated (High Confidence)"
            elif ai_prob > 0.7:
                verdict = "Likely AI"
            elif ai_prob < 0.3:
                verdict = "Real Image (High Confidence)"
            elif ai_prob < 0.5:
                verdict = "Likely Real"
            else:
                verdict = "Uncertain"
            
            print(f"  Verdict: {verdict}", file=sys.stderr)
            print()
        
        # Output just the number for easy parsing
        print(f"{ai_prob:.6f}")
        
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
