"""OpenCV image preprocessing pipeline for Kirana ledger photographs.

Provides grayscale conversion, CLAHE contrast enhancement, deskewing,
safe margin cropping, and dimension bounding to optimize Gemma 4 response time.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image

logger = logging.getLogger("kirana.preprocessor")

try:
    import cv2

    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False


@dataclass
class PreprocessingResult:
    """Holds original and processed images along with transformation metadata."""

    original_image: np.ndarray
    processed_image: np.ndarray
    skew_angle: float = 0.0
    deskewed: bool = False
    contrast_enhanced: bool = False
    cropped: bool = False
    crop_box: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)
    original_dimensions: Tuple[int, int] = (0, 0)  # (width, height)
    processed_dimensions: Tuple[int, int] = (0, 0)
    resized: bool = False
    preprocessing_time_ms: float = 0.0

    def to_pil_original(self) -> Image.Image:
        """Converts original BGR image to RGB PIL Image."""
        if len(self.original_image.shape) == 2:
            return Image.fromarray(self.original_image)
        # OpenCV uses BGR by default; convert to RGB for PIL/Streamlit
        rgb = cv2.cvtColor(self.original_image, cv2.COLOR_BGR2RGB) if HAS_OPENCV else self.original_image
        return Image.fromarray(rgb)

    def to_pil_processed(self) -> Image.Image:
        """Converts processed image to PIL Image."""
        if len(self.processed_image.shape) == 2:
            return Image.fromarray(self.processed_image)
        rgb = cv2.cvtColor(self.processed_image, cv2.COLOR_BGR2RGB) if HAS_OPENCV else self.processed_image
        return Image.fromarray(rgb)

    def to_processed_bytes(self, ext: str = ".jpg", quality: int = 88) -> bytes:
        """Encodes processed image to PNG/JPEG bytes with quality and compression optimization."""
        is_jpeg = ext.lower() in (".jpg", ".jpeg")
        if HAS_OPENCV:
            encode_params = (
                [cv2.IMWRITE_JPEG_QUALITY, max(1, min(100, quality))]
                if is_jpeg
                else [cv2.IMWRITE_PNG_COMPRESSION, 4]
            )
            success, buffer = cv2.imencode(ext, self.processed_image, encode_params)
            if success:
                return buffer.tobytes()
        # Fallback using PIL
        pil_img = self.to_pil_processed()
        buf = io.BytesIO()
        fmt = "JPEG" if is_jpeg else "PNG"
        save_kwargs = {"quality": quality, "optimize": True} if is_jpeg else {}
        pil_img.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue()


class ImagePreprocessor:
    """Performs adaptive preprocessing on handwritten kirana ledger photos."""

    def __init__(
        self,
        clahe_clip_limit: float = 2.0,
        clahe_tile_grid_size: Tuple[int, int] = (8, 8),
        max_skew_angle: float = 30.0,
        min_skew_angle: float = 0.5,
        margin_padding_px: int = 15,
        min_crop_area_ratio: float = 0.50,
        max_crop_area_ratio: float = 0.98,
        max_dimension: int = 1600,
    ) -> None:
        self.clahe_clip_limit = clahe_clip_limit
        self.clahe_tile_grid_size = clahe_tile_grid_size
        self.max_skew_angle = max_skew_angle
        self.min_skew_angle = min_skew_angle
        self.margin_padding_px = margin_padding_px
        self.min_crop_area_ratio = min_crop_area_ratio
        self.max_crop_area_ratio = max_crop_area_ratio
        self.max_dimension = max_dimension

    def load_image(self, image_input: Union[bytes, np.ndarray, Image.Image]) -> np.ndarray:
        """Loads various input types into a standardized BGR numpy ndarray."""
        if isinstance(image_input, bytes):
            nparr = np.frombuffer(image_input, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR) if HAS_OPENCV else np.array(Image.open(io.BytesIO(image_input)))
            if img is None:
                raise ValueError("Could not decode image from provided bytes.")
            return img

        if isinstance(image_input, Image.Image):
            rgb = np.array(image_input.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR) if HAS_OPENCV else rgb

        if isinstance(image_input, np.ndarray):
            return image_input.copy()

        raise TypeError(f"Unsupported image input type: {type(image_input)}")

    def convert_to_grayscale(self, image: np.ndarray) -> np.ndarray:
        """Converts image to single-channel 8-bit grayscale."""
        if not HAS_OPENCV:
            if len(image.shape) == 3:
                return np.dot(image[..., :3], [0.2989, 0.5870, 0.1140]).astype(np.uint8)
            return image

        if len(image.shape) == 2:
            return image
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def enhance_contrast(self, gray: np.ndarray) -> np.ndarray:
        """Enhances contrast using Contrast Limited Adaptive Histogram Equalization (CLAHE)."""
        if not HAS_OPENCV:
            # Fallback min-max normalization
            min_val, max_val = np.min(gray), np.max(gray)
            if max_val > min_val:
                return ((gray - min_val) / (max_val - min_val) * 255).astype(np.uint8)
            return gray

        clahe = cv2.createCLAHE(
            clipLimit=self.clahe_clip_limit,
            tileGridSize=self.clahe_tile_grid_size,
        )
        return clahe.apply(gray)

    def detect_skew_angle(self, gray: np.ndarray) -> float:
        """Calculates text skew angle in degrees using minAreaRect on binary text pixels."""
        if not HAS_OPENCV:
            return 0.0

        # Otsu inverse threshold to isolate ink/text
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        pts = np.column_stack(np.where(thresh > 0))

        if len(pts) < 100:
            return 0.0

        # minAreaRect takes (x, y) coordinates
        pts_xy = np.fliplr(pts)
        rect = cv2.minAreaRect(pts_xy)
        angle = rect[-1]

        # OpenCV minAreaRect returns angle in [-90, 0)
        if angle < -45:
            angle = -(90 + angle)
        elif angle > 45:
            angle = 90 - angle
        else:
            angle = -angle

        return float(angle)

    def deskew(self, image: np.ndarray, angle: float) -> np.ndarray:
        """Rotates image around its center to correct skew angle."""
        if not HAS_OPENCV or abs(angle) < self.min_skew_angle or abs(angle) > self.max_skew_angle:
            return image

        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        # Use white border padding for documents
        rotated = cv2.warpAffine(
            image,
            m,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        return rotated

    def crop_margins(
        self, gray: np.ndarray
    ) -> Tuple[np.ndarray, Optional[Tuple[int, int, int, int]], bool]:
        """Safely detects page boundaries and removes excess non-content margins."""
        if not HAS_OPENCV:
            return gray, None, False

        h, w = gray.shape[:2]
        total_area = w * h

        # Otsu thresholding to find main content bounding contour
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return gray, None, False

        # Find bounding box enclosing all major text/contour content
        x_min, y_min = w, h
        x_max, y_max = 0, 0
        found_content = False

        for c in contours:
            area = cv2.contourArea(c)
            if area > 100:  # Filter minor noise dots
                x, y, cw, ch = cv2.boundingRect(c)
                x_min = min(x_min, x)
                y_min = min(y_min, y)
                x_max = max(x_max, x + cw)
                y_max = max(y_max, y + ch)
                found_content = True

        if not found_content:
            return gray, None, False

        # Apply safety padding
        pad = self.margin_padding_px
        x_crop = max(0, x_min - pad)
        y_crop = max(0, y_min - pad)
        w_crop = min(w - x_crop, (x_max - x_min) + 2 * pad)
        h_crop = min(h - y_crop, (y_max - y_min) + 2 * pad)

        crop_area = w_crop * h_crop
        area_ratio = crop_area / total_area

        # Only crop if content is within safe boundary thresholds
        if self.min_crop_area_ratio <= area_ratio <= self.max_crop_area_ratio:
            cropped = gray[y_crop : y_crop + h_crop, x_crop : x_crop + w_crop]
            return cropped, (x_crop, y_crop, w_crop, h_crop), True

        return gray, None, False

    def resize_if_needed(self, image: np.ndarray, max_dim: int = 1600) -> Tuple[np.ndarray, bool]:
        """Resizes image if its max dimension exceeds max_dim, preserving aspect ratio and stroke clarity."""
        if image is None or max_dim <= 0:
            return image, False
        h, w = image.shape[:2]
        if max(h, w) <= max_dim:
            return image, False

        scale = max_dim / float(max(h, w))
        new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))

        if HAS_OPENCV:
            resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            pil_img = Image.fromarray(image)
            resized = np.array(pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS))

        return resized, True

    def preprocess(
        self,
        image_input: Union[bytes, np.ndarray, Image.Image],
        enable_deskew: bool = True,
        enable_crop: bool = True,
        enable_resize: bool = True,
    ) -> PreprocessingResult:
        """Executes full preprocessing pipeline: Grayscale -> CLAHE -> Deskew -> Crop -> Dimension Bound."""
        t_start = time.perf_counter()
        original = self.load_image(image_input)
        orig_h, orig_w = original.shape[:2]

        # 1. Grayscale
        gray = self.convert_to_grayscale(original)

        # 2. CLAHE Contrast Enhancement
        enhanced = self.enhance_contrast(gray)

        # 3. Deskewing
        skew_angle = 0.0
        deskewed = False
        current = enhanced

        if enable_deskew:
            angle = self.detect_skew_angle(enhanced)
            if self.min_skew_angle <= abs(angle) <= self.max_skew_angle:
                current = self.deskew(enhanced, angle)
                skew_angle = angle
                deskewed = True

        # 4. Safe Margin Cropping
        cropped = False
        crop_box = None
        if enable_crop:
            current, box, was_cropped = self.crop_margins(current)
            if was_cropped:
                cropped = True
                crop_box = box

        # 5. Dimension Bounding to optimize VLM token count & payload latency
        was_resized = False
        if enable_resize and self.max_dimension > 0:
            current, was_resized = self.resize_if_needed(current, max_dim=self.max_dimension)

        proc_h, proc_w = current.shape[:2]
        elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

        logger.info(
            "Preprocessed image in %.2fms: %dx%d -> %dx%d (deskewed=%s, cropped=%s, resized=%s)",
            elapsed_ms,
            orig_w,
            orig_h,
            proc_w,
            proc_h,
            deskewed,
            cropped,
            was_resized,
        )

        return PreprocessingResult(
            original_image=original,
            processed_image=current,
            skew_angle=skew_angle,
            deskewed=deskewed,
            contrast_enhanced=True,
            cropped=cropped,
            crop_box=crop_box,
            original_dimensions=(orig_w, orig_h),
            processed_dimensions=(proc_w, proc_h),
            resized=was_resized,
            preprocessing_time_ms=elapsed_ms,
        )


_GLOBAL_PREPROCESSOR: Optional[ImagePreprocessor] = None


def get_preprocessor(max_dimension: Optional[int] = None) -> ImagePreprocessor:
    """Returns singleton ImagePreprocessor instance with configured max_dimension."""
    global _GLOBAL_PREPROCESSOR
    if _GLOBAL_PREPROCESSOR is None:
        if max_dimension is None:
            try:
                from core.config import get_config
                cfg = get_config()
                max_dimension = getattr(cfg, "max_image_dimension", 1600)
            except Exception:
                max_dimension = 1600
        _GLOBAL_PREPROCESSOR = ImagePreprocessor(max_dimension=max_dimension)
    return _GLOBAL_PREPROCESSOR
